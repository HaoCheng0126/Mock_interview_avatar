"""Qwen ASR manager for interview sessions."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import traceback

from dashscope.audio.qwen_omni import (
    MultiModality,
    OmniRealtimeCallback,
    OmniRealtimeConversation,
)
from dashscope.audio.qwen_omni.omni_realtime import TranscriptionParams

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_ASR_MODEL = os.getenv("DASHSCOPE_ASR_MODEL", "qwen3-asr-flash-realtime")
DASHSCOPE_ASR_URL = os.getenv(
    "DASHSCOPE_ASR_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
)

logger = logging.getLogger(__name__)


async def probe_connection(
    api_key: str,
    model: str | None = None,
    *,
    url: str | None = None,
    timeout: float = 10.0,
) -> None:
    """Verify a DashScope key by opening the realtime WS then closing it.
    Raises on auth/connection failure; returns None on success."""
    conversation = OmniRealtimeConversation(
        model=model or DASHSCOPE_ASR_MODEL,
        url=url or DASHSCOPE_ASR_URL,
        api_key=api_key,
        callback=OmniRealtimeCallback(),
    )
    loop = asyncio.get_running_loop()
    await asyncio.wait_for(loop.run_in_executor(None, conversation.connect), timeout=timeout)
    await loop.run_in_executor(None, conversation.close)


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
            turn_detection_threshold=0.3,
            turn_detection_silence_duration_ms=800,
            turn_detection_prefix_padding_ms=600,
            enable_input_audio_transcription=True,
            transcription_params=TranscriptionParams(
                language="zh", sample_rate=16000, input_audio_format="pcm"
            ),
        )
        self._reconnect_attempts = 0
        logger.info("🎤 Qwen ASR connected (interview mode, silence=800ms, prefix=600ms)")

    def feed_audio(self, pcm_bytes: bytes) -> None:
        if self._conversation:
            self._conversation.append_audio(base64.b64encode(pcm_bytes).decode())

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
        logger.info("🎤 Interview ASR WebSocket opened")

    def on_close(self, code: int, msg: str) -> None:
        logger.info("🎤 Interview ASR closed -- code=%s msg=%s", code, msg)
        if self._on_disconnect:
            asyncio.run_coroutine_threadsafe(self._on_disconnect(), self._loop)

    def on_event(self, response: dict) -> None:
        try:
            event_type = response.get("type", "")
            if event_type == "conversation.item.input_audio_transcription.completed":
                transcript = response.get("transcript", "").strip()
                if transcript and self._on_transcript:
                    asyncio.run_coroutine_threadsafe(
                        self._on_transcript(transcript), self._loop
                    )
            elif event_type == "conversation.item.input_audio_transcription.text":
                interim = response.get("text", "")
                if interim and self._on_interim:
                    asyncio.run_coroutine_threadsafe(
                        self._on_interim(interim), self._loop
                    )
            elif event_type == "input_audio_buffer.speech_started":
                if self._on_speech_started:
                    asyncio.run_coroutine_threadsafe(
                        self._on_speech_started(), self._loop
                    )
            elif event_type == "input_audio_buffer.speech_stopped":
                if self._on_speech_stopped:
                    asyncio.run_coroutine_threadsafe(
                        self._on_speech_stopped(), self._loop
                    )
        except Exception:
            logger.error("🎤 Interview ASR callback error: %s", traceback.format_exc())
