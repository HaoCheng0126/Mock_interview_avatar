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
PLATFORM_TIMEOUT = 15.0


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


def _platform_error(code: Any, message: str) -> str:
    text = message or ""
    low = text.lower()
    if any(k in low for k in ("publish", "shared")) or any(k in text for k in ("发布", "共享")):
        return (
            f"[{code}] {text}\n"
            "注意：若该 avatar 在控制台确为 Online/Public，此错误通常并非发布状态问题 —— "
            "经实测，真假 API Key、真假 avatar 都会返回此消息，说明该平台地址(base_url)下访问不到你的 avatar。"
            "请核对：① API Key 是否与该 avatar 属于同一账户；② 平台地址是否为该账户对应的环境。"
        )
    if code == 40004 or "unidentified" in low:
        return f"[{code}] API Key 无效或未激活，请检查平台 API Key"
    if code in (40005,):
        return f"[{code}] 并发会话已达上限，请关闭其他会话或升级套餐"
    if code in (40006,):
        return f"[{code}] 使用额度已用尽，请充值或切换环境"
    return f"[{code}] {text}"


async def check_platform_connection(settings: dict[str, Any]) -> dict[str, Any]:
    """Verify LiveAvatar credentials + avatar by actually calling /session/start
    (then stopping the session so it doesn't consume a concurrency slot)."""
    platform = settings.get("platform", {})
    if not platform.get("api_key"):
        return {"success": False, "error": "未填写平台 API Key"}
    if not platform.get("avatar_id"):
        return {"success": False, "error": "未填写 Avatar ID"}
    base_url = (platform.get("base_url") or "").rstrip("/")
    if not base_url:
        return {"success": False, "error": "未填写平台地址"}

    import aiohttp

    headers = {"Authorization": f"Bearer {platform['api_key']}", "Content-Type": "application/json"}
    if str(platform.get("sandbox", "")).strip().lower() in ("1", "true", "yes", "on"):
        headers["X-Env-Sandbox"] = "true"
    body = {"avatarId": platform["avatar_id"], "mode": "websocketAgent"}
    # Include the voice so the test validates the full config the session will use —
    # otherwise a Voice ID that isn't in the tenant only fails when a candidate starts.
    if platform.get("voice_id"):
        body["voiceId"] = platform["voice_id"]
    started = perf_counter()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/v1/session/start",
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=PLATFORM_TIMEOUT),
            ) as resp:
                data = await resp.json(content_type=None)
        code = data.get("code")
        if code == 0:
            session_id = (data.get("data") or {}).get("sessionId", "")
            if session_id:
                await _stop_platform_session(base_url, headers, session_id)
            return {"success": True, "latency_ms": round((perf_counter() - started) * 1000), "sessionId": session_id}
        return {"success": False, "error": _platform_error(code, data.get("message", ""))}
    except Exception as exc:  # noqa: BLE001 — surfaced to UI, not swallowed
        return {"success": False, "error": _friendly_error(exc)}


async def _stop_platform_session(base_url: str, headers: dict, session_id: str) -> None:
    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{base_url}/v1/session/stop",
                headers=headers,
                json={"sessionId": session_id},
                timeout=aiohttp.ClientTimeout(total=8),
            )
    except Exception:
        pass


async def check_llm_connection(settings: dict[str, Any]) -> dict[str, Any]:
    from hub.config_store import active_llm

    llm = active_llm(settings)
    if not llm.get("api_key"):
        provider_label = "火山方舟" if llm["provider"] == "volcengine" else "DeepSeek"
        return {"success": False, "error": f"未填写 {provider_label} API Key"}
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
            "provider": llm.get("provider", ""),
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
