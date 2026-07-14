"""Tests for hub.connection_test — provider dispatch, gating, friendly errors."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hub.connection_test import (
    _friendly_error,
    check_asr_connection,
    check_llm_connection,
)


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_empty_key_short_circuits():
    result = await check_llm_connection({"llm": {"api_key": ""}})
    assert result["success"] is False
    assert "API Key" in result["error"]


@pytest.mark.asyncio
async def test_llm_success(monkeypatch):
    create = AsyncMock(return_value=SimpleNamespace())

    class FakeClient:
        def __init__(self, **_kw):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

        async def close(self):
            pass

    monkeypatch.setattr("openai.AsyncOpenAI", FakeClient)
    result = await check_llm_connection(
        {"llm": {"api_key": "sk-x", "base_url": "https://api.deepseek.com", "model": "m"}}
    )
    assert result["success"] is True
    assert result["model"] == "m"
    assert "latency_ms" in result
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_llm_failure_is_friendly(monkeypatch):
    class FakeClient:
        def __init__(self, **_kw):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=AsyncMock(side_effect=Exception("Error code: 401 invalid api key"))
                )
            )

        async def close(self):
            pass

    monkeypatch.setattr("openai.AsyncOpenAI", FakeClient)
    result = await check_llm_connection({"llm": {"api_key": "sk-bad", "model": "m"}})
    assert result["success"] is False
    assert "凭证无效" in result["error"]


# ---------------------------------------------------------------------------
# ASR dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asr_volcengine_missing_creds():
    result = await check_asr_connection({"asr": {"provider": "volcengine", "volc_app_id": ""}})
    assert result["success"] is False
    assert "火山" in result["error"]


@pytest.mark.asyncio
async def test_asr_dashscope_missing_key():
    result = await check_asr_connection({"asr": {"provider": "dashscope", "dashscope_api_key": ""}})
    assert result["success"] is False
    assert "DashScope" in result["error"]


@pytest.mark.asyncio
async def test_asr_volcengine_success_dispatch(monkeypatch):
    seen = {}

    async def fake_probe(app_id, token, cluster, **_kw):
        seen["args"] = (app_id, token, cluster)

    monkeypatch.setattr("interview.volcano_asr.probe_connection", fake_probe)
    result = await check_asr_connection(
        {
            "asr": {
                "provider": "volcengine",
                "volc_app_id": "app1",
                "volc_access_token": "tok1",
                "volc_cluster": "clus1",
            }
        }
    )
    assert result == {"success": True, "provider": "volcengine"}
    assert seen["args"] == ("app1", "tok1", "clus1")


@pytest.mark.asyncio
async def test_asr_dashscope_success_dispatch(monkeypatch):
    monkeypatch.setattr("interview.asr_manager.probe_connection", AsyncMock())
    result = await check_asr_connection(
        {"asr": {"provider": "dashscope", "dashscope_api_key": "sk-a", "model": "qwen"}}
    )
    assert result == {"success": True, "provider": "dashscope"}


@pytest.mark.asyncio
async def test_asr_probe_failure_is_friendly(monkeypatch):
    async def boom(*_a, **_kw):
        raise Exception("Error code: 403 forbidden token")

    monkeypatch.setattr("interview.volcano_asr.probe_connection", boom)
    result = await check_asr_connection(
        {"asr": {"provider": "volcengine", "volc_app_id": "a", "volc_access_token": "t"}}
    )
    assert result["success"] is False
    assert "凭证无效" in result["error"]


# ---------------------------------------------------------------------------
# friendly error mapping
# ---------------------------------------------------------------------------


def test_friendly_error_maps_known_cases():
    assert "凭证无效" in _friendly_error(Exception("HTTP 401 Unauthorized"))
    assert "超时" in _friendly_error(Exception("connection timed out"))
    assert "无法连接" in _friendly_error(Exception("Connection refused"))
