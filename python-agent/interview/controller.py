from __future__ import annotations

import asyncio
import logging
import random
import uuid
from enum import Enum
from time import perf_counter

from interview.interview_manager import InterviewManager
from interview.models import (
    Exchange,
    InterviewReport,
    InterviewState,
    QuestionSpec,
    TranscriptTurn,
)
from interview.follow_up_decider import FollowUpDecider
from interview.follow_up_planner import FollowUpPlanner


logger = logging.getLogger(__name__)


class PlaybackEvent(str, Enum):
    PROMPT_FINISHED = "prompt_finished"


class PlaybackSignalMapper:
    ACTIVE_STATES = {
        "PROMPT_THINKING",
        "PROMPT_STAGING",
        "PROMPT_SPEAKING",
        "SPEAKING",
    }

    def __init__(self) -> None:
        self._awaiting_prompt = False
        self._active_seen = False

    def mark_prompt_sent(self) -> None:
        self._awaiting_prompt = True
        self._active_seen = False

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
        if value != "IDLE":
            return None
        if not self._active_seen:
            return None
        self._awaiting_prompt = False
        self._active_seen = False
        return PlaybackEvent.PROMPT_FINISHED


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
        self._first_question_transition = (
            getattr(speech_config, "first_question_transition", "")
            or "我们先从第一个问题开始。"
        )
        self._next_question_transition = (
            getattr(speech_config, "next_question_transition", "")
            or "好的，我们看下一个问题。"
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
        self._thinking_checks = thinking_checks or [
            (20, "我看到你还在思考。你可以先从一个具体经历或关键决策讲起。"),
            (45, "这个问题可以再给你一点时间。如果暂时没有思路，也可以简单说明。"),
        ]
        self._hard_timeout_seconds = hard_timeout_seconds
        self._opening_to_question_delay_seconds = opening_to_question_delay_seconds
        self._prompt_playback_timeout_seconds = prompt_playback_timeout_seconds
        self._candidate_speech_grace_seconds = candidate_speech_grace_seconds
        self._evaluation_join_timeout_seconds = evaluation_join_timeout_seconds
        self._timeout_task: asyncio.Task | None = None
        self._speech_guard_task: asyncio.Task | None = None
        self._prompt_playback_watchdog_task: asyncio.Task | None = None
        self._pending_evaluation_tasks: set[asyncio.Task] = set()
        self._idle_waiters: list[asyncio.Future] = []
        self._playback_mapper = PlaybackSignalMapper()
        self._prompt_playback_completed_seen = False
        self._pending_completion_after_idle = False

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
        self._state = InterviewState.OPENING
        opening_text = self._manager.build_opening_text()
        opening_metadata = {
            "interviewId": self._interview_id,
            "promptId": self._next_prompt_id(),
            "promptType": "opening",
        }
        self._mark_prompt_sent()
        await self._agent.send_prompt(opening_text, metadata=opening_metadata)
        self._start_prompt_playback_watchdog()
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
        self._cancel_timeout_task()
        self._cancel_speech_guard_task()
        self._cancel_prompt_playback_watchdog()
        if self._state == InterviewState.IDLE:
            return
        await self._await_pending_evaluations()
        self._termination_reason = "user_stopped"
        self._report = await self._report_generator.generate_async(
            self._exchanges,
            transcript=self._transcript,
            rubric_dimensions=self._manager.config.rubric_dimensions,
            termination_reason=self._termination_reason,
        )
        self._state = InterviewState.TERMINATED
        self._persist_status()

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
        if self._state == InterviewState.TRANSITIONING and self._pending_completion_after_idle:
            self._pending_completion_after_idle = False
            await self._complete()
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
        self._prompt_playback_completed_seen = True
        logger.info(
            "interview recorded idle for later: interview_id=%s state=%s",
            self._interview_id,
            self._state.value,
        )

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

    async def handle_answer(self, answer_request_id: str, text: str) -> None:
        if (
            self._state not in {InterviewState.LISTENING, InterviewState.THINKING_CHECK}
            or self._current_exchange is None
        ):
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
        is_last_main_question = self._is_last_main_question()
        self._state = InterviewState.ANALYZING
        self._start_evaluation_task(exchange, text)
        acknowledgement_started_at = perf_counter()
        await self._send_processing_acknowledgement(
            exchange,
            final_answer=is_last_main_question,
        )
        acknowledgement_ms = round((perf_counter() - acknowledgement_started_at) * 1000)
        self._state = InterviewState.DECIDING_FOLLOWUP
        follow_up_decision = None
        follow_up = None
        follow_up_decision_ms = 0
        follow_up_planning_ms = 0
        if self._current_question is not None:
            follow_up_decision_started_at = perf_counter()
            follow_up_decision = await self._follow_up_decider.decide_async(
                question=self._current_question,
                answer_text=text,
                evaluation=None,
                transcript=self._transcript,
                probe_index=exchange.probe_index,
            )
            follow_up_decision_ms = round(
                (perf_counter() - follow_up_decision_started_at) * 1000
            )
            if follow_up_decision.needed:
                self._state = InterviewState.PLANNING_FOLLOWUP
                follow_up_planning_started_at = perf_counter()
                follow_up = self._follow_up_planner.plan(
                    question=self._current_question,
                    decision=follow_up_decision,
                )
                follow_up_planning_ms = round(
                    (perf_counter() - follow_up_planning_started_at) * 1000
                )
        self._log_answer_timing(
            exchange=exchange,
            follow_up_needed=bool(follow_up_decision and follow_up_decision.needed),
            acknowledgement_ms=acknowledgement_ms,
            evaluation_ms="background",
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
            await self._ask_or_queue_question_after_current_prompt(
                {
                    "question": self._current_question,
                    "prompt_text": follow_up,
                    "prompt_type": "follow_up",
                    "parent_exchange_id": exchange.exchange_id,
                    "probe_index": exchange.probe_index + 1,
                }
            )
            return

        if self._current_question is not None:
            self._completed_question_ids.add(self._current_question.question_id)
        next_question = self._planner.next_question(self._completed_question_ids)
        if next_question is None:
            await self._complete_or_queue_after_current_prompt()
            return
        self._state = InterviewState.TRANSITIONING
        await self._ask_or_queue_question_after_current_prompt(
            {
                "question": next_question,
                "prompt_type": "main_question",
            }
        )

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
        skip_transition_text = self._skip_transition_text
        self._mark_prompt_sent()
        await self._agent.send_prompt(skip_transition_text)
        self._start_prompt_playback_watchdog()
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
        return {
            "state": self._state.value,
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
                    "promptText": self._current_exchange.prompt_text,
                    "answerText": self._current_exchange.answer_text,
                }
                if self._current_exchange
                else None
            ),
            "questionsCompleted": len(self._completed_question_ids),
            "totalQuestions": len(self._manager.get_question_specs()),
            "progressPercent": self._progress_percent(),
            "candidateMessage": self._candidate_message(),
            "skippedQuestions": self._skipped_questions,
            "terminationReason": self._termination_reason,
            "transcript": [self._turn_to_dict(turn) for turn in self._transcript],
            "finalReport": self._report_to_dict(self._report) if self._report else None,
        }

    def _persist_status(self) -> None:
        if self._session_store is not None:
            self._session_store.save_status(self._interview_id, self.get_status())

    def _progress_percent(self) -> int:
        total = len(self._manager.get_question_specs())
        if total <= 0:
            return 0
        return min(100, round(len(self._completed_question_ids) / total * 100))

    def _candidate_message(self) -> str:
        messages = {
            InterviewState.IDLE: "面试尚未开始。",
            InterviewState.WAITING_SCENE_READY: "正在数字人面试官正在准备。",
            InterviewState.OPENING: "面试官正在介绍面试流程。",
            InterviewState.ASKING: "面试官正在提问。",
            InterviewState.LISTENING: "请开始回答当前问题。",
            InterviewState.THINKING_CHECK: "面试官正在确认你是否还在思考。",
            InterviewState.ANALYZING: "正在分析你的回答。",
            InterviewState.DECIDING_FOLLOWUP: "正在判断是否需要追问。",
            InterviewState.PLANNING_FOLLOWUP: "正在准备追问。",
            InterviewState.SKIPPING_QUESTION: "当前问题已超时，准备进入下一题。",
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
        self._start_prompt_playback_watchdog()
        self._record_turn(
            role="interviewer",
            type=prompt_type,
            text=exchange.prompt_text,
            question_id=exchange.question_id,
            exchange_id=exchange.exchange_id,
            metadata=self._metadata_for(exchange),
        )
        self._persist_status()

    async def _complete(self) -> None:
        self._state = InterviewState.CLOSING
        await self._await_pending_evaluations()
        self._report = await self._report_generator.generate_async(
            self._exchanges,
            transcript=self._transcript,
            rubric_dimensions=self._manager.config.rubric_dimensions,
        )
        text = self._closing_text
        self._mark_prompt_sent()
        await self._agent.send_prompt(text)
        self._start_prompt_playback_watchdog()
        self._record_turn(role="interviewer", type="closing", text=text)
        self._pending_terminal_state_after_idle = InterviewState.COMPLETED
        self._persist_status()

    async def _terminate(self, reason: str) -> None:
        self._state = InterviewState.CLOSING
        self._termination_reason = reason
        await self._await_pending_evaluations()
        self._report = await self._report_generator.generate_async(
            self._exchanges,
            transcript=self._transcript,
            rubric_dimensions=self._manager.config.rubric_dimensions,
            termination_reason=reason,
        )
        text = self._termination_text
        self._mark_prompt_sent()
        await self._agent.send_prompt(text)
        self._start_prompt_playback_watchdog()
        self._record_turn(
            role="interviewer",
            type="termination",
            text=text,
            metadata={"reason": reason},
        )
        self._pending_terminal_state_after_idle = InterviewState.TERMINATED
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
        exchange = self._current_exchange
        if exchange is None:
            return
        self._state = InterviewState.THINKING_CHECK
        idle_waiter = self._create_idle_waiter()
        self._mark_prompt_sent()
        await self._agent.send_prompt(prompt)
        self._start_prompt_playback_watchdog()
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

    async def _send_processing_acknowledgement(
        self,
        exchange: Exchange,
        *,
        final_answer: bool = False,
    ) -> None:
        phrases = (
            self._final_answer_acknowledgements
            if final_answer
            else self._answer_acknowledgements
        )
        text = self._choose_phrase(phrases)
        self._mark_prompt_sent()
        await self._agent.send_prompt(text)
        self._start_prompt_playback_watchdog()
        self._record_turn(
            role="interviewer",
            type="answer_acknowledgement",
            text=text,
            question_id=exchange.question_id,
            exchange_id=exchange.exchange_id,
        )

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
            return f"{self._choose_phrase(self._follow_up_prefixes)}{raw_prompt}"
        if prompt_type == "main_question" and probe_index == 0:
            if len(self._completed_question_ids) == 0 and len(self._asked_question_ids) == 1:
                return f"{self._first_question_transition}{raw_prompt}"
            return f"{self._next_question_transition}{raw_prompt}"
        return raw_prompt

    @staticmethod
    def _choose_phrase(phrases: tuple[str, ...]) -> str:
        return random.choice(phrases)

    def _is_last_main_question(self) -> bool:
        if self._current_question is None:
            return False
        completed = set(self._completed_question_ids)
        completed.add(self._current_question.question_id)
        return self._planner.next_question(completed) is None

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

    async def _ask_or_queue_question_after_current_prompt(self, prompt: dict) -> None:
        if self._prompt_playback_completed_seen:
            self._prompt_playback_completed_seen = False
            await self._ask_question(**prompt)
            return
        self._pending_question_prompt_after_idle = prompt

    async def _complete_or_queue_after_current_prompt(self) -> None:
        self._state = InterviewState.TRANSITIONING
        if self._prompt_playback_completed_seen:
            self._prompt_playback_completed_seen = False
            await self._complete()
            return
        self._pending_completion_after_idle = True

    def _mark_prompt_sent(self) -> None:
        self._cancel_prompt_playback_watchdog()
        self._playback_mapper.mark_prompt_sent()
        self._prompt_playback_completed_seen = False

    def _start_prompt_playback_watchdog(self) -> None:
        self._cancel_prompt_playback_watchdog()
        self._prompt_playback_watchdog_task = asyncio.create_task(
            self._run_prompt_playback_watchdog()
        )

    def _cancel_prompt_playback_watchdog(self) -> None:
        task = self._prompt_playback_watchdog_task
        if task is None:
            return
        if task is not asyncio.current_task() and not task.done():
            task.cancel()
        self._prompt_playback_watchdog_task = None

    async def _run_prompt_playback_watchdog(self) -> None:
        try:
            await asyncio.sleep(max(0.0, self._prompt_playback_timeout_seconds))
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

    def _start_evaluation_task(self, exchange: Exchange, answer_text: str) -> None:
        task = asyncio.create_task(self._evaluate_exchange(exchange, answer_text))
        self._pending_evaluation_tasks.add(task)
        task.add_done_callback(self._pending_evaluation_tasks.discard)

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
            "summary": report.summary,
            "overallScore": report.overall_score,
            "strengths": report.strengths,
            "weaknesses": report.weaknesses,
            "recommendations": report.recommendations,
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
        }
