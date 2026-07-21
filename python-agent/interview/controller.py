from __future__ import annotations

import asyncio
import logging
import random
import re
import uuid
from enum import Enum
from time import perf_counter

from interview import prompts as prompt_defaults
from interview.interview_manager import InterviewManager
from interview.models import (
    Exchange,
    FollowUpDecision,
    InterviewReport,
    InterviewState,
    QuestionSpec,
    TranscriptTurn,
)
from interview.follow_up_decider import FollowUpDecider
from interview.follow_up_planner import FollowUpPlanner
from interview.report_generator import ReportGenerationError


logger = logging.getLogger(__name__)


class PlaybackEvent(str, Enum):
    PROMPT_FINISHED = "prompt_finished"


class PlaybackSignalMapper:
    # The platform reports the plain busy states (THINKING/STAGING/SPEAKING) in this
    # mode, not the PROMPT_* variants. Treat both as "avatar busy" so a prompt's
    # trailing IDLE is recognized as playback-finished — otherwise the mapper never
    # sees an active state, PROMPT_FINISHED never fires, and every prompt stalls until
    # the 30s watchdog (the main source of interview lag).
    ACTIVE_STATES = {
        "THINKING",
        "STAGING",
        "SPEAKING",
        "PROMPT_THINKING",
        "PROMPT_STAGING",
        "PROMPT_SPEAKING",
    }

    def __init__(self) -> None:
        self._awaiting_prompt = False
        self._active_seen = False
        self._prompt_sent_at = 0.0

    def mark_prompt_sent(self) -> None:
        self._awaiting_prompt = True
        self._active_seen = False
        self._prompt_sent_at = perf_counter()

    @property
    def has_pending_prompt(self) -> bool:
        return self._awaiting_prompt

    def force_finish_prompt(self) -> PlaybackEvent | None:
        if not self._awaiting_prompt:
            return None
        self._awaiting_prompt = False
        self._active_seen = False
        return PlaybackEvent.PROMPT_FINISHED

    def map_state(self, state) -> PlaybackEvent | None:
        value = getattr(state, "value", state)
        if not self._awaiting_prompt:
            return None
        if value in self.ACTIVE_STATES:
            self._active_seen = True
            return None
        # The floor opening for the candidate (LISTENING) means the avatar's prompt is
        # done — a reliable finish signal even when platform TTS is off and no SPEAKING
        # state is ever emitted. Without it the prompt stalls until the 30s watchdog.
        if value == "LISTENING":
            self._awaiting_prompt = False
            self._active_seen = False
            return PlaybackEvent.PROMPT_FINISHED
        if value != "IDLE":
            return None
        # Some platform sessions omit PROMPT_SPEAKING and only report a trailing
        # IDLE. Ignore an immediate stale IDLE from the previous turn, but accept a
        # later one as the real playback-finished signal.
        if not self._active_seen and perf_counter() - self._prompt_sent_at < 0.4:
            return None
        self._awaiting_prompt = False
        self._active_seen = False
        return PlaybackEvent.PROMPT_FINISHED


class NonRepeatingPhraseCycle:
    """Shuffle phrases by round without repeating before the round is exhausted."""

    def __init__(self, phrases) -> None:
        self._phrases = tuple(str(item) for item in phrases if str(item).strip())
        self._remaining: list[str] = []
        self._last = ""

    def next(self) -> str:
        if not self._phrases:
            return ""
        if not self._remaining:
            self._remaining = list(self._phrases)
            random.shuffle(self._remaining)
            if (
                len(self._remaining) > 1
                and self._last
                and self._remaining[0] == self._last
            ):
                self._remaining[0], self._remaining[1] = (
                    self._remaining[1],
                    self._remaining[0],
                )
        value = self._remaining.pop(0)
        self._last = value
        return value


