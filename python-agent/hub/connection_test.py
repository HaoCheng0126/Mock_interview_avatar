"""Connection self-tests for the hub console.

Given a (already secret-merged) settings dict, verify the LLM endpoint and the
selected ASR provider are reachable with the configured credentials. Each
returns a JSON-friendly dict: ``{"success": bool, ...}`` — never raises, so the
handler can surface a friendly message instead of a 500.
"""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

LLM_TIMEOUT = 15.0
ASR_TIMEOUT = 12.0


def _friendly_error(exc: Exception) -> str:
    msg = str(exc) or exc.__class__.__name__
    low = msg.lower()
    if any(k in low for k in ("401", "403", "unauthor", "authenticat", "invalid api key", "invalid token", "forbidden")):
        return "凭证无效或未授权 — 请检查 API Key / Token"
    if any(k in low for k in ("timeout", "timed out")):
        return "连接超时 — 请检查网络或服务地址"
    if any(k in low for k in ("not found", "404", "no such model", "model")):
        return f"服务地址或模型可能有误：{msg[:160]}"
    if any(k in low for k in ("connect", "resolve", "name or service", "getaddrinfo", "refused")):
        return f"无法连接到服务地址：{msg[:160]}"
    return msg[:200]


async def check_llm_connection(settings: dict[str, Any]) -> dict[str, Any]:
    llm = settings.get("llm", {})
    if not llm.get("api_key"):
        return {"success": False, "error": "未填写 LLM API Key"}
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=llm["api_key"],
        base_url=llm.get("base_url") or None,
        _enforce_credentials=True,
    )
    started = perf_counter()
    try:
        await asyncio.wait_for(
            client.chat.completions.create(
                model=llm.get("model") or "",
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            ),
            timeout=LLM_TIMEOUT,
        )
        return {
            "success": True,
            "latency_ms": round((perf_counter() - started) * 1000),
            "model": llm.get("model", ""),
        }
    except Exception as exc:  # noqa: BLE001 — surfaced to UI, not swallowed
        return {"success": False, "error": _friendly_error(exc)}
    finally:
        try:
            await client.close()
        except Exception:
            pass


async def check_asr_connection(settings: dict[str, Any]) -> dict[str, Any]:
    asr = settings.get("asr", {})
    provider = asr.get("provider", "dashscope")
    try:
        if provider == "volcengine":
            if not (asr.get("volc_app_id") and asr.get("volc_access_token")):
                return {"success": False, "error": "未填写火山引擎 APP ID / Access Token"}
            from interview.volcano_asr import probe_connection

            await probe_connection(
                asr["volc_app_id"],
                asr["volc_access_token"],
                asr.get("volc_cluster") or "volcengine_streaming_common",
                timeout=ASR_TIMEOUT,
            )
        else:
            if not asr.get("dashscope_api_key"):
                return {"success": False, "error": "未填写 DashScope API Key"}
            from interview.asr_manager import probe_connection

            await probe_connection(
                asr["dashscope_api_key"], asr.get("model") or None, timeout=ASR_TIMEOUT
            )
        return {"success": True, "provider": provider}
    except Exception as exc:  # noqa: BLE001 — surfaced to UI, not swallowed
        return {"success": False, "error": _friendly_error(exc)}
