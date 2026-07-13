#!/usr/bin/env python3
"""Live Avatar WebSocket Agent — Qwen ASR + DeepSeek LLM.

Architecture:
  HTTP server (aiohttp) — serves demo page + /api/start-session endpoint
  AvatarAgent (SDK)      — connects to platform WS, sends/receives protocol events
  Qwen ASR (DashScope)   — realtime speech recognition via WebSocket
  DeepSeek LLM           — text generation via OpenAI-compatible API

Flow:
  1. Browser loads index.html
  2. Browser calls POST /api/start-session → agent starts session + ASR connection
  3. Agent returns {userToken, sfuUrl} to browser; concurrently connects WS
  4. Browser uses JS SDK to join LiveKit room, avatar video renders
  5. User speaks → platform forwards PCM audio → on_audio_frame → Qwen ASR → text
  6. ASR transcript → DeepSeek LLM (stream) → response chunks → platform TTS → avatar speaks

Usage:
  export DASHSCOPE_API_KEY=sk-xxx
  export DEEPSEEK_API_KEY=sk-xxx
  python agent.py
  # Then open http://localhost:8080
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import time
import traceback
import uuid
from pathlib import Path

from aiohttp import web
from openai import AsyncOpenAI

from dashscope.audio.qwen_omni import (
    MultiModality,
    OmniRealtimeCallback,
    OmniRealtimeConversation,
)
from dashscope.audio.qwen_omni.omni_realtime import TranscriptionParams

from liveavatar_channel_sdk import (
    AgentListener,
    AvatarAgent,
    AvatarAgentConfig,
    SessionState,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_KEY = os.getenv("LIVEAVATAR_API_KEY", "")
AVATAR_ID = os.getenv("LIVEAVATAR_AVATAR_ID", "")
BASE_URL = os.getenv(
    "LIVEAVATAR_BASE_URL", "https://liveavatar.aimiai.com/vih/dispatcher"
)
VOICE_ID = os.getenv("LIVEAVATAR_VOICE_ID", None)
HTTP_PORT = int(os.getenv("HTTP_PORT", "8080"))

# DashScope Qwen ASR
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_ASR_MODEL = os.getenv("DASHSCOPE_ASR_MODEL", "qwen3-asr-flash-realtime")
DASHSCOPE_ASR_URL = os.getenv(
    "DASHSCOPE_ASR_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
)

# DeepSeek LLM
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "你是一个友好的数字人助手。用简洁的中文回答用户问题，保持对话自然流畅。回答控制在200字以内。",
)

# ---------------------------------------------------------------------------
# One-time WS client patch for raw message logging
# ---------------------------------------------------------------------------

_ws_patched = False

def _patch_ws_client() -> None:
    global _ws_patched
    if _ws_patched:
        return
    _ws_patched = True
    from liveavatar_channel_sdk._ws_client import _AvatarWsClient as _Cls
    _orig_handle_text = _Cls._handle_text
    _orig_send_json = _Cls.send_json

    async def _patched_handle_text(self, raw: str) -> None:
        log(f"🔽 RAW RECV: {raw[:600]}")
        await _orig_handle_text(self, raw)

    async def _patched_send_json(self, message: dict) -> None:
        log(f"🔼 RAW SEND: {json.dumps(message, ensure_ascii=False)[:600]}")
        await _orig_send_json(self, message)

    _Cls._handle_text = _patched_handle_text
    _Cls.send_json = _patched_send_json

# ---------------------------------------------------------------------------
# Shared log buffer
# ---------------------------------------------------------------------------

_log_lines: list[str] = []
MAX_LOG_LINES = 1000


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    _log_lines.append(line)
    if len(_log_lines) > MAX_LOG_LINES:
        _log_lines.pop(0)
    print(line)


def get_logs() -> list[str]:
    return list(_log_lines)


class SharedLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            log(self.format(record))
        except Exception:
            pass


def setup_logging() -> None:
    fmt = logging.Formatter("%(levelname)-7s | %(name)s | %(message)s")

    shared = SharedLogHandler()
    shared.setLevel(logging.DEBUG)
    shared.setFormatter(fmt)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(shared)
    root.addHandler(console)

    for name in ("httpx", "httpcore", "asyncio", "aiohttp", "websockets"):
        logging.getLogger(name).setLevel(logging.WARNING)

    for name in ("liveavatar_channel_sdk", "liveavatar_channel_sdk._ws_client"):
        logging.getLogger(name).setLevel(logging.DEBUG)


# ---------------------------------------------------------------------------
# Qwen ASR Manager
# ---------------------------------------------------------------------------

class QwenAsrManager:
    """Manages the Qwen ASR realtime WebSocket connection via DashScope SDK."""

    def __init__(self, *, on_transcript, on_speech_started=None, on_speech_stopped=None,
                 on_interim=None, on_error=None) -> None:
        self._on_transcript = on_transcript  # async callback(text: str)
        self._on_speech_started = on_speech_started
        self._on_speech_stopped = on_speech_stopped
        self._on_interim = on_interim  # async callback(text: str)
        self._on_error = on_error  # async callback(code: str, message: str)
        self._conversation: OmniRealtimeConversation | None = None
        self._callback: QwenAsrCallback | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reconnect_attempts = 0
        self._max_reconnect = 3
        self._closed = False

    async def connect(self) -> None:
        if not DASHSCOPE_API_KEY:
            raise RuntimeError(
                "DASHSCOPE_API_KEY is not set. "
                "Get your API key at https://bailian.console.aliyun.com/#/api-key"
            )

        self._loop = asyncio.get_running_loop()
        self._closed = False

        callback = QwenAsrCallback(
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

        transcription_params = TranscriptionParams(
            language="zh",
            sample_rate=16000,
            input_audio_format="pcm",
        )
        conversation.update_session(
            output_modalities=[MultiModality.TEXT],
            enable_turn_detection=True,
            turn_detection_type="server_vad",
            turn_detection_threshold=0.0,
            turn_detection_silence_duration_ms=400,
            enable_input_audio_transcription=True,
            transcription_params=transcription_params,
        )
        self._reconnect_attempts = 0
        log(f"🎤 Qwen ASR connected — model={DASHSCOPE_ASR_MODEL}")

    def feed_audio(self, pcm_bytes: bytes) -> None:
        conversation = self._conversation
        if conversation is None:
            return
        b64 = base64.b64encode(pcm_bytes).decode()
        conversation.append_audio(b64)

    async def close(self) -> None:
        self._closed = True
        conversation = self._conversation
        if conversation is not None:
            loop = self._loop or asyncio.get_running_loop()
            await loop.run_in_executor(None, conversation.close)
            self._conversation = None
            log("🎤 Qwen ASR disconnected")

    async def _on_asr_disconnect(self) -> None:
        if self._closed:
            return
        self._conversation = None
        await self._reconnect()

    async def _reconnect(self) -> bool:
        if self._closed:
            return False
        self._reconnect_attempts += 1
        if self._reconnect_attempts > self._max_reconnect:
            msg = f"Qwen ASR reconnect failed after {self._max_reconnect} attempts"
            log(f"🎤 {msg}")
            if self._on_error:
                try:
                    await self._on_error("ASR_DISCONNECT", msg)
                except Exception:
                    pass
            return False
        delay = 2 ** (self._reconnect_attempts - 1)
        log(f"🎤 Qwen ASR reconnecting in {delay}s (attempt {self._reconnect_attempts}/{self._max_reconnect})")
        await asyncio.sleep(delay)
        try:
            await self.connect()
            return True
        except Exception as e:
            log(f"🎤 Qwen ASR reconnect error: {e}")
            return False

    @property
    def is_connected(self) -> bool:
        return self._conversation is not None and not self._closed


class QwenAsrCallback(OmniRealtimeCallback):
    """Receives ASR events from the DashScope WebSocket thread."""

    def __init__(self, *, loop: asyncio.AbstractEventLoop, on_transcript, on_disconnect=None,
                 on_speech_started=None, on_speech_stopped=None, on_interim=None) -> None:
        super().__init__()
        self.conversation: OmniRealtimeConversation | None = None
        self._loop = loop
        self._on_transcript = on_transcript
        self._on_disconnect = on_disconnect
        self._on_speech_started = on_speech_started
        self._on_speech_stopped = on_speech_stopped
        self._on_interim = on_interim

    def on_open(self) -> None:
        log("🎤 Qwen ASR WebSocket opened")

    def on_close(self, code: int, msg: str) -> None:
        log(f"🎤 Qwen ASR WebSocket closed — code={code} msg={msg}")
        if self._on_disconnect:
            asyncio.run_coroutine_threadsafe(self._on_disconnect(), self._loop)

    def on_event(self, response: dict) -> None:
        try:
            event_type = response.get("type", "")
            if event_type == "session.created":
                sid = response.get("session", {}).get("id", "?")
                log(f"🎤 ASR session created: {sid[:20]}...")

            elif event_type == "conversation.item.input_audio_transcription.completed":
                transcript = response.get("transcript", "").strip()
                if transcript and self._on_transcript:
                    asyncio.run_coroutine_threadsafe(
                        self._on_transcript(transcript), self._loop
                    )

            elif event_type == "conversation.item.input_audio_transcription.text":
                stash = response.get("text", "")
                if stash:
                    log(f"🎤 [interim] {stash}")
                    if self._on_interim:
                        asyncio.run_coroutine_threadsafe(
                            self._on_interim(stash), self._loop
                        )

            elif event_type == "input_audio_buffer.speech_started":
                log("🎤 VAD: speech started")
                if self._on_speech_started:
                    asyncio.run_coroutine_threadsafe(self._on_speech_started(), self._loop)

            elif event_type == "input_audio_buffer.speech_stopped":
                log("🎤 VAD: speech stopped")
                if self._on_speech_stopped:
                    asyncio.run_coroutine_threadsafe(self._on_speech_stopped(), self._loop)

        except Exception:
            log(f"🎤 ASR callback error: {traceback.format_exc()}")


# ---------------------------------------------------------------------------
# DeepSeek LLM Client
# ---------------------------------------------------------------------------

class LlmClient:
    """Async LLM client for DeepSeek via OpenAI-compatible API."""

    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )
        self._model = DEEPSEEK_MODEL
        self._system_prompt = SYSTEM_PROMPT
        self._messages: list[dict] = [
            {"role": "system", "content": self._system_prompt}
        ]
        self._lock = asyncio.Lock()

    async def generate(self, user_text: str) -> str:
        self._messages.append({"role": "user", "content": user_text})

        # Keep context manageable — trim to last 20 messages + system prompt
        if len(self._messages) > 21:
            self._messages = [self._messages[0]] + self._messages[-20:]

        full_reply = ""

        # Streaming with retry
        for attempt in range(2):
            try:
                stream = await self._client.chat.completions.create(
                    model=self._model,
                    messages=self._messages,
                    stream=True,
                    max_tokens=512,
                    temperature=0.7,
                )
                async for chunk in stream:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        full_reply += delta.content
                break
            except Exception as e:
                log(f"🤖 LLM error (attempt {attempt + 1}): {e}")
                if attempt == 0:
                    await asyncio.sleep(1)
                else:
                    full_reply = "抱歉，我暂时无法回答，请稍后再试。"

        if full_reply.strip():
            self._messages.append({"role": "assistant", "content": full_reply})
        return full_reply

    async def generate_streaming(self, user_text: str, on_chunk) -> str:
        """Stream LLM output, calling on_chunk(text_delta) for each fragment.

        Returns the full reply text.
        """
        async with self._lock:
            self._messages.append({"role": "user", "content": user_text})
            if len(self._messages) > 21:
                self._messages = [self._messages[0]] + self._messages[-20:]

            full_reply = ""

            for attempt in range(2):
                try:
                    stream = await self._client.chat.completions.create(
                        model=self._model,
                        messages=self._messages,
                        stream=True,
                        max_tokens=512,
                        temperature=0.7,
                    )
                    async for chunk in stream:
                        delta = chunk.choices[0].delta
                        if delta.content:
                            full_reply += delta.content
                            await on_chunk(delta.content)
                    break
                except Exception as e:
                    log(f"🤖 LLM error (attempt {attempt + 1}): {e}")
                    if attempt == 0:
                        await asyncio.sleep(1)
                    else:
                        full_reply = "抱歉，我暂时无法回答，请稍后再试。"
                        await on_chunk(full_reply)

            if full_reply.strip():
                self._messages.append({"role": "assistant", "content": full_reply})
            return full_reply

    def reset_context(self) -> None:
        self._messages = [{"role": "system", "content": self._system_prompt}]


# ---------------------------------------------------------------------------
# Agent Listener
# ---------------------------------------------------------------------------

class DemoAgentListener(AgentListener):
    def __init__(
        self,
        agent: AvatarAgent | None = None,
        *,
        asr_manager: QwenAsrManager | None = None,
        llm_client: LlmClient | None = None,
    ) -> None:
        self.agent = agent
        self.asr_manager = asr_manager
        self.llm_client = llm_client
        self.conversation: list[dict] = []
        self._processing_lock = asyncio.Lock()
        self._current_task: asyncio.Task | None = None
        self._current_response_id: str | None = None
        self._voice_request_id: str | None = None
        self._echo_cooldown_until: float = 0.0   # ignore ASR until this timestamp

    # ---- Platform session events ----

    async def on_session_init(self, session_id: str, user_id: str) -> None:
        log(f"⬇️  session.init | sessionId={session_id} userId={user_id}")

    async def on_interrupt(self) -> None:
        """Handle control.interrupt from client — cancel current LLM stream."""
        log("⬇️  control.interrupt")
        task = self._current_task
        response_id = self._current_response_id
        if task is not None and not task.done():
            task.cancel()
            log(f"⏹️  LLM stream cancelled")
        if response_id and self.agent:
            await self.agent.send_response_cancel(response_id)
            log(f"⬆️  response.cancel | responseId={response_id}")

    async def on_text_input(self, text: str, request_id: str) -> None:
        """Platform ASR text input — fallback path."""
        log(f"⬇️  input.text | requestId={request_id!r} text={json.dumps(text, ensure_ascii=False)}")
        self.conversation.append({"role": "user", "text": text, "time": time.time()})
        await self._generate_and_send_reply(text, request_id)

    async def on_idle_trigger(self, reason: str, idle_time_ms: int) -> None:
        log(f"⬇️  system.idleTrigger | reason={reason} idle_time_ms={idle_time_ms}")
        await self.agent.send_prompt("你好，有什么想聊的吗？")

    async def on_session_state(self, state: SessionState) -> None:
        log(f"⬇️  session.state | state={state.value}")

    async def on_session_closing(self, reason: str | None) -> None:
        log(f"⬇️  session.closing | reason={reason}")

    async def on_error(self, code: str, message: str) -> None:
        log(f"⬇️  error | code={code} message={message}")

    async def on_audio_frame(self, frame) -> None:
        """Raw PCM audio from platform (Developer ASR mode). Feeds Qwen ASR."""
        if self.asr_manager is not None:
            self.asr_manager.feed_audio(frame.payload)

    async def on_closed(self, code: int, reason: str) -> None:
        log(f"🔌 WebSocket closed | code={code} reason={reason}")

    # ---- Internal ----

    async def _on_asr_error(self, code: str, message: str) -> None:
        """Called when ASR encounters a fatal error."""
        agent = self.agent
        if agent is not None:
            await agent.send_error(code, message)
            log(f"⬆️  error | code={code} message={message}")

    async def _on_asr_interim(self, text: str) -> None:
        """Called from ASR callback with partial/interim recognition result."""
        agent = self.agent
        request_id = self._voice_request_id
        if agent is None or request_id is None:
            return
        if time.time() < self._echo_cooldown_until:
            return
        # seq resets per voice session; we just use a simple counter
        seq = getattr(self, "_asr_partial_seq", 0)
        await agent.send_asr_partial(request_id, text, seq)
        self._asr_partial_seq = seq + 1
        log(f"⬆️  asr.partial | requestId={request_id} seq={seq} text={json.dumps(text, ensure_ascii=False)}")

    async def _on_speech_started(self) -> None:
        """Called when VAD detects speech start — cancel in-progress reply, then send voice.start."""
        agent = self.agent
        if agent is None:
            return

        # Echo cooldown: suppress false VAD triggers from avatar's own audio output
        if time.time() < self._echo_cooldown_until:
            log("🎤 VAD triggered during echo cooldown — ignoring")
            return

        # Per protocol Scenario 2B: cancel current response BEFORE voice.start
        task = self._current_task
        response_id = self._current_response_id
        if task is not None and not task.done():
            task.cancel()
            log("⏹️  LLM stream cancelled (new speech detected)")
        if response_id:
            await agent.send_response_cancel(response_id)

        self._voice_request_id = str(uuid.uuid4())
        self._asr_partial_seq = 0
        await agent.send_voice_start(self._voice_request_id)
        log(f"⬆️  voice.start | requestId={self._voice_request_id}")

    async def _on_speech_stopped(self) -> None:
        """Called when VAD detects speech end — send voice.finish to platform."""
        agent = self.agent
        if agent is None or self._voice_request_id is None:
            return
        await agent.send_voice_finish(self._voice_request_id)
        log(f"⬆️  voice.finish | requestId={self._voice_request_id}")

    async def _on_asr_transcript(self, text: str) -> None:
        """Called from ASR callback when a final transcript is available."""
        log(f"🎤 ASR final: {json.dumps(text, ensure_ascii=False)}")

        # Echo cooldown: suppress transcripts that arrive right after avatar spoke
        if time.time() < self._echo_cooldown_until:
            log("🎤 ASR transcript suppressed by echo cooldown")
            return

        self.conversation.append({"role": "user", "text": text, "time": time.time()})

        # Send ASR result to platform so it appears in the conversation
        agent = self.agent
        request_id = self._voice_request_id or str(uuid.uuid4())
        if agent is not None:
            await agent.send_asr_final(request_id, text)
            log(f"⬆️  asr.final | requestId={request_id} text={json.dumps(text, ensure_ascii=False)}")

        await self._generate_and_send_reply(text, request_id)

    async def _generate_and_send_reply(
        self, text: str, request_id: str | None = None
    ) -> None:
        """Call LLM, stream response chunks to platform TTS."""
        agent = self.agent
        if agent is None or self.llm_client is None:
            return

        if request_id is None:
            request_id = str(uuid.uuid4())

        async def _stream():
            async with self._processing_lock:
                log(f"🤖 LLM query: {json.dumps(text, ensure_ascii=False)}")

                response_id = str(uuid.uuid4())
                self._current_response_id = response_id
                seq = 0

                await agent.send_response_start(request_id, response_id)

                async def send_chunk(delta: str) -> None:
                    nonlocal seq
                    ts = int(time.time() * 1000)
                    await agent.send_response_chunk(request_id, response_id, seq, ts, delta)
                    seq += 1

                full_reply = await self.llm_client.generate_streaming(text, send_chunk)

                # If LLM returned the fallback error message, notify platform
                if "抱歉" in full_reply and "请稍后再试" in full_reply:
                    await agent.send_error("LLM_FAIL", full_reply, request_id)

                await agent.send_response_done(request_id, response_id)
                # Echo cooldown: ignore ASR for 1s after avatar stops speaking
                self._echo_cooldown_until = time.time() + 1.0
                log(f"✅ Response complete — responseId={response_id[:8]}... text={json.dumps(full_reply[:80], ensure_ascii=False)}")

                self.conversation.append({
                    "role": "agent", "text": full_reply, "time": time.time()
                })

        self._current_task = asyncio.create_task(_stream())
        try:
            await self._current_task
        except asyncio.CancelledError:
            log("⏹️  LLM generation cancelled by interrupt")


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_agent: AvatarAgent | None = None
_listener: DemoAgentListener | None = None
_asr_manager: QwenAsrManager | None = None
_llm_client: LlmClient | None = None


async def start_agent() -> tuple[str, str]:
    global _agent, _listener, _asr_manager, _llm_client

    _patch_ws_client()

    # Init LLM client
    _llm_client = LlmClient()

    # Init ASR manager if key is configured
    use_developer_asr = bool(DASHSCOPE_API_KEY)
    if use_developer_asr:
        _asr_manager = QwenAsrManager(on_transcript=None)  # set after listener created
    else:
        log("⚠️  DASHSCOPE_API_KEY not set — falling back to Platform ASR")
        _asr_manager = None

    # Init listener
    _listener = DemoAgentListener(None, asr_manager=_asr_manager, llm_client=_llm_client)
    if _asr_manager is not None:
        _asr_manager._on_transcript = _listener._on_asr_transcript
        _asr_manager._on_speech_started = _listener._on_speech_started
        _asr_manager._on_speech_stopped = _listener._on_speech_stopped
        _asr_manager._on_interim = _listener._on_asr_interim
        _asr_manager._on_error = _listener._on_asr_error
        await _asr_manager.connect()

    # Start platform session
    config = AvatarAgentConfig(
        api_key=API_KEY,
        avatar_id=AVATAR_ID,
        base_url=BASE_URL,
        developer_asr=use_developer_asr,
        developer_tts=False,
        voice_id=VOICE_ID,
        timeout=30.0,
    )

    _agent = AvatarAgent(config, _listener)
    _listener.agent = _agent

    log(f"🚀 POST /v1/session/start avatarId={AVATAR_ID}")
    result = await _agent.start()

    log(f"📋 sessionId: {result.session_id}")
    log(f"📋 sfuUrl: {result.sfu_url}")
    log(f"📋 agentWsUrl: {result.agent_ws_url}")
    log(f"📋 userToken prefix: {result.user_token[:30]}...")

    return result.user_token, result.sfu_url


async def stop_agent() -> None:
    global _agent, _asr_manager, _llm_client

    if _asr_manager is not None:
        await _asr_manager.close()
        _asr_manager = None

    if _llm_client is not None:
        _llm_client.reset_context()
        _llm_client = None

    if _agent is not None:
        log("🛑 Stopping agent...")
        await _agent.stop()
        _agent = None
        log("✅ Agent stopped")


# ---------------------------------------------------------------------------
# HTTP Handlers
# ---------------------------------------------------------------------------

# Ensure project root is on sys.path for shared imports
sys.path.insert(0, str(Path(__file__).parent.parent))

HERE = Path(__file__).parent
FRONTEND = HERE.parent.parent / "frontend"


async def index_handler(request: web.Request) -> web.Response:
    # Serve chat.html; fall back to index.html for backward compatibility
    html_path = FRONTEND / "chat.html"
    if not html_path.exists():
        html_path = FRONTEND / "index.html"
    return web.Response(body=html_path.read_bytes(), content_type="text/html")


async def js_sdk_handler(request: web.Request) -> web.Response:
    js_path = (
        FRONTEND
        / "node_modules"
        / "@sanseng"
        / "liveavatar-js-sdk"
        / "dist"
        / "index.full.umd.js"
    )
    return web.Response(body=js_path.read_bytes(), content_type="application/javascript")


async def start_session_handler(request: web.Request) -> web.Response:
    global _agent

    if _agent is not None and _agent.is_running:
        await stop_agent()

    try:
        user_token, sfu_url = await start_agent()
    except Exception as e:
        log(f"❌ Failed to start session: {e}")
        log(traceback.format_exc())
        return web.json_response({"success": False, "error": str(e)}, status=500)

    return web.json_response(
        {
            "success": True,
            "userToken": user_token,
            "sfuUrl": sfu_url,
            "sessionId": _agent.session_id if _agent else None,
        }
    )


async def stop_session_handler(request: web.Request) -> web.Response:
    await stop_agent()
    return web.json_response({"success": True})


async def interrupt_handler(request: web.Request) -> web.Response:
    global _listener
    if _listener is not None:
        await _listener.on_interrupt()
        return web.json_response({"success": True})
    return web.json_response({"success": False, "error": "no active session"}, status=400)


async def logs_handler(request: web.Request) -> web.Response:
    return web.json_response(get_logs())


async def clear_logs_handler(request: web.Request) -> web.Response:
    _log_lines.clear()
    log("🗑️  Logs cleared")
    return web.json_response({"success": True})


async def on_shutdown(app: web.Application) -> None:
    await stop_agent()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not DASHSCOPE_API_KEY:
        log("⚠️  DASHSCOPE_API_KEY not set — ASR won't work")
    if not DEEPSEEK_API_KEY:
        log("⚠️  DEEPSEEK_API_KEY not set — LLM won't work")

    setup_logging()

    app = web.Application()
    app.router.add_get("/", index_handler)
    app.router.add_get("/sdk.js", js_sdk_handler)
    app.router.add_post("/api/start-session", start_session_handler)
    app.router.add_post("/api/stop-session", stop_session_handler)
    app.router.add_post("/api/interrupt", interrupt_handler)
    app.router.add_get("/api/logs", logs_handler)
    app.router.add_post("/api/clear-logs", clear_logs_handler)
    app.on_shutdown.append(on_shutdown)

    log(f"🌐 Demo server at http://localhost:{HTTP_PORT}")
    web.run_app(app, host="0.0.0.0", port=HTTP_PORT)


if __name__ == "__main__":
    main()
