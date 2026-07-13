"""Qwen ASR Manager — WebSocket-based real-time speech recognition for children."""

from __future__ import annotations

import asyncio
import base64
import logging
import traceback

from dashscope.audio.qwen_omni import (
    MultiModality,
    OmniRealtimeCallback,
    OmniRealtimeConversation,
)
from dashscope.audio.qwen_omni.omni_realtime import TranscriptionParams

from teaching.config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_ASR_MODEL,
    DASHSCOPE_ASR_URL,
)


class QwenAsrManager:
    def __init__(
        self,
        *,
        on_transcript=None,
        on_speech_started=None,
        on_speech_stopped=None,
        on_interim=None,
        on_error=None,
    ) -> None:
        self._on_transcript = on_transcript
        self._on_speech_started = on_speech_started
        self._on_speech_stopped = on_speech_stopped
        self._on_interim = on_interim
        self._on_error = on_error
        self._conversation: OmniRealtimeConversation | None = None
        self._callback: AsrCallback | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reconnect_attempts = 0
        self._max_reconnect = 3
        self._closed = False

    async def connect(self) -> None:
        if not DASHSCOPE_API_KEY:
            raise RuntimeError("DASHSCOPE_API_KEY is not set")
        self._loop = asyncio.get_running_loop()
        self._closed = False
        callback = AsrCallback(
            loop=self._loop,
            on_transcript=self._on_transcript,
            on_disconnect=self._on_asr_disconnect,
            on_speech_started=self._on_speech_started,
            on_speech_stopped=self._on_speech_stopped,
            on_interim=self._on_interim,
        )
        self._callback = callback
        conversation = OmniRealtimeConversation(
            model=DASHSCOPE_ASR_MODEL,
            url=DASHSCOPE_ASR_URL,
            api_key=DASHSCOPE_API_KEY,
            callback=callback,
        )
        callback.conversation = conversation
        self._conversation = conversation
        await self._loop.run_in_executor(None, conversation.connect)
        conversation.update_session(
            output_modalities=[MultiModality.TEXT],
            enable_turn_detection=True,
            turn_detection_type="server_vad",
            turn_detection_threshold=0.3,           # Catch softer child speech starts
            turn_detection_silence_duration_ms=800, # 1s for children's slower pace
            turn_detection_prefix_padding_ms=600,   # Keep speech that starts as VAD wakes up
            enable_input_audio_transcription=True,
            transcription_params=TranscriptionParams(
                language="zh", sample_rate=16000, input_audio_format="pcm"
            ),
        )
        self._reconnect_attempts = 0
        logging.getLogger(__name__).info(
            "🎤 Qwen ASR connected (child mode, silence=800ms, prefix=600ms)"
        )

    def feed_audio(self, pcm_bytes: bytes) -> None:
        if self._conversation:
            self._conversation.append_audio(
                base64.b64encode(pcm_bytes).decode()
            )

    async def close(self) -> None:
        self._closed = True
        if self._conversation:
            loop = self._loop or asyncio.get_running_loop()
            await loop.run_in_executor(None, self._conversation.close)
            self._conversation = None

    async def _on_asr_disconnect(self) -> None:
        if not self._closed:
            self._conversation = None
            await self._reconnect()

    async def _reconnect(self) -> bool:
        if self._closed:
            return False
        self._reconnect_attempts += 1
        if self._reconnect_attempts > self._max_reconnect:
            if self._on_error:
                await self._on_error("ASR_DISCONNECT", "ASR reconnect failed")
            return False
        await asyncio.sleep(2 ** (self._reconnect_attempts - 1))
        try:
            await self.connect()
            return True
        except Exception:
            return False


class AsrCallback(OmniRealtimeCallback):
    def __init__(
        self,
        *,
        loop,
        on_transcript,
        on_disconnect=None,
        on_speech_started=None,
        on_speech_stopped=None,
        on_interim=None,
    ) -> None:
        super().__init__()
        self.conversation: OmniRealtimeConversation | None = None
        self._loop = loop
        self._on_transcript = on_transcript
        self._on_disconnect = on_disconnect
        self._on_speech_started = on_speech_started
        self._on_speech_stopped = on_speech_stopped
        self._on_interim = on_interim

    def on_open(self) -> None:
        logging.getLogger(__name__).info("🎤 ASR WebSocket opened")

    def on_close(self, code: int, msg: str) -> None:
        logging.getLogger(__name__).info(
            "🎤 ASR closed -- code=%s msg=%s", code, msg
        )
        if self._on_disconnect:
            asyncio.run_coroutine_threadsafe(
                self._on_disconnect(), self._loop
            )

    def on_event(self, response: dict) -> None:
        try:
            et = response.get("type", "")
            if (
                et
                == "conversation.item.input_audio_transcription.completed"
            ):
                transcript = response.get("transcript", "").strip()
                if transcript and self._on_transcript:
                    asyncio.run_coroutine_threadsafe(
                        self._on_transcript(transcript), self._loop
                    )
            elif (
                et == "conversation.item.input_audio_transcription.text"
            ):
                stash = response.get("text", "")
                if stash and self._on_interim:
                    asyncio.run_coroutine_threadsafe(
                        self._on_interim(stash), self._loop
                    )
            elif et == "input_audio_buffer.speech_started":
                if self._on_speech_started:
                    asyncio.run_coroutine_threadsafe(
                        self._on_speech_started(), self._loop
                    )
            elif et == "input_audio_buffer.speech_stopped":
                if self._on_speech_stopped:
                    asyncio.run_coroutine_threadsafe(
                        self._on_speech_stopped(), self._loop
                    )
        except Exception:
            logging.getLogger(__name__).error(
                "🎤 ASR callback error: %s", traceback.format_exc()
            )
