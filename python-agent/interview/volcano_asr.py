"""Volcengine (豆包语音) streaming ASR manager for interview sessions.

Implements the V2 small-model streaming protocol
(``wss://openspeech.bytedance.com/api/v2/asr``) and maps it onto the same
callback contract as :class:`interview.asr_manager.QwenAsrManager`
(``on_transcript`` / ``on_speech_started`` / ``on_speech_stopped`` /
``on_interim`` / ``on_error``; ``connect`` / ``feed_audio`` / ``close``).

Auth is ``Authorization: Bearer; {access_token}`` on the WS handshake, with
appid / cluster / token repeated in the first full-client-request JSON. The
Secret Key from the console is only used for signed REST endpoints, not this
WebSocket stream, so it is intentionally not read here.

Frame layout (4-byte header + payload-size + gzip payload):
  byte0: protocol_version(0b0001) << 4 | header_size(0b0001)
  byte1: message_type << 4 | message_type_flags
  byte2: serialization(JSON=0b0001) << 4 | compression(GZIP=0b0001)
  byte3: reserved
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import uuid

import websockets

logger = logging.getLogger(__name__)

VOLC_ASR_URL = os.getenv("VOLC_ASR_URL", "wss://openspeech.bytedance.com/api/v2/asr")
VOLC_APP_ID = os.getenv("VOLC_ASR_APP_ID", "")
VOLC_ACCESS_TOKEN = os.getenv("VOLC_ASR_ACCESS_TOKEN", "")
VOLC_CLUSTER = os.getenv("VOLC_ASR_CLUSTER", "volcengine_streaming_common")
VOLC_ASR_LANGUAGE = os.getenv("VOLC_ASR_LANGUAGE", "zh-CN")
AUDIO_SAMPLE_RATE = 16000

# protocol constants
_PROTO_VER = 0b0001
_HEADER_SIZE = 0b0001
_FULL_CLIENT_REQUEST = 0b0001
_AUDIO_ONLY_REQUEST = 0b0010
_FULL_SERVER_RESPONSE = 0b1001
_SERVER_ERROR_RESPONSE = 0b1111
_NO_SEQUENCE = 0b0000
_NEG_SEQUENCE = 0b0010
_JSON = 0b0001
_GZIP = 0b0001

# success + "no valid speech" server codes (ignore the latter, not an error)
_CODE_SUCCESS = 1000
_CODE_NO_SPEECH = 1013


def _header(message_type: int, flags: int = _NO_SEQUENCE) -> bytearray:
    h = bytearray(4)
    h[0] = (_PROTO_VER << 4) | _HEADER_SIZE
    h[1] = (message_type << 4) | flags
    h[2] = (_JSON << 4) | _GZIP
    h[3] = 0x00
    return h


def build_frame(message_type: int, payload: bytes, flags: int = _NO_SEQUENCE) -> bytes:
    """Header + 4-byte big-endian payload size + gzip(payload)."""
    body = gzip.compress(payload)
    frame = _header(message_type, flags)
    frame.extend(len(body).to_bytes(4, "big"))
    frame.extend(body)
    return bytes(frame)


def parse_response(res: bytes) -> dict:
    """Decode a server frame into ``{"message_type", "code"?, "payload_msg"?}``."""
    header_size = res[0] & 0x0F
    message_type = res[1] >> 4
    flags = res[1] & 0x0F
    compression = res[2] & 0x0F
    payload = res[header_size * 4:]
    out: dict = {"message_type": message_type}
    if flags & 0x01:  # positive sequence present (V3); skip it
        payload = payload[4:]
    if message_type == _FULL_SERVER_RESPONSE:
        payload = payload[4:]  # skip payload_size
    elif message_type == _SERVER_ERROR_RESPONSE:
        out["code"] = int.from_bytes(payload[:4], "big")
        payload = payload[8:]  # skip code(4) + size(4)
    else:
        return out
    if not payload:
        return out
    if compression == _GZIP:
        payload = gzip.decompress(payload)
    out["payload_msg"] = json.loads(payload.decode("utf-8"))
    return out


def build_request_payload(app_id: str, access_token: str, cluster: str) -> dict:
    """Full-client-request JSON for a 16k/mono/16bit raw PCM stream."""
    return {
        "app": {"appid": app_id, "cluster": cluster, "token": access_token},
        "user": {"uid": "interview"},
        "request": {
            "reqid": str(uuid.uuid4()),
            "workflow": "audio_in,resample,partition,vad,fe,decode,itn,nlu_punctuate",
            "show_utterances": True,
            "result_type": "single",
            "sequence": 1,
            "end_window_size": 200,
        },
        "audio": {
            "format": "raw",
            "rate": AUDIO_SAMPLE_RATE,
            "bits": 16,
            "channel": 1,
            "codec": "raw",
            "language": VOLC_ASR_LANGUAGE,
        },
    }


async def probe_connection(
    app_id: str,
    access_token: str,
    cluster: str,
    *,
    url: str = VOLC_ASR_URL,
    timeout: float = 10.0,
) -> None:
    """Verify credentials by opening the WS + one full-client-request round-trip.
    Raises on handshake/auth failure; returns None on success."""
    ws = await websockets.connect(
        url,
        additional_headers={"Authorization": f"Bearer; {access_token}"},
        ping_interval=None,
        open_timeout=timeout,
    )
    try:
        payload = build_request_payload(app_id, access_token, cluster)
        await ws.send(build_frame(_FULL_CLIENT_REQUEST, json.dumps(payload).encode("utf-8")))
        first = parse_response(await asyncio.wait_for(ws.recv(), timeout=timeout))
        code = first.get("payload_msg", {}).get("code", _CODE_SUCCESS)
        if code not in (_CODE_SUCCESS, _CODE_NO_SPEECH):
            message = first.get("payload_msg", {}).get("message", "handshake rejected")
            raise RuntimeError(f"[{code}] {message}")
    finally:
        await ws.close()


class VolcAsrManager:
    """Streaming recognizer that feeds interview audio to Volcengine ASR."""

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
        self._ws = None
        self._queue: asyncio.Queue | None = None
        self._send_task: asyncio.Task | None = None
        self._recv_task: asyncio.Task | None = None
        self._closed = False
        self._speaking = False

    def _request_payload(self) -> dict:
        return build_request_payload(VOLC_APP_ID, VOLC_ACCESS_TOKEN, VOLC_CLUSTER)

    async def connect(self) -> None:
        if not (VOLC_APP_ID and VOLC_ACCESS_TOKEN):
            raise RuntimeError("VOLC_ASR_APP_ID / VOLC_ASR_ACCESS_TOKEN are not set")
        self._closed = False
        self._speaking = False
        logger.debug("🎤 Volcengine ASR connecting to %s", VOLC_ASR_URL)
        self._ws = await websockets.connect(
            VOLC_ASR_URL,
            additional_headers={"Authorization": f"Bearer; {VOLC_ACCESS_TOKEN}"},
            ping_interval=None,
            max_size=10_000_000,
            open_timeout=10,
        )
        payload = self._request_payload()
        logger.debug("🎤 Volcengine ASR sending full client request: %s", payload)
        await self._ws.send(
            build_frame(
                _FULL_CLIENT_REQUEST,
                json.dumps(payload).encode("utf-8"),
            )
        )
        raw_first = await asyncio.wait_for(self._ws.recv(), timeout=10)
        logger.debug("🎤 Volcengine ASR first response raw: %d bytes", len(raw_first))
        first = parse_response(raw_first)
        logger.debug("🎤 Volcengine ASR first response parsed: %s", first)
        code = first.get("payload_msg", {}).get("code", _CODE_SUCCESS)
        if code not in (_CODE_SUCCESS, _CODE_NO_SPEECH):
            message = first.get("payload_msg", {}).get("message", "ASR handshake rejected")
            await self._ws.close()
            self._ws = None
            raise RuntimeError(f"Volcengine ASR rejected connection: [{code}] {message}")
        self._queue = asyncio.Queue()
        self._send_task = asyncio.create_task(self._sender())
        self._recv_task = asyncio.create_task(self._receiver())
        logger.info("🎤 Volcengine ASR connected (interview mode, cluster=%s)", VOLC_CLUSTER)

    def feed_audio(self, pcm_bytes: bytes) -> None:
        # Called synchronously from the same event loop as connect().
        if self._queue is not None and not self._closed:
            self._queue.put_nowait(pcm_bytes)

    async def _sender(self) -> None:
        try:
            while True:
                pcm = await self._queue.get()
                if pcm is None:
                    await self._ws.send(build_frame(_AUDIO_ONLY_REQUEST, b"", _NEG_SEQUENCE))
                    break
                await self._ws.send(build_frame(_AUDIO_ONLY_REQUEST, pcm))
        except (asyncio.CancelledError, websockets.ConnectionClosed):
            return
        except Exception:
            logger.exception("🎤 Volcengine ASR sender error")

    async def _receiver(self) -> None:
        try:
            async for raw in self._ws:
                await self._handle_frame(raw)
        except (asyncio.CancelledError, websockets.ConnectionClosed):
            return
        except Exception:
            logger.exception("🎤 Volcengine ASR receiver error")

    async def _handle_frame(self, raw: bytes) -> None:
        logger.debug("🎤 Volcengine ASR received frame: %d bytes", len(raw))
        parsed = parse_response(raw)
        logger.debug("🎤 Volcengine ASR parsed: %s", parsed)
        if parsed.get("message_type") == _SERVER_ERROR_RESPONSE:
            code = parsed.get("code", 0)
            message = parsed.get("payload_msg", {}).get("message", "")
            logger.error("🎤 Volcengine ASR error: code=%d message=%s", code, message)
            if self._on_error:
                await self._on_error(str(code), message)
            return
        msg = parsed.get("payload_msg") or {}
        if msg.get("code", _CODE_SUCCESS) == _CODE_NO_SPEECH:
            return
        results = msg.get("result") or []
        if not results:
            return
        head = results[0] if isinstance(results, list) else results
        text = (head.get("text") or "").strip()
        utterances = head.get("utterances") or []
        if text and not self._speaking:
            self._speaking = True
            if self._on_speech_started:
                await self._on_speech_started()
        if text and self._on_interim:
            logger.debug("🎤 Volcengine ASR interim: %s", text)
            await self._on_interim(text)
        for utterance in utterances:
            if not utterance.get("definite"):
                continue
            final_text = (utterance.get("text") or "").strip()
            logger.debug("🎤 Volcengine ASR final: %s", final_text)
            if self._on_speech_stopped:
                await self._on_speech_stopped()
            if final_text and self._on_transcript:
                await self._on_transcript(final_text)
            self._speaking = False

    async def close(self) -> None:
        self._closed = True
        if self._queue is not None:
            self._queue.put_nowait(None)
        for task in (self._send_task, self._recv_task):
            if task is None:
                continue
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=3)
            except Exception:
                task.cancel()
        self._send_task = self._recv_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
