"""Tests for interview.volcano_asr — frame codec + result→callback mapping."""

import gzip
import json

import pytest
from unittest.mock import AsyncMock

from interview.volcano_asr import (
    VolcAsrManager,
    build_frame,
    parse_response,
)

_FULL_SERVER_RESPONSE = 0b1001
_SERVER_ERROR_RESPONSE = 0b1111
_AUDIO_ONLY_REQUEST = 0b0010


def _server_frame(payload_msg: dict) -> bytes:
    body = gzip.compress(json.dumps(payload_msg).encode("utf-8"))
    header = bytes([(1 << 4) | 1, (_FULL_SERVER_RESPONSE << 4), (1 << 4) | 1, 0])
    return header + len(body).to_bytes(4, "big") + body


def _error_frame(code: int, message: str) -> bytes:
    body = gzip.compress(json.dumps({"message": message}).encode("utf-8"))
    header = bytes([(1 << 4) | 1, (_SERVER_ERROR_RESPONSE << 4), (1 << 4) | 1, 0])
    return header + code.to_bytes(4, "big") + len(body).to_bytes(4, "big") + body


# ---------------------------------------------------------------------------
# codec
# ---------------------------------------------------------------------------


def test_build_frame_header_and_gzip_payload():
    frame = build_frame(_AUDIO_ONLY_REQUEST, b"raw-pcm-bytes")
    assert frame[0] == 0x11  # version 1 | header_size 1
    assert frame[1] >> 4 == _AUDIO_ONLY_REQUEST
    assert frame[2] == 0x11  # json | gzip
    size = int.from_bytes(frame[4:8], "big")
    body = frame[8:]
    assert len(body) == size
    assert gzip.decompress(body) == b"raw-pcm-bytes"


def test_parse_full_server_response_returns_json():
    out = parse_response(_server_frame({"code": 1000, "result": [{"text": "你好"}]}))
    assert out["message_type"] == _FULL_SERVER_RESPONSE
    assert out["payload_msg"]["result"][0]["text"] == "你好"


def test_parse_error_response_extracts_code():
    out = parse_response(_error_frame(45000001, "invalid token"))
    assert out["message_type"] == _SERVER_ERROR_RESPONSE
    assert out["code"] == 45000001
    assert out["payload_msg"]["message"] == "invalid token"


# ---------------------------------------------------------------------------
# result → callback mapping
# ---------------------------------------------------------------------------


def _manager():
    return VolcAsrManager(
        on_transcript=AsyncMock(),
        on_speech_started=AsyncMock(),
        on_speech_stopped=AsyncMock(),
        on_interim=AsyncMock(),
        on_error=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_interim_then_definite_drives_callbacks_in_order():
    m = _manager()

    # first non-empty partial -> speech_started + interim
    await m._handle_frame(_server_frame({"result": [{"text": "我在", "utterances": [
        {"text": "我在", "definite": False}]}]}))
    m._on_speech_started.assert_awaited_once()
    m._on_interim.assert_awaited()  # interim delivered
    m._on_transcript.assert_not_awaited()

    # definite utterance -> speech_stopped + transcript
    await m._handle_frame(_server_frame({"result": [{"text": "我在回答问题", "utterances": [
        {"text": "我在回答问题", "definite": True}]}]}))
    m._on_speech_stopped.assert_awaited_once()
    m._on_transcript.assert_awaited_once_with("我在回答问题")


@pytest.mark.asyncio
async def test_no_speech_code_is_ignored():
    m = _manager()
    await m._handle_frame(_server_frame({"code": 1013, "result": []}))
    m._on_speech_started.assert_not_awaited()
    m._on_transcript.assert_not_awaited()


@pytest.mark.asyncio
async def test_server_error_frame_reports_error():
    m = _manager()
    await m._handle_frame(_error_frame(45000151, "quota exhausted"))
    m._on_error.assert_awaited_once()
    args = m._on_error.await_args.args
    assert args[0] == "45000151"
    m._on_transcript.assert_not_awaited()