class InterviewController:
    _ANSWER_ACKNOWLEDGEMENTS = (
        "好，我听明白了，我先顺一下你刚才讲的。",
        "嗯，这个点我记下了，我稍微接着往下看。",
        "好的，你刚才这段信息挺关键的，我先整理一下。",
        "明白，我先把你这段回答放到上下文里看一下。",
    )
    _FINAL_ANSWER_ACKNOWLEDGEMENTS = (
        "好，这一题我先记下，等下我会把整体情况一起看一下。",
        "好的，这部分信息够了，我接下来会整体看一下你的回答。",
        "明白，这一段我记下了，我先把今天聊到的内容合在一起看一下。",
    )
    _FOLLOW_UP_PREFIXES = (
        "我想顺着这里多问一句。",
        "这里我想再确认一个细节。",
        "这个点我们稍微展开一下。",
        "我追一下刚才你提到的部分。",
    )

    def __init__(
        self,
        *,
        agent,
        manager: InterviewManager,
        planner,
        evaluator,
        report_generator,
        closing_comment_generator=None,
        follow_up_decider=None,
        follow_up_planner=None,
        session_store=None,
        interview_id: str | None = None,
        thinking_checks: list[tuple[float, str]] | None = None,
        hard_timeout_seconds: float = 75,
        opening_to_question_delay_seconds: float = 0.8,
        prompt_playback_timeout_seconds: float = 30,
        candidate_speech_grace_seconds: float = 8,
        evaluation_join_timeout_seconds: float = 5,
        foreground_evaluation_timeout_seconds: float = 5.0,
        max_skipped_questions: int = 3,
        max_consecutive_skipped_questions: int = 2,
        speech_config=None,
        on_terminal=None,
    ) -> None:
        self._agent = agent
        self._manager = manager
        self._planner = planner
        self._evaluator = evaluator
        self._report_generator = report_generator
        self._closing_comment_generator = closing_comment_generator
        self._on_terminal = on_terminal
        self._follow_up_decider = follow_up_decider or FollowUpDecider()
        self._follow_up_planner = follow_up_planner or FollowUpPlanner()
        self._session_store = session_store
        self._interview_id = interview_id or f"iv_{uuid.uuid4().hex[:8]}"
        self._state = InterviewState.IDLE
        self._tts_idle = None
        self._exchange_seq = 0
        self._prompt_seq = 0
        self._turn_seq = 0
        self._asked_question_ids: set[str] = set()
        self._completed_question_ids: set[str] = set()
        self._current_question: QuestionSpec | None = None
        self._current_exchange: Exchange | None = None
        self._exchanges: list[Exchange] = []
        self._transcript: list[TranscriptTurn] = []
        self._report: InterviewReport | None = None
        self._report_generation: dict = {
            "state": "idle",
            "stage": "idle",
            "message": "",
            "completedSteps": 0,
            "totalSteps": 0,
            "percent": 0,
            "attempt": 0,
            "maxAttempts": 0,
            "error": "",
            "generationSource": "",
        }
        self._report_finish_mode = ""
        self._report_finish_reason: str | None = None
        self._report_generation_id = ""
        self._report_task: asyncio.Task | None = None
        self._scene_ready = False
        self._skipped_questions = 0
        self._consecutive_skipped_questions = 0
        self._effective_answers = 0
        self._termination_reason: str | None = None
        self._max_skipped_questions = max_skipped_questions
        self._max_consecutive_skipped_questions = max_consecutive_skipped_questions
        # Spoken phrases: overridable via speech_config (SpeechConfig), else defaults.
        self._answer_acknowledgements = tuple(
            getattr(speech_config, "answer_acknowledgements", None)
            or self._ANSWER_ACKNOWLEDGEMENTS
        )
        self._final_answer_acknowledgements = tuple(
            getattr(speech_config, "final_answer_acknowledgements", None)
            or self._FINAL_ANSWER_ACKNOWLEDGEMENTS
        )
        self._follow_up_prefixes = tuple(
            getattr(speech_config, "follow_up_prefixes", None)
            or self._FOLLOW_UP_PREFIXES
        )
        self._follow_up_prefix_cycle = NonRepeatingPhraseCycle(
            self._follow_up_prefixes
        )
        self._first_question_transition = (
            getattr(speech_config, "first_question_transition", "")
            or "我们先从第一个问题开始。"
        )
        configured_next_transitions = tuple(
            getattr(speech_config, "next_question_transitions", None) or ()
        )
        if not configured_next_transitions:
            legacy_next_transition = getattr(
                speech_config, "next_question_transition", ""
            )
            configured_next_transitions = (
                (legacy_next_transition,)
                if legacy_next_transition
                else tuple(prompt_defaults.DEFAULT_NEXT_QUESTION_TRANSITIONS)
            )
        self._next_question_transitions = configured_next_transitions
        self._next_question_transition_cycle = NonRepeatingPhraseCycle(
            self._next_question_transitions
        )
        self._skip_transition_text = (
            getattr(speech_config, "skip_transition", "") or "没关系，这个问题我们先跳过。"
        )
        self._closing_text = (
            getattr(speech_config, "closing", "")
            or "今天的模拟面试就到这里，稍后你可以查看完整反馈报告。"
        )
        self._termination_text = (
            getattr(speech_config, "termination", "")
            or "由于这次面试中没有收到足够的有效回答，本次面试将提前结束。"
        )
        self._pending_question_after_idle: QuestionSpec | None = None
        self._pending_question_prompt_after_idle: dict | None = None
        self._pending_terminal_state_after_idle: InterviewState | None = None
        # 真实面试开始时间（mark_scene_ready 触发后），用于报告里计算「面了几分钟」
        self._started_at: float | None = None
        # 答题/提问的累计时间（秒）：所有"问题被问到"到"答题完成或被跳过"之间的真实时长之和
        # 用 perf_counter 单调计时，不受系统时间跳变影响
        self._accumulated_interview_seconds: float = 0.0
        self._current_question_started_at: float | None = None
        self._thinking_checks = thinking_checks or [
            (20, "我看到你还在思考。你可以先从一个具体经历或关键决策讲起。"),
            (45, "这个问题可以再给你一点时间。如果暂时没有思路，也可以简单说明。"),
        ]
        self._hard_timeout_seconds = hard_timeout_seconds
        self._opening_to_question_delay_seconds = opening_to_question_delay_seconds
        self._prompt_playback_timeout_seconds = prompt_playback_timeout_seconds
        self._candidate_speech_grace_seconds = candidate_speech_grace_seconds
        self._evaluation_join_timeout_seconds = evaluation_join_timeout_seconds
        self._foreground_evaluation_timeout_seconds = min(
            6.0, max(0.05, foreground_evaluation_timeout_seconds)
        )
        self._timeout_task: asyncio.Task | None = None
        self._speech_guard_task: asyncio.Task | None = None
        self._prompt_playback_watchdog_task: asyncio.Task | None = None
        self._pending_evaluation_tasks: set[asyncio.Task] = set()
        self._idle_waiters: list[asyncio.Future] = []
        self._playback_mapper = PlaybackSignalMapper()

    @property
    def state(self) -> InterviewState:
        return self._state

    async def start(self) -> None:
        if self._state != InterviewState.IDLE:
            return
        self._state = InterviewState.WAITING_SCENE_READY
        self._persist_status()

    async def mark_scene_ready(self) -> None:
        if self._scene_ready:
            return
        if self._state != InterviewState.WAITING_SCENE_READY:
            return
        self._scene_ready = True
        # 真实面试开始计时——从这里起，停止/终止/完成时算出的就是"面了几分钟"
        self._started_at = perf_counter()
        self._current_question_started_at = self._started_at
        self._state = InterviewState.OPENING
        opening_text = self._manager.build_opening_text()
        opening_metadata = {
            "interviewId": self._interview_id,
            "promptId": self._next_prompt_id(),
            "promptType": "opening",
        }
        self._mark_prompt_sent()
        await self._agent.send_prompt(opening_text, metadata=opening_metadata)
        self._start_prompt_playback_watchdog(opening_text)
        self._record_turn(
            role="interviewer",
            type="opening",
            text=opening_text,
            metadata=opening_metadata,
        )
        self._pending_question_after_idle = self._planner.next_question(set())
        if self._pending_question_after_idle is None:
            await self._complete()
            return

    async def stop(self) -> None:
        task = self.begin_stop_report()
        if task is not None:
            await task

    def begin_stop_report(self) -> asyncio.Task | None:
        self._cancel_timeout_task()
        self._cancel_speech_guard_task()
        self._cancel_prompt_playback_watchdog()
        if self._state in {
            InterviewState.IDLE,
            InterviewState.REPORT_ERROR,
            InterviewState.COMPLETED,
            InterviewState.TERMINATED,
        }:
            return self._report_task
        self._termination_reason = "user_stopped"
        # 把"当前问题还没结算"的尾段时间也累计进总时长（如果用户中途停止）
        self._flush_current_question_duration()
        return self._start_report_task("stopped", self._termination_reason)

    def _flush_current_question_duration(self) -> None:
        """把正在进行的这一道题的"已耗时"累加到累计时长里，避免最后一题被漏算。"""
        if self._current_question_started_at is None:
            return
        delta = perf_counter() - self._current_question_started_at
        if delta > 0:
            self._accumulated_interview_seconds += delta
        self._current_question_started_at = None

    def _actual_duration_seconds(self) -> float:
        """报告里展示的面了几分钟。优先用累计的单调计时（_started_at 起算），
        退而用 start_time-now 的近似。"""
        if self._started_at is None:
            return 0.0
        # 把"已 flush 累计 + 当前正在进行的这道题还没 flush"两部分加起来
        running = 0.0
        if self._current_question_started_at is not None:
            running = max(0.0, perf_counter() - self._current_question_started_at)
        return self._accumulated_interview_seconds + running

    async def notify_platform_state(self, state) -> None:
        event = self._playback_mapper.map_state(state)
        if event == PlaybackEvent.PROMPT_FINISHED:
            self._cancel_prompt_playback_watchdog()
            await self.notify_platform_idle()

    async def notify_platform_idle(self) -> None:
        self._resolve_idle_waiters()
        if (
            self._state == InterviewState.OPENING
            and self._pending_question_after_idle is not None
        ):
            question = self._pending_question_after_idle
            self._pending_question_after_idle = None
            if self._opening_to_question_delay_seconds > 0:
                await asyncio.sleep(self._opening_to_question_delay_seconds)
            await self._ask_question(question, prompt_type="main_question")
            return
        if self._state == InterviewState.ASKING and self._current_exchange is not None:
            self._state = InterviewState.LISTENING
            self._start_timeout_task(self._current_exchange.exchange_id)
            self._persist_status()
            return
        if (
            self._state
            in {
                InterviewState.SKIPPING_QUESTION,
                InterviewState.TRANSITIONING,
                InterviewState.PLANNING_FOLLOWUP,
            }
            and self._pending_question_prompt_after_idle is not None
        ):
            prompt = self._pending_question_prompt_after_idle
            self._pending_question_prompt_after_idle = None
            await self._ask_question(**prompt)
            return
        if (
            self._state == InterviewState.CLOSING
            and self._pending_terminal_state_after_idle is not None
        ):
            self._state = self._pending_terminal_state_after_idle
            self._pending_terminal_state_after_idle = None
            self._persist_status()
            await self._notify_terminal()
            return
        logger.info(
            "interview recorded idle for later: interview_id=%s state=%s",
            self._interview_id,
            self._state.value,
        )

    async def open_floor_if_asking(self) -> None:
        """Candidate started speaking before the platform opened the floor — common
        when platform TTS is off (avatar silent) so they answer the instant the
        question appears. Finish the prompt now so the answer is captured, not dropped.
        """
        if self._state != InterviewState.ASKING:
            return
        if self._playback_mapper.force_finish_prompt() is not None:
            await self.notify_platform_idle()

    async def mark_candidate_speaking(self) -> None:
        if self._state not in {InterviewState.LISTENING, InterviewState.THINKING_CHECK}:
            return
        self._cancel_timeout_task()
        self._start_speech_guard_task()

    async def mark_candidate_speech_stopped(self) -> None:
        self._cancel_speech_guard_task()
        self._resume_answer_timeout_if_needed()

    def current_answer_metadata(self) -> dict:
        if self._current_exchange is None:
            return {"interviewId": self._interview_id}
        metadata = self._metadata_for(self._current_exchange)
        metadata["answerId"] = self._current_exchange.exchange_id.replace("ex_", "ans_", 1)
        return metadata

    async def handle_answer(
        self,
        answer_request_id: str,
        text: str,
        *,
        expected_exchange_id: str | None = None,
    ) -> None:
        if (
            self._state not in {InterviewState.LISTENING, InterviewState.THINKING_CHECK}
            or self._current_exchange is None
            or (
                expected_exchange_id is not None
                and expected_exchange_id != self._current_exchange.exchange_id
            )
        ):
            return
        if self._is_candidate_skip_intent(text):
            await self._handle_candidate_requested_skip(text)
            return
        answer_started_at = perf_counter()
        self._cancel_timeout_task()
        self._cancel_speech_guard_task()
        exchange = self._current_exchange
        exchange.answer_request_id = answer_request_id
        exchange.answer_text = text
        self._effective_answers += 1
        self._consecutive_skipped_questions = 0
        self._record_turn(
            role="candidate",
            type="answer",
            text=text,
            question_id=exchange.question_id,
            exchange_id=exchange.exchange_id,
            metadata={"answerRequestId": answer_request_id},
        )
        self._state = InterviewState.ANALYZING
        self._persist_status()
        evaluation_task = self._start_evaluation_task(exchange, text)
        evaluation_ready = await self._wait_for_foreground_evaluation(evaluation_task)
        self._state = InterviewState.DECIDING_FOLLOWUP
        follow_up_decision = None
        follow_up = None
        follow_up_decision_ms = 0
        follow_up_planning_ms = 0
        if (
            self._current_question is not None
            and evaluation_ready
            and exchange.evaluation is not None
        ):
            follow_up_decision_started_at = perf_counter()
            follow_up_decision = self._specific_ai_follow_up(
                question=self._current_question,
                evaluation=exchange.evaluation,
                probe_index=exchange.probe_index,
                answer_text=text,
            )
            follow_up_decision_ms = round(
                (perf_counter() - follow_up_decision_started_at) * 1000
            )
            if follow_up_decision.needed:
                follow_up = follow_up_decision.suggested_question
        self._log_answer_timing(
            exchange=exchange,
            follow_up_needed=bool(follow_up_decision and follow_up_decision.needed),
            acknowledgement_ms=0,
            evaluation_ms=(
                round((perf_counter() - answer_started_at) * 1000)
                if evaluation_ready
                else f">{round(self._foreground_evaluation_timeout_seconds * 1000)}"
            ),
            follow_up_decision_ms=follow_up_decision_ms,
            follow_up_planning_ms=follow_up_planning_ms,
            total_ms=round((perf_counter() - answer_started_at) * 1000),
        )
        if (
            follow_up
            and self._current_question is not None
            and exchange.probe_index < self._max_followups_for(self._current_question)
        ):
            self._state = InterviewState.PLANNING_FOLLOWUP
            await self._ask_question(
                self._current_question,
                prompt_text=follow_up,
                prompt_type="follow_up",
                parent_exchange_id=exchange.exchange_id,
                probe_index=exchange.probe_index + 1,
            )
            return

        if self._current_question is not None:
            self._completed_question_ids.add(self._current_question.question_id)
        next_question = self._planner.next_question(self._completed_question_ids)
        if next_question is None:
            await self._complete()
            return
        self._state = InterviewState.TRANSITIONING
        await self._ask_question(next_question, prompt_type="main_question")

    @staticmethod
    def _is_candidate_skip_intent(text: str) -> bool:
        normalized = re.sub(r"[\s，。！？、,.!?;；]+", "", str(text or "")).lower()
        if not normalized or len(normalized) > 36:
            return False
        patterns = (
            r"^(下一题|下一个问题|换一题|换个问题|跳过|pass)(吧|了|可以吗)?$",
            r"^(这个|这题)?(不会|不知道|没思路|没有思路)(了|，)?(下一题|跳过|换一题|换个问题)?(吧)?$",
            r"^(好的|好|行|可以)?(下一题|下一个问题|跳过)(吧)?$",
        )
        return any(re.fullmatch(pattern, normalized) for pattern in patterns)

    async def _handle_candidate_requested_skip(self, text: str) -> None:
        exchange = self._current_exchange
        if exchange is None:
            return
        self._cancel_timeout_task()
        self._cancel_speech_guard_task()
        self._state = InterviewState.SKIPPING_QUESTION
        self._skipped_questions += 1
        self._consecutive_skipped_questions += 1
        self._record_turn(
            role="system",
            type="question_skipped",
            text="candidate_requested_skip",
            question_id=exchange.question_id,
            exchange_id=exchange.exchange_id,
            metadata={"reason": "candidate_requested_skip", "utterance": text[:80]},
        )
        if self._current_question is not None:
            self._completed_question_ids.add(self._current_question.question_id)
        next_question = self._planner.next_question(self._completed_question_ids)
        if next_question is None:
            if self._effective_answers < 1:
                await self._terminate("insufficient_effective_answers")
            else:
                await self._complete()
            return
        self._state = InterviewState.TRANSITIONING
        await self._ask_question(next_question, prompt_type="main_question")

    async def handle_exchange_timeout(self) -> None:
        if self._state != InterviewState.LISTENING or self._current_exchange is None:
            return
        self._cancel_timeout_task()
        exchange = self._current_exchange
        self._state = InterviewState.SKIPPING_QUESTION
        self._skipped_questions += 1
        self._consecutive_skipped_questions += 1
        self._record_turn(
            role="system",
            type="question_skipped",
            text="hard_timeout_no_answer",
            question_id=exchange.question_id,
            exchange_id=exchange.exchange_id,
            metadata={"reason": "hard_timeout_no_answer"},
        )
        if self._current_question is not None:
            self._completed_question_ids.add(self._current_question.question_id)
        if self._should_terminate_for_no_answer():
            await self._terminate("too_many_no_answer_timeouts")
            return
        next_question = self._planner.next_question(self._completed_question_ids)
        if next_question is None:
            if self._effective_answers < 1:
                await self._terminate("insufficient_effective_answers")
                return
            await self._complete()
            return
        # 跳题话术：虚拟人真的会说出来（真实 send_prompt），
        # 但前端对话页面不显示具体文案（前端 HIDDEN_TURN_TYPES 过滤 + 服务端
        # get_status 过滤双保险）。
        skip_transition_text = self._skip_transition_text
        self._mark_prompt_sent()
        await self._agent.send_prompt(skip_transition_text)
        self._start_prompt_playback_watchdog(skip_transition_text)
        self._record_turn(
            role="interviewer",
            type="question_skip_transition",
            text=skip_transition_text,
        )
        self._pending_question_prompt_after_idle = {
            "question": next_question,
            "prompt_type": "main_question",
        }

    def get_status(self) -> dict:
        public_report_generation = {
            key: value
            for key, value in self._report_generation.items()
            if key not in {"attempt", "maxAttempts"}
        }
        return {
            "state": self._state.value,
            "captureAllowed": bool(
                self._state == InterviewState.LISTENING
                and self._current_exchange is not None
            ),
            "interviewId": self._interview_id,
            "currentQuestion": (
                {
                    "questionId": self._current_question.question_id,
                    "sectionId": self._current_question.section_id,
                    "prompt": self._current_question.prompt,
                }
                if self._current_question
                else None
            ),
            "currentExchange": (
                {
                    "exchangeId": self._current_exchange.exchange_id,
                    "questionId": self._current_exchange.question_id,
                    "sectionId": self._current_exchange.section_id,
                    "promptType": self._current_exchange.prompt_type,
                    "promptText": self._current_exchange.prompt_text,
                    "answerText": self._current_exchange.answer_text,
                }
                if self._current_exchange
                else None
            ),
            "questionsCompleted": len(self._completed_question_ids),
            "totalQuestions": len(self._manager.get_question_specs()),
            "progressPercent": self._progress_percent(),
            "stageProgressPercent": self._stage_progress_percent(),
            "stageLabel": self._stage_label(),
            "candidateMessage": self._candidate_message(),
            "skippedQuestions": self._skipped_questions,
            "terminationReason": self._termination_reason,
            "reportGeneration": public_report_generation,
            "transcript": [
                self._turn_to_dict(turn)
                for turn in self._transcript
                # 新规则：答后衔接 / 思考中提示 / 跳题话术已关闭，
                # 服务端返回 transcript 时也直接过滤掉这三类 turn
                if getattr(turn, "type", None)
                not in {"answer_acknowledgement", "thinking_check", "question_skip_transition"}
            ],
            "finalReport": self._report_to_dict(self._report) if self._report else None,
        }

    def _persist_status(self) -> None:
        if self._session_store is not None:
            self._session_store.save_status(self._interview_id, self.get_status())

    def _progress_percent(self) -> int:
        total = len(self._manager.get_question_specs())
        if total <= 0:
            return 0
        # 规则：已完成 + 当前正在问的（>=1）都算进度
        # 这样走到第 4 题时进度 = 4/9 ≈ 44%，比"完成才计数"更直观
        reached = len(self._completed_question_ids)
        if self._current_question is not None:
            reached += 1
        return min(100, round(reached / total * 100))

    def _stage_progress_percent(self) -> int:
        if self._state in {
            InterviewState.REPORT_GENERATING,
            InterviewState.REPORT_ERROR,
            InterviewState.CLOSING,
            InterviewState.COMPLETED,
            InterviewState.TERMINATED,
        }:
            return 100
        if self._state in {
            InterviewState.IDLE,
            InterviewState.WAITING_SCENE_READY,
        }:
            return 0
        question_id, section_id = self._active_progress_slot()
        if not question_id and not section_id:
            return 8 if self._state == InterviewState.OPENING else 0
        anchor = self._progress_anchor_for(question_id, section_id)
        if self._current_exchange is not None:
            if self._current_exchange.prompt_type == "follow_up":
                anchor += 3
            elif self._state in {
                InterviewState.ANALYZING,
                InterviewState.DECIDING_FOLLOWUP,
                InterviewState.PLANNING_FOLLOWUP,
            }:
                anchor += 2
        return min(anchor, 96)

    def _stage_label(self) -> str:
        if self._state == InterviewState.IDLE:
            return "面试准备中"
        if self._state in {InterviewState.WAITING_SCENE_READY, InterviewState.OPENING}:
            return "开场了解中"
        if self._state in {
            InterviewState.CLOSING,
            InterviewState.COMPLETED,
            InterviewState.TERMINATED,
        }:
            return "面试收尾中"
        if self._state == InterviewState.REPORT_GENERATING:
            return "AI 报告生成中"
        if self._state == InterviewState.REPORT_ERROR:
            return "AI 报告生成失败"
        question_id, section_id = self._active_progress_slot()
        if section_id == "business" or str(question_id or "").startswith("business_"):
            return "综合考察中"
        if section_id in {"resume_project_intro", "resume_experience"} or str(
            question_id or ""
        ).startswith("resume_"):
            return "项目深挖中"
        return "开场了解中"

    def _active_progress_slot(self) -> tuple[str | None, str | None]:
        if self._current_question is not None:
            return self._current_question.question_id, self._current_question.section_id
        if self._current_exchange is not None:
            return self._current_exchange.question_id, self._current_exchange.section_id
        for turn in reversed(self._transcript):
            if turn.role != "interviewer":
                continue
            if turn.type not in {"main_question", "follow_up"}:
                continue
            metadata = turn.metadata or {}
            return turn.question_id, metadata.get("sectionId")
        return None, None

    @staticmethod
    def _progress_anchor_for(question_id: str | None, section_id: str | None) -> int:
        qid = str(question_id or "")
        sid = str(section_id or "")
        if qid == "self_intro" or sid == "self_intro":
            return 15
        if sid in {"resume_project_intro", "resume_experience"} or qid.startswith(
            "resume_"
        ):
            resume_match = re.match(r"resume(?:_intro)?_(\d+)", qid)
            resume_index = int(resume_match.group(1)) if resume_match else 1
            return 30 if resume_index <= 1 else 45
        if sid == "business" or qid.startswith("business_"):
            business_match = re.match(r"business_(\d+)", qid)
            business_index = int(business_match.group(1)) if business_match else 1
            return min(90, 45 + 15 * max(1, business_index))
        return 8

    def _candidate_message(self) -> str:
        messages = {
            InterviewState.IDLE: "面试尚未开始。",
            InterviewState.WAITING_SCENE_READY: "正在数字人面试官正在准备。",
            InterviewState.OPENING: "面试官正在介绍面试流程。",
            InterviewState.ASKING: "面试官说话中",
            InterviewState.LISTENING: "正在聆听你的回答",
            InterviewState.THINKING_CHECK: "面试官说话中",
            InterviewState.ANALYZING: "正在分析你的回答。",
            InterviewState.DECIDING_FOLLOWUP: "正在判断是否需要追问。",
            InterviewState.PLANNING_FOLLOWUP: "正在准备追问。",
            InterviewState.SKIPPING_QUESTION: "当前问题已超时，准备进入下一题。",
            InterviewState.REPORT_GENERATING: "正在生成 AI 面试报告。",
            InterviewState.REPORT_ERROR: "AI 报告生成失败，可重新生成。",
            InterviewState.CLOSING: "正在生成面试反馈。",
            InterviewState.COMPLETED: "面试已完成，可以查看反馈报告。",
            InterviewState.TERMINATED: "面试已提前结束。",
            InterviewState.ERROR: "面试出现异常。",
        }
        return messages.get(self._state, "面试进行中。")

    async def _ask_question(
        self,
        question: QuestionSpec,
        *,
        prompt_type: str,
        prompt_text: str | None = None,
        parent_exchange_id: str | None = None,
        probe_index: int = 0,
    ) -> None:
        self._state = InterviewState.ASKING
        self._mark_prompt_sent()
        self._current_question = question
        self._asked_question_ids.add(question.question_id)
        exchange = Exchange(
            exchange_id=self._next_exchange_id(),
            question_id=question.question_id,
            section_id=question.section_id,
            type=prompt_type,
            prompt_id=self._next_prompt_id(),
            prompt_text=self._spoken_prompt_text(
                question=question,
                prompt_type=prompt_type,
                prompt_text=prompt_text,
                probe_index=probe_index,
            ),
            prompt_type=prompt_type,
            parent_exchange_id=parent_exchange_id,
            probe_index=probe_index,
        )
        self._current_exchange = exchange
        self._exchanges.append(exchange)
        await self._agent.send_prompt(
            exchange.prompt_text,
            metadata=self._metadata_for(exchange),
        )
        self._start_prompt_playback_watchdog(exchange.prompt_text)
        self._record_turn(
            role="interviewer",
            type=prompt_type,
            text=exchange.prompt_text,
            question_id=exchange.question_id,
            exchange_id=exchange.exchange_id,
            metadata=self._metadata_for(exchange),
        )
        # 起一道新题时，记录这一道的开始时间；上一道（如果还在计时）也要 flush
        self._flush_current_question_duration()
        self._current_question_started_at = perf_counter()
        self._persist_status()

    async def _complete(self) -> None:
        self._flush_current_question_duration()
        self._start_report_task("completed", None)

    async def _finish_completed_report(self) -> None:
        self._state = InterviewState.CLOSING
        text = self._closing_text
        # Spoken LLM recap (总评) of the whole interview, said just before the closing
        # line. Falls back to the plain closing line when unavailable (returns "").
        if self._closing_comment_generator is not None:
            recap = await self._closing_comment_generator.generate_async(
                self._report,
                target_role=self._manager.config.candidate.target_role,
            )
            if recap:
                text = f"{recap}\n{self._closing_text}"
        self._mark_prompt_sent()
        await self._agent.send_prompt(text)
        self._start_prompt_playback_watchdog(text)
        self._record_turn(role="interviewer", type="closing", text=text)
        self._pending_terminal_state_after_idle = InterviewState.COMPLETED
        self._persist_status()

    async def _terminate(self, reason: str) -> None:
        self._termination_reason = reason
        self._flush_current_question_duration()
        self._start_report_task("terminated_prompt", reason)

    async def _finish_terminated_report(self, reason: str) -> None:
        self._state = InterviewState.CLOSING
        text = self._termination_text
        self._mark_prompt_sent()
        await self._agent.send_prompt(text)
        self._start_prompt_playback_watchdog(text)
        self._record_turn(
            role="interviewer",
            type="termination",
            text=text,
            metadata={"reason": reason},
        )
        self._pending_terminal_state_after_idle = InterviewState.TERMINATED
        self._persist_status()

    async def _run_report_flow(
        self,
        mode: str,
        termination_reason: str | None,
        generation_id: str,
    ) -> bool:
        await self._await_pending_evaluations()
        try:
            report = await asyncio.wait_for(
                self._report_generator.generate_async(
                    self._exchanges,
                    transcript=self._transcript,
                    rubric_dimensions=self._manager.config.rubric_dimensions,
                    termination_reason=termination_reason,
                    actual_duration_seconds=self._actual_duration_seconds(),
                    on_progress=lambda progress: self._on_report_progress_for(
                        generation_id, progress
                    ),
                ),
                timeout=15 * 60,
            )
        except ReportGenerationError as exc:
            if generation_id != self._report_generation_id:
                return False
            self._state = InterviewState.REPORT_ERROR
            self._report_generation.update(
                {
                    "state": "error",
                    "message": "AI 报告尚未完整生成，可继续生成",
                    "error": str(exc)[:240],
                }
            )
            self._persist_status()
            return False
        except TimeoutError:
            if generation_id != self._report_generation_id:
                return False
            self._state = InterviewState.REPORT_ERROR
            self._report_generation.update(
                {
                    "state": "error",
                    "message": "AI 报告生成时间过长，可继续生成",
                    "error": "report generation exceeded 15 minutes",
                }
            )
            self._persist_status()
            return False

        if generation_id != self._report_generation_id:
            logger.info(
                "discarded stale report generation: interview_id=%s generation_id=%s",
                self._interview_id,
                generation_id,
            )
            return False
        self._report = report

        if mode == "completed":
            await self._finish_completed_report()
        elif mode == "terminated_prompt":
            await self._finish_terminated_report(termination_reason or "")
        else:
            self._state = InterviewState.TERMINATED
            self._persist_status()
        return True

    async def retry_report_generation(self) -> bool:
        if self._state != InterviewState.REPORT_ERROR or not self._report_finish_mode:
            return False
        self._start_report_task(
            self._report_finish_mode,
            self._report_finish_reason,
            force=True,
        )
        return True

    def _start_report_task(
        self,
        mode: str,
        termination_reason: str | None,
        *,
        force: bool = False,
    ) -> asyncio.Task:
        if (
            not force
            and self._report_task is not None
            and not self._report_task.done()
        ):
            return self._report_task
        self._report_finish_mode = mode
        self._report_finish_reason = termination_reason
        self._report_generation_id = f"gen_{uuid.uuid4().hex[:10]}"
        self._report = None
        self._state = InterviewState.REPORT_GENERATING
        preserved_completed = (
            int(self._report_generation.get("completedSteps") or 0) if force else 0
        )
        preserved_total = (
            int(self._report_generation.get("totalSteps") or 0) if force else 0
        )
        preserved_percent = (
            int(self._report_generation.get("percent") or 0) if force else 3
        )
        preserved_stage = (
            str(self._report_generation.get("stage") or "preprocessing")
            if force
            else "preprocessing"
        )
        self._report_generation.update(
            {
                "state": "generating",
                "stage": preserved_stage,
                "message": (
                    "正在继续处理未完成部分"
                    if force
                    else "正在整理面试记录"
                ),
                "completedSteps": preserved_completed,
                "totalSteps": preserved_total,
                "percent": preserved_percent,
                "attempt": 0,
                "maxAttempts": 0,
                "error": "",
                "generationSource": "",
                "generationId": self._report_generation_id,
            }
        )
        self._persist_status()
        task = asyncio.create_task(
            self._run_report_flow(
                mode,
                termination_reason,
                self._report_generation_id,
            )
        )
        self._report_task = task
        return task

    def _on_report_progress_for(self, generation_id: str, progress: dict) -> None:
        if generation_id != self._report_generation_id:
            return
        self._on_report_progress(progress)

    def _on_report_progress(self, progress: dict) -> None:
        next_percent = max(0, min(100, int(progress.get("percent") or 0)))
        next_state = str(progress.get("state") or "generating")
        if next_state != "completed":
            next_percent = max(int(self._report_generation.get("percent") or 0), next_percent)
        next_completed = int(progress.get("completed_steps") or 0)
        next_total = int(progress.get("total_steps") or 0)
        if next_state != "completed":
            next_completed = max(
                int(self._report_generation.get("completedSteps") or 0),
                next_completed,
            )
            next_total = max(
                int(self._report_generation.get("totalSteps") or 0),
                next_total,
            )
        next_stage = str(progress.get("stage") or "")
        if next_stage in {"", "error"}:
            next_stage = str(
                self._report_generation.get("stage") or "preprocessing"
            )
        self._report_generation.update(
            {
                "state": next_state,
                "stage": next_stage,
                "message": str(progress.get("message") or ""),
                "completedSteps": next_completed,
                "totalSteps": next_total,
                "percent": next_percent,
                "attempt": int(progress.get("attempt") or 0),
                "maxAttempts": int(progress.get("max_attempts") or 0),
                "error": str(progress.get("error") or ""),
                "generationSource": str(progress.get("generation_source") or ""),
            }
        )
        self._persist_status()

    def _start_timeout_task(self, exchange_id: str) -> None:
        self._cancel_timeout_task()
        self._timeout_task = asyncio.create_task(self._run_exchange_timeout(exchange_id))

    def _cancel_timeout_task(self) -> None:
        task = self._timeout_task
        if task is None:
            return
        if task is not asyncio.current_task() and not task.done():
            task.cancel()
        self._timeout_task = None

    def _start_speech_guard_task(self) -> None:
        self._cancel_speech_guard_task()
        if self._current_exchange is None:
            return
        self._speech_guard_task = asyncio.create_task(
            self._run_speech_guard(self._current_exchange.exchange_id)
        )

    def _cancel_speech_guard_task(self) -> None:
        task = self._speech_guard_task
        if task is None:
            return
        if task is not asyncio.current_task() and not task.done():
            task.cancel()
        self._speech_guard_task = None

    async def _run_speech_guard(self, exchange_id: str) -> None:
        try:
            await asyncio.sleep(max(0.0, self._candidate_speech_grace_seconds))
            if (
                self._current_exchange is not None
                and self._current_exchange.exchange_id == exchange_id
                and not self._current_exchange.answer_text
            ):
                self._resume_answer_timeout_if_needed()
        except asyncio.CancelledError:
            return

    def _resume_answer_timeout_if_needed(self) -> None:
        if (
            self._current_exchange is None
            or self._current_exchange.answer_text
            or self._state not in {InterviewState.LISTENING, InterviewState.THINKING_CHECK}
        ):
            return
        self._state = InterviewState.LISTENING
        self._start_timeout_task(self._current_exchange.exchange_id)
        self._persist_status()

    async def _run_exchange_timeout(self, exchange_id: str) -> None:
        elapsed = 0.0
        try:
            for after_seconds, prompt in self._thinking_checks:
                delay = max(0.0, after_seconds - elapsed)
                await asyncio.sleep(delay)
                elapsed = after_seconds
                if not self._is_current_listening_exchange(exchange_id):
                    return
                await self._send_thinking_check(prompt)
            await asyncio.sleep(max(0.0, self._hard_timeout_seconds - elapsed))
            if self._is_current_listening_exchange(exchange_id):
                await self.handle_exchange_timeout()
        except asyncio.CancelledError:
            return

    def _is_current_listening_exchange(self, exchange_id: str) -> bool:
        return (
            self._state == InterviewState.LISTENING
            and self._current_exchange is not None
            and self._current_exchange.exchange_id == exchange_id
        )

    def _is_current_thinking_exchange(self, exchange_id: str) -> bool:
        return (
            self._state == InterviewState.THINKING_CHECK
            and self._current_exchange is not None
            and self._current_exchange.exchange_id == exchange_id
        )

    async def _send_thinking_check(self, prompt: str) -> None:
        # 跳题话术 / 思考中提醒：虚拟人真的会说出来（真实 send_prompt），
        # 但前端对话页面不显示具体文案（前端 HIDDEN_TURN_TYPES 过滤 + 服务端
        # get_status 过滤双保险）。
        exchange = self._current_exchange
        if exchange is None:
            return
        self._state = InterviewState.THINKING_CHECK
        idle_waiter = self._create_idle_waiter()
        self._mark_prompt_sent()
        await self._agent.send_prompt(prompt)
        self._start_prompt_playback_watchdog(prompt)
        self._record_turn(
            role="interviewer",
            type="thinking_check",
            text=prompt,
            question_id=exchange.question_id,
            exchange_id=exchange.exchange_id,
        )
        self._persist_status()
        await self._wait_for_idle(idle_waiter)
        if self._is_current_thinking_exchange(exchange.exchange_id):
            self._state = InterviewState.LISTENING
            self._persist_status()

    def _spoken_prompt_text(
        self,
        *,
        question: QuestionSpec,
        prompt_type: str,
        prompt_text: str | None,
        probe_index: int,
    ) -> str:
        raw_prompt = prompt_text or question.prompt
        if prompt_type == "follow_up":
            return f"{self._follow_up_prefix_cycle.next()}{raw_prompt}"
        if prompt_type == "main_question" and probe_index == 0:
            if len(self._completed_question_ids) == 0 and len(self._asked_question_ids) == 1:
                return f"{self._first_question_transition}{raw_prompt}"
            return f"{self._next_question_transition_cycle.next()}{raw_prompt}"
        return raw_prompt

    async def _notify_terminal(self) -> None:
        if self._on_terminal is not None:
            await self._on_terminal(self._state)

    def _log_answer_timing(
        self,
        *,
        exchange: Exchange,
        follow_up_needed: bool,
        acknowledgement_ms: int,
        evaluation_ms: int | str,
        follow_up_decision_ms: int,
        follow_up_planning_ms: int,
        total_ms: int,
    ) -> None:
        logger.info(
            "interview answer timing: interview_id=%s question_id=%s "
            "exchange_id=%s prompt_type=%s probe_index=%s "
            "follow_up_needed=%s acknowledgement_ms=%s evaluation_ms=%s "
            "followup_decision_ms=%s followup_planning_ms=%s total_ms=%s",
            self._interview_id,
            exchange.question_id,
            exchange.exchange_id,
            exchange.prompt_type,
            exchange.probe_index,
            follow_up_needed,
            acknowledgement_ms,
            evaluation_ms,
            follow_up_decision_ms,
            follow_up_planning_ms,
            total_ms,
        )

    def _mark_prompt_sent(self) -> None:
        self._cancel_prompt_playback_watchdog()
        self._playback_mapper.mark_prompt_sent()

    def _start_prompt_playback_watchdog(self, prompt_text: str = "") -> None:
        self._cancel_prompt_playback_watchdog()
        text_length = len("".join(str(prompt_text or "").split()))
        estimated_timeout = max(5.0, min(30.0, 3.0 + text_length / 5.0))
        timeout_seconds = min(
            max(0.0, self._prompt_playback_timeout_seconds), estimated_timeout
        )
        self._prompt_playback_watchdog_task = asyncio.create_task(
            self._run_prompt_playback_watchdog(timeout_seconds)
        )

    def _cancel_prompt_playback_watchdog(self) -> None:
        task = self._prompt_playback_watchdog_task
        if task is None:
            return
        if task is not asyncio.current_task() and not task.done():
            task.cancel()
        self._prompt_playback_watchdog_task = None

    async def _run_prompt_playback_watchdog(self, timeout_seconds: float) -> None:
        try:
            await asyncio.sleep(max(0.0, timeout_seconds))
            if self._playback_mapper.force_finish_prompt() == PlaybackEvent.PROMPT_FINISHED:
                logger.warning(
                    "interview prompt playback watchdog fired: interview_id=%s state=%s",
                    self._interview_id,
                    self._state.value,
                )
                self._prompt_playback_watchdog_task = None
                await self.notify_platform_idle()
        except asyncio.CancelledError:
            return

    def _start_evaluation_task(
        self, exchange: Exchange, answer_text: str
    ) -> asyncio.Task:
        task = asyncio.create_task(self._evaluate_exchange(exchange, answer_text))
        self._pending_evaluation_tasks.add(task)
        task.add_done_callback(self._pending_evaluation_tasks.discard)
        return task

    async def _wait_for_foreground_evaluation(self, task: asyncio.Task) -> bool:
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self._foreground_evaluation_timeout_seconds,
            )
            return True
        except TimeoutError:
            logger.info(
                "interview evaluation moved to background: interview_id=%s timeout_ms=%s",
                self._interview_id,
                round(self._foreground_evaluation_timeout_seconds * 1000),
            )
            return False

    @staticmethod
    def _specific_ai_follow_up(
        *, question, evaluation, probe_index: int, answer_text: str = ""
    ):
        max_followups = question.max_followups
        if max_followups is not None and probe_index >= max_followups:
            return FollowUpDecision(needed=False)
        if not evaluation.follow_up_needed:
            return FollowUpDecision(needed=False)
        raw = " ".join(str(evaluation.follow_up_question or "").split()).strip()
        if not raw or len(raw) > 160:
            return FollowUpDecision(needed=False)
        first_marks = [i for i in (raw.find("？"), raw.find("?")) if i >= 0]
        if first_marks:
            first = min(first_marks)
            if "？" in raw[first + 1 :] or "?" in raw[first + 1 :]:
                return FollowUpDecision(needed=False)
            raw = raw[:first].rstrip("？?") + "？"
        else:
            raw = raw.rstrip("。！!；;") + "？"
        key = re.sub(r"[\W_]+", "", raw).lower()
        generic = {
            "请补充一个具体做法和最终结果",
            "可以再具体讲讲你的做法和结果吗",
            "可以再具体讲讲你的做法取舍和结果吗",
            "请再具体说明一下",
            "能再展开讲讲吗",
        }
        if key in {re.sub(r"[\W_]+", "", item).lower() for item in generic}:
            return FollowUpDecision(needed=False)
        if key == re.sub(r"[\W_]+", "", question.prompt).lower():
            return FollowUpDecision(needed=False)
        if not InterviewController._follow_up_is_grounded(
            question.prompt, answer_text, raw
        ):
            return FollowUpDecision(needed=False)
        return FollowUpDecision(needed=True, suggested_question=raw)

    @staticmethod
    def _follow_up_is_grounded(
        question_text: str, answer_text: str, follow_up_text: str
    ) -> bool:
        ignored = {
            "具体", "什么", "如何", "可以", "一下", "这个", "那个", "问题",
            "回答", "刚才", "讲讲", "说明", "请问", "你的", "你会", "是否",
            "设计", "应该", "需要", "进行", "负责",
        }

        def units(text: str) -> set[str]:
            result = {
                token.lower()
                for token in re.findall(r"[A-Za-z][A-Za-z0-9_+-]{2,}", text or "")
            }
            for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text or ""):
                result.update(chunk[index : index + 2] for index in range(len(chunk) - 1))
            return {item for item in result if item not in ignored}

        follow_units = units(follow_up_text)
        if not follow_units:
            return False
        answer_units = units(answer_text)
        if answer_units and answer_units.intersection(follow_units):
            return True
        question_units = units(question_text)
        return bool(question_units.intersection(follow_units))

    async def _evaluate_exchange(self, exchange: Exchange, answer_text: str) -> None:
        started_at = perf_counter()
        try:
            exchange.evaluation = await self._evaluator.evaluate(
                exchange.prompt_text,
                answer_text,
                transcript=[self._turn_to_dict(turn) for turn in self._transcript],
                rubric_dimensions=self._manager.config.rubric_dimensions,
            )
            logger.info(
                "interview evaluation timing: interview_id=%s question_id=%s "
                "exchange_id=%s evaluation_ms=%s",
                self._interview_id,
                exchange.question_id,
                exchange.exchange_id,
                round((perf_counter() - started_at) * 1000),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "interview evaluation failed: interview_id=%s question_id=%s exchange_id=%s",
                self._interview_id,
                exchange.question_id,
                exchange.exchange_id,
            )

    async def _await_pending_evaluations(self) -> None:
        if not self._pending_evaluation_tasks:
            return
        tasks = set(self._pending_evaluation_tasks)
        done, pending = await asyncio.wait(
            tasks,
            timeout=max(0.0, self._evaluation_join_timeout_seconds),
        )
        if pending:
            logger.warning(
                "interview evaluation join timeout: interview_id=%s pending=%s",
                self._interview_id,
                len(pending),
            )
            for task in pending:
                task.cancel()
        if done or pending:
            await asyncio.gather(*done, *pending, return_exceptions=True)

    def _create_idle_waiter(self) -> asyncio.Future:
        waiter = asyncio.get_running_loop().create_future()
        self._idle_waiters.append(waiter)
        return waiter

    async def _wait_for_idle(self, waiter: asyncio.Future) -> None:
        try:
            await waiter
        finally:
            if waiter in self._idle_waiters:
                self._idle_waiters.remove(waiter)

    def _resolve_idle_waiters(self) -> None:
        waiters = self._idle_waiters
        self._idle_waiters = []
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)

    def _should_terminate_for_no_answer(self) -> bool:
        return (
            self._skipped_questions >= self._max_skipped_questions
            or self._consecutive_skipped_questions >= self._max_consecutive_skipped_questions
        )

    def _metadata_for(self, exchange: Exchange) -> dict:
        return {
            "interviewId": self._interview_id,
            "sectionId": exchange.section_id,
            "questionId": exchange.question_id,
            "exchangeId": exchange.exchange_id,
            "promptId": exchange.prompt_id,
            "promptType": exchange.prompt_type,
        }

    def _next_exchange_id(self) -> str:
        self._exchange_seq += 1
        return f"ex_{self._exchange_seq:03d}"

    def _max_followups_for(self, question: QuestionSpec) -> int:
        if question.max_followups is not None:
            return question.max_followups
        return self._manager.config.max_probe_per_question

    def _next_prompt_id(self) -> str:
        self._prompt_seq += 1
        return f"prompt_{self._prompt_seq:03d}"

    def _next_turn_id(self) -> str:
        self._turn_seq += 1
        return f"turn_{self._turn_seq:03d}"

    def _record_turn(
        self,
        *,
        role: str,
        type: str,
        text: str,
        question_id: str | None = None,
        exchange_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        self._transcript.append(
            TranscriptTurn(
                turn_id=self._next_turn_id(),
                interview_id=self._interview_id,
                role=role,
                type=type,
                text=text,
                question_id=question_id,
                exchange_id=exchange_id,
                metadata=dict(metadata or {}),
            )
        )
        self._persist_status()

    @staticmethod
    def _turn_to_dict(turn: TranscriptTurn) -> dict:
        return {
            "turnId": turn.turn_id,
            "interviewId": turn.interview_id,
            "role": turn.role,
            "type": turn.type,
            "text": turn.text,
            "questionId": turn.question_id,
            "exchangeId": turn.exchange_id,
            "metadata": turn.metadata,
        }

    @staticmethod
    def _report_to_dict(report: InterviewReport) -> dict:
        return {
            "generationSource": report.generation_source,
            "cover": {
                "title": report.cover.title,
                "interviewType": report.cover.interview_type,
                "durationText": report.cover.duration_text,
                "generatedAt": report.cover.generated_at,
            },
            "summary": report.summary,
            "overallScore": report.overall_score,
            "strengths": report.strengths,
            "weaknesses": report.weaknesses,
            "recommendations": report.recommendations,
            "highlights": {
                "alerts": report.highlights.alerts,
                "advice": report.highlights.advice,
            },
            "dimensions": {
                name: {
                    "score": assessment.score,
                    "evidence": assessment.evidence,
                    "concerns": assessment.concerns,
                    "recommendations": assessment.recommendations,
                    "confidence": assessment.confidence,
                }
                for name, assessment in report.dimension_scores.items()
            },
            "dimensionCommentaries": [
                {
                    "key": item.key,
                    "title": item.title,
                    "score": item.score,
                    "commentary": item.commentary,
                }
                for item in report.dimension_commentaries
            ],
            "learningPlan": {
                "tags": report.learning_plan.tags,
                "phases": [
                    {
                        "title": phase.title,
                        "window": phase.window,
                        "items": phase.items,
                    }
                    for phase in report.learning_plan.phases
                ],
            },
            "qaAnalyses": [
                {
                    "questionIndex": item.question_index,
                    "question": item.question,
                    "answer": item.answer,
                    "strengths": item.strengths,
                    "risks": item.risks,
                    "commentary": item.commentary,
                    "approach": item.approach,
                }
                for item in report.qa_analyses
            ],
        }
