from __future__ import annotations

import asyncio
import inspect
import logging
import math
import os
import time
import uuid
from array import array
from collections.abc import Awaitable, Callable

from liveavatar_channel_sdk import AgentListener

from interview.audio_resample import normalize_to_pcm16k_mono
from interview.models import InterviewState

logger = logging.getLogger(__name__)


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
        self._audio_frame_count = 0
        self._last_interim_text = ""
        self._warned_unfeedable = False
        self._speech_started_at = 0.0
        self._speech_stopped_pending = False
        self._pending_submit_task: asyncio.Task | None = None
        self._pending_submit_request_id: str | None = None
        self._pending_submit_texts: list[str] = []
        self._last_speech_activity_at = 0.0
        self._pending_speech_acceptance = False
        self._caption_sinks: set[
            Callable[[dict], Awaitable[None] | None]
        ] = set()
        self._direct_audio_input_enabled = False
        self._direct_capture_started_at = 0.0
        self._direct_first_audio_at = 0.0
        self._direct_first_interim_logged = False
        self._capture_exchange_id: str | None = None
        self._capture_total_samples = 0
        self._capture_voiced_samples = 0
        self._capture_peak_rms = 0.0
        try:
            self._submit_debounce_seconds = float(
                os.getenv("INTERVIEW_ASR_SUBMIT_DEBOUNCE_SECONDS", "0.35")
            )
        except Exception:
            self._submit_debounce_seconds = 0.35
        self._submit_debounce_seconds = min(5.0, max(0.2, self._submit_debounce_seconds))
        try:
            self._min_accept_chars = int(os.getenv("INTERVIEW_ASR_MIN_ACCEPT_CHARS", "2"))
        except Exception:
            self._min_accept_chars = 2
        self._min_accept_chars = min(12, max(1, self._min_accept_chars))
        try:
            self._voice_rms_threshold = float(
                os.getenv("INTERVIEW_ASR_VOICE_RMS_THRESHOLD", "450")
            )
        except Exception:
            self._voice_rms_threshold = 450.0
        try:
            self._min_voiced_ms = float(
                os.getenv("INTERVIEW_ASR_MIN_VOICED_MS", "120")
            )
        except Exception:
            self._min_voiced_ms = 120.0
        try:
            self._min_capture_ms = float(
                os.getenv("INTERVIEW_ASR_MIN_CAPTURE_MS", "180")
            )
        except Exception:
            self._min_capture_ms = 180.0

    def set_controller(self, controller) -> None:
        self.controller = controller

    def add_caption_sink(
        self, sink: Callable[[dict], Awaitable[None] | None]
    ) -> None:
        """Register a low-latency browser caption transport."""
        self._caption_sinks.add(sink)

    def remove_caption_sink(
        self, sink: Callable[[dict], Awaitable[None] | None]
    ) -> None:
        self._caption_sinks.discard(sink)

    async def _publish_caption(self, payload: dict) -> None:
        for sink in tuple(self._caption_sinks):
            try:
                result = sink(payload)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.debug("failed to publish direct ASR caption", exc_info=True)
                self._caption_sinks.discard(sink)

    def start_direct_audio_input(self, exchange_id: str | None = None) -> bool:
        """Start accepting browser-provided PCM16/16 kHz audio."""
        if not self._begin_capture(exchange_id):
            return False
        # The direct browser channel and the platform uplink must never feed the
        # recognizer at the same time, otherwise every word is duplicated.
        self.audio_input_enabled = False
        self._direct_audio_input_enabled = True
        self._direct_capture_started_at = time.monotonic()
        self._direct_first_audio_at = 0.0
        self._direct_first_interim_logged = False
        logger.info("direct ASR capture started: exchange_id=%s", self._capture_exchange_id)
        return True

    def feed_direct_audio(self, pcm_bytes: bytes) -> bool:
        """Feed one already-normalized PCM16/16 kHz packet to the ASR."""
        if (
            not self._direct_audio_input_enabled
            or self.asr_manager is None
            or not pcm_bytes
            or not self._capture_matches_current_exchange()
        ):
            return False
        self._record_pcm(pcm_bytes)
        if not self._direct_first_audio_at:
            self._direct_first_audio_at = time.monotonic()
            logger.info(
                "direct ASR first audio: capture_to_audio_ms=%s bytes=%s",
                round(
                    (self._direct_first_audio_at - self._direct_capture_started_at)
                    * 1000
                ),
                len(pcm_bytes),
            )
        self.asr_manager.feed_audio(pcm_bytes)
        return True

    def stop_direct_audio_input(self) -> None:
        # Do not reset the active utterance here: server VAD may still deliver the
        # final transcript after the last packet has been sent.
        self._direct_audio_input_enabled = False
        logger.info("direct ASR capture stopped")

    async def on_session_state(self, state) -> None:
        logger.info("platform session_state=%r", getattr(state, "value", state))
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
        self._audio_frame_count += 1
        if self._audio_frame_count % 200 == 1:
            logger.info(
                "audio_frame #%d enabled=%s asr=%s bytes=%d sr=%s codec=%s ch=%s state=%s",
                self._audio_frame_count,
                self.audio_input_enabled,
                self.asr_manager is not None,
                len(getattr(frame, "payload", b"") or b""),
                getattr(frame, "sample_rate", None),
                getattr(frame, "codec", None),
                getattr(frame, "channel", None),
                getattr(getattr(self.controller, "state", None), "value", None),
            )
        if (
            self.asr_manager is None
            or not self.audio_input_enabled
            or not self._can_accept_speech()
        ):
            return
        # Recognizers want 16 kHz mono PCM16; the platform may relay 24/48 kHz or
        # stereo. Convert first — feeding a mismatched rate produces no transcript.
        pcm = normalize_to_pcm16k_mono(
            frame.payload,
            sample_rate=getattr(frame, "sample_rate", 0),
            channel=getattr(frame, "channel", 0),
            codec=getattr(frame, "codec", 0),
        )
        if pcm is None:
            self._warn_unfeedable_frame(frame)
            return
        if pcm:
            self._record_pcm(pcm)
            if self._audio_frame_count % 200 == 1:
                logger.info("🎤 Feeding %d bytes to ASR manager", len(pcm))
            self.asr_manager.feed_audio(pcm)

    def _warn_unfeedable_frame(self, frame) -> None:
        """Log once when uplink audio can't be fed to the ASR (e.g. Opus)."""
        if self._warned_unfeedable:
            return
        self._warned_unfeedable = True
        logger.warning(
            "ASR cannot consume uplink audio: codec=%s sample_rate=%s "
            "(need PCM; Opus decoding is not built in). Voice answers will not register.",
            getattr(frame, "codec", None),
            getattr(frame, "sample_rate", None),
        )

    def set_audio_input_enabled(
        self, enabled: bool, exchange_id: str | None = None
    ) -> bool:
        if enabled:
            if not self._begin_capture(exchange_id):
                self.audio_input_enabled = False
                return False
            self._direct_audio_input_enabled = False
            self.audio_input_enabled = True
            return True
        self.audio_input_enabled = False
        if not enabled:
            self._reset_capture_state(clear_pending_submit=True)
        return True

    async def _on_speech_started(self) -> None:
        state = getattr(getattr(self.controller, "state", None), "value", None)
        can = self._can_buffer_speech()
        logger.info(
            "ASR speech_started: can_buffer=%s state=%s",
            can,
            state,
        )
        if not can or self.agent is None or not self._capture_matches_current_exchange():
            self._reset_capture_state()
            return
        self._cancel_pending_submit()
        self._accepted_speech = False
        self._pending_speech_acceptance = True
        self._voice_request_id = str(uuid.uuid4())
        self._asr_partial_seq = 0
        self._last_interim_text = ""
        self._speech_started_at = time.monotonic()
        self._speech_stopped_pending = False
        self._touch_speech_activity()

    async def _on_speech_stopped(self) -> None:
        if self._pending_speech_acceptance and not self._accepted_speech:
            self._speech_stopped_pending = True
            return
        if not self._accepted_speech or self.agent is None or not self._voice_request_id:
            return
        # Ignore overly eager speech_stopped events when the candidate has only just
        # started talking and we still have no usable transcript.
        elapsed = time.monotonic() - self._speech_started_at if self._speech_started_at else 0.0
        if elapsed < 1.2 and not self._last_interim_text:
            self._speech_stopped_pending = True
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
        transcript = text.strip()
        if not transcript or not self._capture_matches_current_exchange():
            return
        if not self._accepted_speech:
            if not await self._maybe_accept_pending_speech(transcript):
                return
        if self.agent is None or not self._voice_request_id:
            return
        # Volcengine streams the same partial dozens of times/sec; forwarding every one
        # floods the platform WS and lags the whole session. Only push on real change.
        if transcript == self._last_interim_text:
            return
        self._last_interim_text = transcript
        self._touch_speech_activity()
        if self._direct_first_audio_at and not self._direct_first_interim_logged:
            self._direct_first_interim_logged = True
            logger.info(
                "direct ASR first interim: audio_to_interim_ms=%s",
                round((time.monotonic() - self._direct_first_audio_at) * 1000),
            )
        await self._publish_caption(
            {
                "type": "interim",
                "text": transcript,
                "sequence": self._asr_partial_seq,
                "exchangeId": self._capture_exchange_id,
            }
        )
        if self._speech_stopped_pending and len(transcript) >= 2:
            self._speech_stopped_pending = False
            await self._on_speech_stopped()
            return
        await self.agent.send_asr_partial(
            self._voice_request_id,
            transcript,
            self._asr_partial_seq,
            metadata=self.controller.current_answer_metadata(),
        )
        self._asr_partial_seq += 1

    async def _on_asr_transcript(self, text: str) -> None:
        logger.info("ASR final (accepted=%s): %r", self._accepted_speech, text)
        transcript = text.strip()
        if len(transcript) <= 1 or not self._capture_matches_current_exchange():
            logger.info(
                "ASR final rejected as stale: capture_exchange=%s current_exchange=%s",
                self._capture_exchange_id,
                self._current_exchange_id(),
            )
            return
        if not self._accepted_speech:
            if not await self._maybe_accept_pending_speech(transcript):
                logger.info(
                    "ASR final ignored before acceptance: len=%s threshold=%s",
                    len(transcript),
                    self._min_accept_chars,
                )
                return
        if self.agent is None or not self._voice_request_id:
            return
        request_id = self._voice_request_id
        await self._publish_caption(
            {
                "type": "final",
                "text": transcript,
                "requestId": request_id,
                "exchangeId": self._capture_exchange_id,
            }
        )
        if self._direct_first_audio_at:
            logger.info(
                "direct ASR final: audio_to_final_ms=%s",
                round((time.monotonic() - self._direct_first_audio_at) * 1000),
            )
        await self.agent.send_asr_final(
            request_id,
            transcript,
            metadata=self.controller.current_answer_metadata(),
        )
        self._pending_submit_request_id = request_id
        self._pending_submit_texts.append(transcript)
        self._touch_speech_activity()
        self._schedule_debounced_submit()

    async def on_text_input(self, text: str, request_id: str) -> None:
        """Candidate answered by typing (platform Data Channel → input.text)."""
        if self.controller is None or not self._can_accept_speech():
            return
        answer = text.strip()
        if not answer:
            return
        # Drop any in-flight voice capture so a late ASR final can't submit a
        # second answer for the same exchange.
        self._reset_capture_state(clear_pending_submit=True)
        await self.controller.handle_answer(request_id, answer)

    def _can_accept_speech(self) -> bool:
        return (
            self.controller is not None
            and self.controller.state == InterviewState.LISTENING
        )

    def _can_buffer_speech(self) -> bool:
        return self._can_accept_speech()

    def _touch_speech_activity(self) -> None:
        self._last_speech_activity_at = time.monotonic()

    async def _maybe_accept_pending_speech(self, transcript: str) -> bool:
        if self.agent is None or self.controller is None or not self._voice_request_id:
            return False
        if self._accepted_speech:
            return True
        if not self._pending_speech_acceptance:
            return False
        if len(transcript.strip()) < self._min_accept_chars:
            return False
        if not self._capture_matches_current_exchange():
            logger.info(
                "ASR pending speech rejected: state=%s capture_exchange=%s current_exchange=%s",
                getattr(getattr(self.controller, "state", None), "value", None),
                self._capture_exchange_id,
                self._current_exchange_id(),
            )
            return False
        if not self._has_verified_voice():
            logger.info(
                "ASR pending speech rejected without verified PCM voice: "
                "total_ms=%s voiced_ms=%s peak_rms=%.1f",
                round(self._capture_total_samples / 16),
                round(self._capture_voiced_samples / 16),
                self._capture_peak_rms,
            )
            return False
        self._accepted_speech = True
        self._pending_speech_acceptance = False
        marker = getattr(self.controller, "mark_candidate_speaking", None)
        if marker is not None:
            result = marker()
            if inspect.isawaitable(result):
                await result
        await self.agent.send_voice_start(
            self._voice_request_id,
            metadata=self.controller.current_answer_metadata(),
        )
        logger.info(
            "ASR speech accepted: request_id=%s chars=%s",
            self._voice_request_id,
            len(transcript.strip()),
        )
        return True

    def _reset_capture_state(self, *, clear_pending_submit: bool = False) -> None:
        self._accepted_speech = False
        self._pending_speech_acceptance = False
        self._voice_request_id = None
        self._asr_partial_seq = 0
        self._last_interim_text = ""
        self._speech_started_at = 0.0
        self._speech_stopped_pending = False
        self._capture_exchange_id = None
        self._capture_total_samples = 0
        self._capture_voiced_samples = 0
        self._capture_peak_rms = 0.0
        if clear_pending_submit:
            self._cancel_pending_submit()
            self._pending_submit_request_id = None
            self._pending_submit_texts.clear()

    def _cancel_pending_submit(self) -> None:
        task = self._pending_submit_task
        if task is not None and not task.done():
            task.cancel()
        self._pending_submit_task = None

    def _schedule_debounced_submit(self) -> None:
        self._cancel_pending_submit()
        self._pending_submit_task = asyncio.create_task(self._debounced_submit())

    async def _debounced_submit(self) -> None:
        try:
            await asyncio.sleep(max(0.0, self._submit_debounce_seconds))
            if time.monotonic() - self._last_speech_activity_at < self._submit_debounce_seconds:
                return
            if not self._pending_submit_texts or self.controller is None:
                return
            exchange_id = self._capture_exchange_id
            if not exchange_id or exchange_id != self._current_exchange_id():
                logger.info(
                    "ASR answer submit rejected as stale: capture_exchange=%s current_exchange=%s",
                    exchange_id,
                    self._current_exchange_id(),
                )
                self._reset_capture_state(clear_pending_submit=True)
                return
            request_id = self._pending_submit_request_id or (self._voice_request_id or str(uuid.uuid4()))
            text = " ".join(self._pending_submit_texts).strip()
            self._pending_submit_texts.clear()
            self._pending_submit_request_id = None
            self._speech_stopped_pending = False
            await self.controller.handle_answer(
                request_id, text, expected_exchange_id=exchange_id
            )
            self._reset_capture_state()
        except asyncio.CancelledError:
            return

    def _begin_capture(self, exchange_id: str | None) -> bool:
        current_exchange_id = self._current_exchange_id()
        requested_exchange_id = str(exchange_id or current_exchange_id or "").strip()
        if (
            not self._can_accept_speech()
            or not current_exchange_id
            or requested_exchange_id != current_exchange_id
        ):
            logger.info(
                "audio capture rejected: requested_exchange=%s current_exchange=%s state=%s",
                requested_exchange_id,
                current_exchange_id,
                getattr(getattr(self.controller, "state", None), "value", None),
            )
            self._reset_capture_state(clear_pending_submit=True)
            return False
        self._reset_capture_state(clear_pending_submit=True)
        self._capture_exchange_id = current_exchange_id
        return True

    def _current_exchange_id(self) -> str:
        if self.controller is None:
            return ""
        getter = getattr(self.controller, "current_answer_metadata", None)
        if not callable(getter):
            return ""
        metadata = getter()
        if not isinstance(metadata, dict):
            return ""
        return str(metadata.get("exchangeId") or "")

    def _capture_matches_current_exchange(self) -> bool:
        return bool(
            self._can_accept_speech()
            and self._capture_exchange_id
            and self._capture_exchange_id == self._current_exchange_id()
        )

    def _record_pcm(self, pcm_bytes: bytes) -> None:
        usable = len(pcm_bytes) - (len(pcm_bytes) % 2)
        if usable <= 0:
            return
        samples = array("h")
        samples.frombytes(pcm_bytes[:usable])
        if not samples:
            return
        rms = math.sqrt(sum(int(sample) * int(sample) for sample in samples) / len(samples))
        self._capture_total_samples += len(samples)
        self._capture_peak_rms = max(self._capture_peak_rms, rms)
        if rms >= self._voice_rms_threshold:
            self._capture_voiced_samples += len(samples)

    def _has_verified_voice(self) -> bool:
        total_ms = self._capture_total_samples / 16.0
        voiced_ms = self._capture_voiced_samples / 16.0
        return bool(
            total_ms >= self._min_capture_ms
            and voiced_ms >= self._min_voiced_ms
            and self._capture_peak_rms >= self._voice_rms_threshold
        )
