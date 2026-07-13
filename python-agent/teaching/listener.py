"""TeachingListener — bridges LiveAvatar platform events to TeachingController.

Handles: speech callbacks with state-gating, Q&A flow, transition generation,
and interaction feedback.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
import uuid

from liveavatar_channel_sdk import AgentListener, AvatarAgent, SessionState

from teaching.asr_manager import QwenAsrManager
from teaching.teaching_controller import (
    TeachingController,
    TeachingState,
    LECTURE_SYSTEM_PROMPT,
    QA_SYSTEM_PROMPT,
    TRANSITION_PROMPT,
    INTERACTION_FEEDBACK_PROMPT,
)


class TeachingListener(AgentListener):
    def __init__(
        self,
        *,
        asr_manager: QwenAsrManager | None = None,
        llm_client=None,  # LlmClient — deferred import to avoid circular
    ) -> None:
        self.agent: AvatarAgent | None = None
        self.controller: TeachingController | None = None
        self.asr_manager = asr_manager
        self.llm_client = llm_client
        self._echo_cooldown_until: float = 0.0
        self._voice_request_id: str | None = None
        self._processing_task: asyncio.Task | None = None
        self._current_response_id: str | None = None
        self._hand_timeout_task: asyncio.Task | None = None
        self._qa_in_progress = False
        self._hand_cancelled_during_qa = False  # Flag: student wants to go back to lecture
        self._follow_up_count = 0               # Prevent infinite QA loop

    def set_controller(self, ctrl: TeachingController) -> None:
        self.controller = ctrl

    def reset_runtime_state(self) -> None:
        if self._processing_task and not self._processing_task.done():
            self._processing_task.cancel()
        if self._hand_timeout_task and not self._hand_timeout_task.done():
            self._hand_timeout_task.cancel()
        self.agent = None
        self.controller = None
        self._voice_request_id = None
        self._processing_task = None
        self._current_response_id = None
        self._hand_timeout_task = None
        self._qa_in_progress = False
        self._hand_cancelled_during_qa = False
        self._follow_up_count = 0
        self._echo_cooldown_until = 0.0

    # -- Platform events -------------------------------------------------------

    async def on_session_init(self, session_id: str, user_id: str) -> None:
        logging.getLogger(__name__).info(
            "⬇️  session.init | %s %s", session_id, user_id
        )

    async def on_session_state(self, state: SessionState) -> None:
        if hasattr(state, "value"):
            if state.value == "IDLE":
                if self.controller:
                    self.controller.notify_platform_idle()
                    # If course ended and goodbye TTS finished, wait then close
                    if self.controller._course_ended.is_set():
                        logging.getLogger(__name__).info("📚 Goodbye TTS done — waiting 3s for follow-up")
                        ctrl = self.controller
                        # Give student 3s to raise hand after goodbye
                        for _ in range(6):  # 6 × 0.5s = 3s
                            await asyncio.sleep(0.5)
                            if ctrl.state == TeachingState.ANSWERING:
                                ctrl._course_ended.clear()
                                logging.getLogger(__name__).info("📚 Student interrupted goodbye — staying alive")
                                return
                        # No response — close session
                        ctrl.mark_course_closed()
                        if self.agent:
                            await self.agent.stop()
            elif self.controller and self.controller.state != TeachingState.ANSWERING:
                # Platform TTS active and student is NOT speaking — echo protection
                self._echo_cooldown_until = time.time() + 2.0

    async def on_session_closing(self, reason: str | None) -> None:
        logging.getLogger(__name__).info("⬇️  session.closing | %s", reason)

    async def on_error(self, code: str, message: str) -> None:
        logging.getLogger(__name__).error("⬇️  error | %s %s", code, message)

    async def on_audio_frame(self, frame) -> None:
        if self.asr_manager:
            self.asr_manager.feed_audio(frame.payload)

    async def on_closed(self, code: int, reason: str) -> None:
        logging.getLogger(__name__).info(
            "🔌 WS closed | %s %s", code, reason
        )

    # -- ASR callbacks with state-gating ---------------------------------------

    async def _on_speech_started(self) -> None:
        ctrl = self.controller
        if ctrl is None:
            return
        state = ctrl.state
        # IDLE / QUIZZING / QUIZ_RESULT / PROCESSING_INTER: never accept speech
        if state in (TeachingState.IDLE, TeachingState.QUIZZING, TeachingState.QUIZ_RESULT, TeachingState.PROCESSING_INTER):
            return
        # LECTURING: only accept speech if hand was explicitly raised
        if state == TeachingState.LECTURING:
            if not ctrl._hand_raised.is_set():
                return
        # ANSWERING / WAITING_INTERACT: student is already in interaction mode,
        # skip echo cooldown — speech is intentional
        elif state in (TeachingState.ANSWERING, TeachingState.WAITING_INTERACT):
            if state == TeachingState.WAITING_INTERACT:
                ctrl.cancel_interaction_timeout()
            pass  # Always accept
        # TRANSITIONING: echo protection still applies
        elif state == TeachingState.TRANSITIONING:
            if time.time() < self._echo_cooldown_until:
                return
        self._echo_cooldown_until = 0.0
        # Accept speech in ANSWERING, TRANSITIONING, and WAITING_INTERACT
        if self._qa_in_progress or self._processing_task:
            self._cancel_qa()
            logging.getLogger(__name__).info("⏹️ QA cancelled by new speech")
        await ctrl.stop_classmate_audio()
        # Cancel any running lecture task (from previous transition)
        if ctrl._task and not ctrl._task.done():
            ctrl._task.cancel()
        # Always interrupt. Start voice if not already started by handle_raise_hand
        await self.agent.send_interrupt()
        if not self._voice_request_id:
            self._voice_request_id = str(uuid.uuid4())
            await self.agent.send_voice_start(self._voice_request_id)
        # Ensure we're in ANSWERING state
        if state != TeachingState.WAITING_INTERACT:
            ctrl._state = TeachingState.ANSWERING
        logging.getLogger(__name__).info(
            "🔊 interrupt + voice.start (state=%s)", state.value
        )

    async def _on_speech_stopped(self) -> None:
        if self.agent and self._voice_request_id:
            await self.agent.send_voice_finish(self._voice_request_id)

    async def _on_asr_transcript(self, text: str) -> None:
        ctrl = self.controller
        agent = self.agent
        if ctrl is None or agent is None or time.time() < self._echo_cooldown_until:
            return
        # Drop implausibly short transcripts (VAD noise, TTS echo, ambient sounds)
        t = text.strip()
        if len(t) <= 1:
            logging.getLogger(__name__).debug(
                "🎤 ASR ignored (too short, %d chars): %r", len(t), t
            )
            return
        state = ctrl.state
        logging.getLogger(__name__).info(
            "🎤 ASR final [%s]: %s", state.value, text
        )
        if state in (TeachingState.ANSWERING, TeachingState.TRANSITIONING):
            # Cancel current QA to accept the new question
            self._cancel_qa()
            # Cancel any running lecture task
            if ctrl._task and not ctrl._task.done():
                ctrl._task.cancel()
            # Ensure ANSWERING state
            ctrl._state = TeachingState.ANSWERING
            if self._voice_request_id:
                await agent.send_asr_final(self._voice_request_id, text)
            if self.controller:
                self.controller.log_message("user", text)
            await self._handle_qa(text)
        elif state == TeachingState.WAITING_INTERACT:
            if self._voice_request_id:
                await agent.send_asr_final(self._voice_request_id, text)
            if self.controller:
                self.controller.log_message("user", text)
            await self._handle_interaction_response(text)

    async def _on_asr_interim(self, text: str) -> None:
        if self.agent and self._voice_request_id:
            await self.agent.send_asr_partial(
                self._voice_request_id, text, 0
            )

    async def _on_asr_error(self, code: str, message: str) -> None:
        if self.agent:
            await self.agent.send_error(code, message)

    # -- Q&A flow --------------------------------------------------------------

    def _cancel_qa(self) -> None:
        """Safely cancel in-progress QA flow and clean up state."""
        if self._processing_task and not self._processing_task.done():
            self._processing_task.cancel()
        self._qa_in_progress = False
        self._processing_task = None

    async def _handle_qa(self, text: str) -> None:
        ctrl = self.controller
        agent = self.agent
        if ctrl is None or agent is None or self.llm_client is None:
            return
        # Quick classification: "no more questions" or actual question?
        if await self._classify_intent(text):
            logging.getLogger(__name__).info("✅ Classified as confirmation — resuming lecture")
            ctrl.log_message("user", text)
            ack = "好的，那我们继续上课吧！"
            ctrl.log_message("agent", ack)
            await agent.send_prompt(ack)
            await ctrl.await_tts_idle(timeout=10.0)
            ctrl._state = TeachingState.LECTURING
            ctrl._task = asyncio.create_task(ctrl._resume_lecture())
            return
        # Normal QA flow
        self._follow_up_count = 0  # Reset: this is a fresh question
        self._qa_in_progress = True
        if ctrl._task and not ctrl._task.done():
            ctrl._task.cancel()
        if self._hand_timeout_task:
            self._hand_timeout_task.cancel()
            self._hand_timeout_task = None
        self._processing_task = asyncio.create_task(self._qa_flow(text))

    async def _classify_intent(self, text: str) -> bool:
        """LLM-based classification: does student want to stop Q&A?"""
        t = text.strip()
        classify_prompt = (
            "小朋友说了一句话。判断是想「继续提问」还是「结束问答」。\n"
            "只输出 QUESTION 或 RESUME。\n\n"
            "示例：\n"
            "- \"没有了\" → RESUME\n"
            "- \"继续吧\" → RESUME\n"
            "- \"没有问题了\" → RESUME\n"
            "- \"为什么\" → QUESTION\n"
            "- \"我没听懂\" → QUESTION\n\n"
            f"小朋友说：{t}"
        )
        try:
            self.llm_client.reset_context()
            self.llm_client._messages = [{"role": "user", "content": classify_prompt}]
            result = await self.llm_client.generate(classify_prompt, max_tokens=10)
            return "RESUME" in (result or "").upper()
        except Exception:
            return False

    async def _qa_flow(self, question: str) -> None:
        ctrl = self.controller
        agent = self.agent
        if ctrl is None or agent is None or self.llm_client is None:
            return
        logger = logging.getLogger(__name__)
        try:
            bp = ctrl._breakpoint
            chapter = (
                ctrl._cm.get_chapter(bp["chapter_id"]) if bp else None
            )
            chapter_title = chapter["title"] if chapter else "课程"
            qa_prompt = QA_SYSTEM_PROMPT.format(
                chapter_title=chapter_title
            )
            self.llm_client._system_prompt = qa_prompt
            self.llm_client._messages = [
                {"role": "system", "content": qa_prompt}
            ]
            # Clear stale TTS idle from interrupt, so we wait for THIS response
            ctrl._tts_idle.clear()
            response_id = str(uuid.uuid4())
            self._current_response_id = response_id
            await agent.send_response_start("qa", response_id)
            seq = 0

            async def on_chunk(delta):
                nonlocal seq
                await agent.send_response_chunk(
                    "qa", response_id, seq, int(time.time() * 1000), delta
                )
                seq += 1

            answer = await self.llm_client.generate_streaming(
                question, on_chunk, max_tokens=256
            )
            await agent.send_response_done("qa", response_id)
            self._echo_cooldown_until = time.time() + 1.5
            if answer and self.controller:
                self.controller.log_message("agent", answer)
            logger.info("✅ QA done: %s", answer[:80])
            # Wait for QA response TTS to finish
            await ctrl.await_tts_idle(timeout=15.0)

            # Brief pause before asking follow-up (separate from answer)
            await asyncio.sleep(1.5)
            # Ask if student has more questions (max 1 follow-up to avoid loops)
            if self._follow_up_count < 1:
                ctrl._tts_idle.clear()  # Wait for follow-up TTS, not stale idle
                self._follow_up_count += 1
                follow_up_prompt = "还有问题想问老师吗？没有的话我们继续上课哦～"
                ctrl.log_message("agent", follow_up_prompt)
                await agent.send_prompt(follow_up_prompt)
                await ctrl.await_tts_idle(timeout=15.0)

                # Wait for student response
                ctrl._state = TeachingState.ANSWERING
                ctrl._task = None
                # Clear _qa_in_progress and set echo protection during wait
                self._qa_in_progress = False
                self._echo_cooldown_until = time.time() + 0.5  # Brief cooldown for TTS tail
                for _ in range(8):  # 4s
                    await asyncio.sleep(0.5)
                    # Someone else started new QA or resume
                    if self._qa_in_progress or ctrl._task:
                        return
                # Timeout: no response — resume lecture
            else:
                self._follow_up_count = 0

            # Resume lecture
            if self._hand_cancelled_during_qa:
                self._hand_cancelled_during_qa = False
            ctrl._state = TeachingState.LECTURING
            ctrl._task = asyncio.create_task(ctrl._resume_lecture())
        except asyncio.CancelledError:
            logger.info("⏹️ QA cancelled")
        except Exception as e:
            logger.error("QA flow error: %s", e)
            await agent.send_prompt(
                "哎呀，老师需要想一想这个问题。我们先把刚才的内容学完，好不好？"
            )
        finally:
            self._qa_in_progress = False

    async def _generate_transition(self) -> None:
        ctrl = self.controller
        agent = self.agent
        if ctrl is None or agent is None or self.llm_client is None:
            return
        ctrl._state = TeachingState.TRANSITIONING
        try:
            bp = ctrl._breakpoint
            chapter = (
                ctrl._cm.get_chapter(bp["chapter_id"]) if bp else None
            )
            context = chapter["title"] if chapter else "课程内容"
            self.llm_client.reset_context()
            self.llm_client._system_prompt = LECTURE_SYSTEM_PROMPT
            self.llm_client._messages = [
                {"role": "system", "content": LECTURE_SYSTEM_PROMPT}
            ]
            transition = await self.llm_client.generate(
                TRANSITION_PROMPT.format(context=context), max_tokens=128
            )
            transition = (transition or "").strip() or "好啦，我们继续看下一个有趣的知识吧！"
            ctrl.log_message("agent", transition)
            await agent.send_prompt(transition)
            # Wait for transition TTS to finish before resuming lecture
            await ctrl.await_tts_idle(timeout=15.0)
        except Exception:
            fallback = "好啦，我们继续看下一个有趣的知识吧！"
            ctrl.log_message("agent", fallback)
            await agent.send_prompt(fallback)
            await ctrl.await_tts_idle(timeout=15.0)
        ctrl._state = TeachingState.LECTURING
        ctrl._task = asyncio.create_task(ctrl._resume_lecture())

    async def _handle_interaction_response(self, text: str) -> None:
        ctrl = self.controller
        agent = self.agent
        if ctrl is None or agent is None or self.llm_client is None:
            return
        chapter_id = ctrl._current_chapter_id
        ctrl.cancel_interaction_timeout()
        ctrl._state = TeachingState.PROCESSING_INTER
        try:
            chapter = (
                ctrl._cm.get_chapter(ctrl._current_chapter_id)
                if ctrl._current_chapter_id
                else None
            )
            question = (
                chapter["interaction"]["prompt"]
                if chapter and chapter.get("interaction")
                else "问题"
            )
            self.llm_client.reset_context()
            self.llm_client._system_prompt = LECTURE_SYSTEM_PROMPT
            self.llm_client._messages = [
                {"role": "system", "content": LECTURE_SYSTEM_PROMPT}
            ]
            feedback = await self.llm_client.generate(
                INTERACTION_FEEDBACK_PROMPT.format(
                    question=question, response=text
                ),
                max_tokens=200,
            )
            await agent.send_prompt(
                feedback or "谢谢你告诉老师！我们继续往下看吧～"
            )
        except Exception:
            await agent.send_prompt(
                "谢谢你告诉老师！我们继续往下看吧～"
            )
        if chapter_id or ctrl._current_chapter_id:
            await ctrl._finish_interaction_and_continue(chapter_id or ctrl._current_chapter_id)
        else:
            ctrl._state = TeachingState.LECTURING
