from __future__ import annotations

import inspect
import uuid

from liveavatar_channel_sdk import AgentListener

from interview.models import InterviewState


class InterviewListener(AgentListener):
    def __init__(self, *, asr_manager=None) -> None:
        self.agent = None
        self.controller = None
        self.asr_manager = asr_manager
        self._voice_request_id: str | None = None
        self._asr_partial_seq = 0
        self._accepted_speech = False
        self.audio_input_enabled = False
        # scene.ready is a one-shot platform event. With session preheat the
        # interview may start minutes later, so remember it for replay.
        self.scene_ready_seen = False

    def set_controller(self, controller) -> None:
        self.controller = controller

    async def on_session_state(self, state) -> None:
        if self.controller is None:
            return
        notifier = getattr(self.controller, "notify_platform_state", None)
        if notifier is not None:
            await notifier(state)
        elif hasattr(state, "value") and state.value == "IDLE":
            await self.controller.notify_platform_idle()

    async def on_scene_ready(self) -> None:
        self.scene_ready_seen = True
        if self.controller is not None:
            await self.controller.mark_scene_ready()

    async def on_audio_frame(self, frame) -> None:
        if self.asr_manager is not None and self.audio_input_enabled:
            self.asr_manager.feed_audio(frame.payload)

    def set_audio_input_enabled(self, enabled: bool) -> None:
        self.audio_input_enabled = enabled
        if not enabled:
            self._accepted_speech = False
            self._voice_request_id = None

    async def _on_speech_started(self) -> None:
        if not self._can_accept_speech() or self.agent is None:
            self._accepted_speech = False
            return
        self._accepted_speech = True
        self._voice_request_id = str(uuid.uuid4())
        self._asr_partial_seq = 0
        marker = getattr(self.controller, "mark_candidate_speaking", None)
        if marker is not None:
            result = marker()
            if inspect.isawaitable(result):
                await result
        await self.agent.send_voice_start(
            self._voice_request_id,
            metadata=self.controller.current_answer_metadata(),
        )

    async def _on_speech_stopped(self) -> None:
        if not self._accepted_speech or self.agent is None or not self._voice_request_id:
            return
        await self.agent.send_voice_finish(
            self._voice_request_id,
            metadata=self.controller.current_answer_metadata(),
        )
        marker = getattr(self.controller, "mark_candidate_speech_stopped", None)
        if marker is not None:
            result = marker()
            if inspect.isawaitable(result):
                await result

    async def _on_asr_interim(self, text: str) -> None:
        if not self._accepted_speech or self.agent is None or not self._voice_request_id:
            return
        await self.agent.send_asr_partial(
            self._voice_request_id,
            text,
            self._asr_partial_seq,
            metadata=self.controller.current_answer_metadata(),
        )
        self._asr_partial_seq += 1

    async def _on_asr_transcript(self, text: str) -> None:
        if not self._accepted_speech or self.agent is None or not self._voice_request_id:
            return
        transcript = text.strip()
        if len(transcript) <= 1:
            return
        request_id = self._voice_request_id
        await self.agent.send_asr_final(
            request_id,
            transcript,
            metadata=self.controller.current_answer_metadata(),
        )
        await self.controller.handle_answer(request_id, transcript)
        self._accepted_speech = False
        self._voice_request_id = None

    async def on_text_input(self, text: str, request_id: str) -> None:
        """Candidate answered by typing (platform Data Channel → input.text)."""
        if self.controller is None or not self._can_accept_speech():
            return
        answer = text.strip()
        if not answer:
            return
        # Drop any in-flight voice capture so a late ASR final can't submit a
        # second answer for the same exchange.
        self._accepted_speech = False
        self._voice_request_id = None
        await self.controller.handle_answer(request_id, answer)

    def _can_accept_speech(self) -> bool:
        return (
            self.controller is not None
            and self.controller.state
            in {InterviewState.LISTENING, InterviewState.THINKING_CHECK}
        )
