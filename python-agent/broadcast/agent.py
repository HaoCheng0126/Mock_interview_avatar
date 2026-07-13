#!/usr/bin/env python3
"""E-commerce Live Broadcast Agent — queue-based product narration.

Architecture:
  HTTP server (aiohttp) — serves broadcast control API
  ProductManager          — YAML config loading, video/script selection
  ScriptGenerator         — LLM-driven script creation
  BroadcastController     — Queue engine + state machine
  LlmClient              — DeepSeek LLM for script generation and user replies
  AvatarAgent (SDK)      — WS communication + scene.switchVideo

Usage:
  export DEEPSEEK_API_KEY=sk-xxx
  export LIVEAVATAR_API_KEY=lk_live_xxx
  python broadcast/agent.py
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import sys
from pathlib import Path

from aiohttp import web

# Ensure project root is on sys.path for shared imports (llm_client etc.)
sys.path.insert(0, str(Path(__file__).parent.parent))

HERE = Path(__file__).parent
FRONTEND = HERE.parent.parent / "frontend"

_ws_patched = False
_scene_ready_hook: callable | None = None  # called when scene.ready received


def _patch_ws_client() -> None:
    global _ws_patched
    if _ws_patched:
        return
    _ws_patched = True
    from liveavatar_channel_sdk._ws_client import _AvatarWsClient as _Cls

    _orig_handle_text = _Cls._handle_text
    _orig_send_json = _Cls.send_json

    async def _patched_handle_text(self, raw: str) -> None:
        logging.getLogger(__name__).debug("🔽 RAW RECV: %s", raw[:600])
        # Hook: trigger broadcast start on scene.ready
        if _scene_ready_hook and '"event":"scene.ready"' in raw:
            logging.getLogger(__name__).info("🎬 scene.ready → starting broadcast")
            try:
                await _scene_ready_hook()
            except Exception as exc:
                logging.getLogger(__name__).error("scene.ready hook failed: %s", exc)
        await _orig_handle_text(self, raw)

    async def _patched_send_json(self, message: dict) -> None:
        logging.getLogger(__name__).debug(
            "🔼 RAW SEND: %s", _json.dumps(message, ensure_ascii=False)[:600]
        )
        # Use ensure_ascii=False so Chinese text stays as UTF-8 in the JSON,
        # not escaped as \uXXXX. The platform TTS needs readable Chinese.
        raw = _json.dumps(message, ensure_ascii=False)
        await self._ws.send(raw)

    _Cls._handle_text = _patched_handle_text
    _Cls.send_json = _patched_send_json


def json_response(data, **kwargs):
    """Like web.json_response but with ensure_ascii=False for readable Chinese."""
    return web.json_response(
        data, dumps=lambda obj: _json.dumps(obj, ensure_ascii=False), **kwargs
    )

from liveavatar_channel_sdk import AvatarAgent, AvatarAgentConfig, AgentListener

from broadcast.controller import BroadcastController
from llm_client import LlmClient
from broadcast.product_manager import ProductManager
from broadcast.script_generator import ScriptGenerator
from broadcast.tiktok_monitor import TikTokMonitor

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_KEY = os.getenv("LIVEAVATAR_API_KEY", "")
AVATAR_ID = os.getenv("LIVEAVATAR_AVATAR_ID", "")
BASE_URL = os.getenv(
    "LIVEAVATAR_BASE_URL", "https://liveavatar.aimiai.com/vih/dispatcher"
)
VOICE_ID = os.getenv("LIVEAVATAR_VOICE_ID", None)
HTTP_PORT = int(os.getenv("BROADCAST_HTTP_PORT", "8081"))

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

CONFIG_PATH = Path(
    os.getenv(
        "PRODUCTS_CONFIG_PATH",
        str(Path(__file__).parent.parent / "config" / "products.yaml"),
    )
)

DEFAULT_REPLY_SYSTEM_PROMPT = (
    "You are a friendly live shopping host on TikTok. "
    "Reply to viewer questions in short, enthusiastic English, "
    "under 50 words. Encourage purchases naturally."
)
DEFAULT_TIKTOK_SYSTEM_PROMPT = "You are an enthusiastic live host. Reply in short English."
DEFAULT_TIKTOK_COMMENT_TEMPLATE = (
    'Viewer "{user}" said: {text}. Reply in one short enthusiastic English '
    "sentence (under 25 words)."
)
DEFAULT_TIKTOK_JOIN_TEMPLATE = (
    'Welcome "{user}" to the live stream! One short enthusiastic English '
    "welcome (under 15 words)."
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging() -> None:
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(handler)
    for name in ("httpx", "httpcore", "asyncio", "aiohttp", "websockets"):
        logging.getLogger(name).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_agent: AvatarAgent | None = None
_controller: BroadcastController | None = None
_product_manager: ProductManager | None = None
_script_generator: ScriptGenerator | None = None
_llm_client: LlmClient | None = None
_tiktok_monitor: TikTokMonitor | None = None
_session_info: dict = {}


async def init_components() -> None:
    """Initialize shared components on server startup (no agent connection yet).

    The agent connection is created on-demand when the frontend calls /api/start-session.
    This keeps sessions fresh: each Connect = new room.
    """
    global _product_manager, _script_generator, _llm_client, _tiktok_monitor
    logger = logging.getLogger(__name__)

    _product_manager = ProductManager(CONFIG_PATH)
    products = _product_manager.get_products()
    logger.info("Loaded %d products from %s", len(products), CONFIG_PATH)

    _llm_client = LlmClient(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        model=DEEPSEEK_MODEL,
        system_prompt=_product_manager.get_persona_system_prompt(
            DEFAULT_REPLY_SYSTEM_PROMPT
        ),
    )

    _script_generator = ScriptGenerator(
        llm_client=_llm_client,
        prompt_template=_product_manager.get_prompt("script_template") or None,
        system_prompt=_product_manager.get_prompt("script_system_prompt") or None,
    )

    # ---- TikTok Live monitor (optional, independent of agent connection) ----
    live_url = _product_manager._settings.get("live_url", "")
    logger.info("🎵 live_url from config: %s", repr(live_url))
    if live_url:
        _tiktok_llm = LlmClient(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            model=DEEPSEEK_MODEL,
            system_prompt=_product_manager.get_prompt(
                "tiktok_system_prompt", DEFAULT_TIKTOK_SYSTEM_PROMPT
            ),
        )

        async def _on_tiktok_comment(user: str, text: str) -> None:
            if _controller:
                prompt = _product_manager.format_prompt(
                    "tiktok_comment_template",
                    DEFAULT_TIKTOK_COMMENT_TEMPLATE,
                    user=user,
                    text=text,
                )
                try:
                    _tiktok_llm.reset_context()
                    reply = await _tiktok_llm.generate(prompt, max_tokens=128)
                    if reply and reply.strip():
                        _controller.enqueue_message(reply.strip())
                except Exception as exc:
                    logger.error("TikTok comment reply failed: %s", exc)

        async def _on_tiktok_join(user: str) -> None:
            if _controller:
                prompt = _product_manager.format_prompt(
                    "tiktok_join_template",
                    DEFAULT_TIKTOK_JOIN_TEMPLATE,
                    user=user,
                )
                try:
                    _tiktok_llm.reset_context()
                    reply = await _tiktok_llm.generate(prompt, max_tokens=64)
                    if reply and reply.strip():
                        _controller.enqueue_message(reply.strip())
                except Exception as exc:
                    logger.error("TikTok join welcome failed: %s", exc)

        _tiktok_monitor = TikTokMonitor(
            live_url=live_url,
            on_comment=_on_tiktok_comment,
            on_join=_on_tiktok_join,
            comment_cooldown_s=_product_manager._settings.get("comment_cooldown_s", 30),
            join_cooldown_s=_product_manager._settings.get("join_cooldown_s", 120),
            settings=_product_manager._settings,
        )
        asyncio.create_task(_tiktok_monitor.start())
        logger.info("🎵 TikTok monitor enabled: %s", live_url)


async def start_broadcast_session() -> tuple[str, str]:
    """Start a fresh agent session. Called on Connect click via /api/start-session.

    Stops any existing session first to ensure a clean room.
    Returns (user_token, sfu_url) for the frontend SDK.
    """
    global _agent, _controller, _session_info, _scene_ready_hook
    logger = logging.getLogger(__name__)

    # Stop existing session if any
    await stop_broadcast_session()

    if _product_manager is None:
        raise RuntimeError("Components not initialized — server may not be ready")

    config = AvatarAgentConfig(
        api_key=API_KEY,
        avatar_id=AVATAR_ID,
        base_url=BASE_URL,
        developer_asr=False,
        developer_tts=False,
        voice_id=VOICE_ID,
        timeout=30.0,
    )
    _listener = _BroadcastListener()
    _agent = AvatarAgent(config, _listener)
    result = await _agent.start()
    logger.info("AvatarAgent connected — sessionId=%s", result.session_id)
    _session_info = {
        "userToken": result.user_token,
        "sfuUrl": result.sfu_url,
        "sessionId": result.session_id,
    }

    _controller = BroadcastController(
        agent=_agent,
        product_manager=_product_manager,
        llm_client=_llm_client,
        chunk_delay_ms=_product_manager._settings.get("chunk_delay_ms", 200),
        loop=_product_manager._settings.get("loop", True),
    )
    _listener.set_controller(_controller)

    # Auto-start broadcast when the frontend scene is ready
    async def _on_scene_ready() -> None:
        if _controller and _controller.state.value == "idle":
            await _controller.start()

    _scene_ready_hook = _on_scene_ready

    # Regenerate hook: background regeneration at 75% mark
    async def _regenerate(product_id: str) -> None:
        product = _product_manager.get_product(product_id)
        if not product:
            return
        lang = _product_manager._settings.get("lang", "zh")
        info = product.name
        if product.description:
            info += "\n" + product.description
        try:
            name, scripts = await _script_generator.generate(
                product_info=info, lang=lang,
            )
            if scripts:
                _product_manager.save_scripts(product.id, scripts, name=name or "")
                logger.info("🔄 Regenerated %d scripts for %s", len(scripts), product_id)
        except Exception as exc:
            logger.error("Regenerate failed for %s: %s", product_id, exc)

    _controller._regenerate_hook = _regenerate

    return result.user_token, result.sfu_url


async def stop_broadcast_session() -> None:
    """Stop the current agent session (controller + agent), keep components alive."""
    global _agent, _controller, _session_info
    if _controller is not None:
        try:
            await _controller.stop()
        except Exception:
            pass
        _controller = None
    if _agent is not None:
        try:
            await _agent.stop()
        except Exception:
            pass
        _agent = None
    _session_info = {}


async def shutdown_broadcast() -> None:
    """Full shutdown — stop session + TikTok monitor."""
    global _tiktok_monitor
    await stop_broadcast_session()
    if _tiktok_monitor is not None:
        await _tiktok_monitor.stop()
        _tiktok_monitor = None
    logging.getLogger(__name__).info("Broadcast shutdown complete")


class _BroadcastListener(AgentListener):
    """Listens for platform state changes, forwards IDLE to controller."""

    def __init__(self) -> None:
        self._controller_ref: list = []  # mutable container, set after controller creation

    def set_controller(self, controller) -> None:
        self._controller_ref.append(controller)

    async def on_session_state(self, state) -> None:
        if hasattr(state, 'value') and state.value == 'IDLE':
            if self._controller_ref:
                self._controller_ref[0].notify_platform_idle()


# ---------------------------------------------------------------------------
# HTTP Handlers
# ---------------------------------------------------------------------------


async def handle_broadcast_start(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response(
            {"success": False, "error": "Not initialized"}, status=500
        )
    await _controller.start()
    queue_len = (
        len(_product_manager.get_products()) if _product_manager else 0
    )
    return json_response({"success": True, "queueLength": queue_len})


async def handle_broadcast_stop(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response(
            {"success": False, "error": "Not initialized"}, status=500
        )
    await _controller.stop()
    return json_response({"success": True})


async def handle_broadcast_pause(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response(
            {"success": False, "error": "Not initialized"}, status=500
        )
    _controller.pause()
    return json_response({"success": True})


async def handle_broadcast_resume(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response(
            {"success": False, "error": "Not initialized"}, status=500
        )
    _controller.resume()
    return json_response({"success": True})


async def handle_broadcast_skip(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response(
            {"success": False, "error": "Not initialized"}, status=500
        )
    _controller.skip()
    return json_response({"success": True})


async def handle_broadcast_status(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response(
            {"success": False, "error": "Not initialized"}, status=500
        )
    return json_response(_controller.get_status())


async def handle_comment(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response(
            {"success": False, "error": "Not initialized"}, status=500
        )
    try:
        body = await request.json()
        text = body.get("text", "").strip()
    except Exception:
        return json_response(
            {"success": False, "error": "Invalid JSON body"}, status=400
        )

    if not text:
        return json_response(
            {"success": False, "error": "text is required"}, status=400
        )

    try:
        reply = await _controller.handle_comment(text)
        return json_response({"success": True, "reply": reply})
    except Exception as exc:
        logging.getLogger(__name__).error("handle_comment error: %s", exc)
        return json_response(
            {"success": False, "error": str(exc)}, status=500
        )


async def handle_product_generate(request: web.Request) -> web.Response:
    if _script_generator is None or _product_manager is None:
        return json_response(
            {"success": False, "error": "Not initialized"}, status=500
        )
    try:
        body = await request.json()
        url = body.get("url", "")
        product_id = body.get("productId", "")
        video_id = body.get("videoId", "")
        lang = body.get("lang", _product_manager._settings.get("lang", "zh"))
        explicit_info = body.get("productInfo", "")
    except Exception:
        return json_response(
            {"success": False, "error": "Invalid JSON body"}, status=400
        )

    if not product_id:
        return json_response(
            {"success": False, "error": "productId is required"}, status=400
        )

    # Build product_info: explicit > config description > URL scrape
    product_info = explicit_info
    if not product_info:
        product = _product_manager.get_product(product_id)
        if product and product.description:
            product_info = product.name + "\n" + product.description

    try:
        product_name, scripts = await _script_generator.generate(
            url=url, product_info=product_info, lang=lang,
        )
        if scripts and product_id:
            _product_manager.save_scripts(
                product_id, scripts,
                video_id=video_id,
                name=product_name or "",
            )
            _product_manager.reload()
        return json_response(
            {
                "success": True,
                "productName": product_name,
                "scriptsGenerated": len(scripts),
                "scripts": scripts,
            }
        )
    except ValueError as exc:
        return json_response({"success": False, "error": str(exc)}, status=404)
    except Exception as exc:
        logging.getLogger(__name__).error("generate error: %s", exc)
        return json_response(
            {"success": False, "error": str(exc)}, status=500
        )


async def handle_product_scripts(request: web.Request) -> web.Response:
    if _product_manager is None:
        return json_response(
            {"success": False, "error": "Not initialized"}, status=500
        )
    try:
        body = await request.json()
        product_id = body.get("productId", "")
        video_id = body.get("videoId", "")
        text = body.get("text", "")
    except Exception:
        return json_response(
            {"success": False, "error": "Invalid JSON body"}, status=400
        )

    if not product_id or not video_id or not text:
        return json_response(
            {"success": False, "error": "productId, videoId, and text are required"},
            status=400,
        )

    try:
        _product_manager.add_script(product_id, video_id, text)
        return json_response({"success": True})
    except ValueError as exc:
        return json_response({"success": False, "error": str(exc)}, status=404)


async def handle_product_reload(request: web.Request) -> web.Response:
    if _product_manager is None:
        return json_response(
            {"success": False, "error": "Not initialized"}, status=500
        )
    try:
        _product_manager.reload()
        count = len(_product_manager.get_products())
        return json_response({"success": True, "productCount": count})
    except Exception as exc:
        logging.getLogger(__name__).error("reload error: %s", exc)
        return json_response(
            {"success": False, "error": str(exc)}, status=500
        )


async def handle_session_info(request: web.Request) -> web.Response:
    return json_response(_session_info)


async def handle_start_session(request: web.Request) -> web.Response:
    """Start a fresh agent session and return connection info for the frontend SDK.

    Each call stops any existing session first, so every Connect = new room.
    """
    global _agent, _session_info
    logger = logging.getLogger(__name__)

    if _product_manager is None:
        return json_response(
            {"success": False, "error": "Server not ready — components not initialized"},
            status=503,
        )

    try:
        user_token, sfu_url = await start_broadcast_session()
    except Exception as e:
        logger.error("Failed to start session: %s", e)
        return json_response({"success": False, "error": str(e)}, status=500)

    return json_response({
        "success": True,
        "userToken": user_token,
        "sfuUrl": sfu_url,
        "sessionId": _agent.session_id if _agent else None,
    })


async def handle_stop_session(request: web.Request) -> web.Response:
    """Stop the current agent session (disconnect from platform)."""
    await stop_broadcast_session()
    return json_response({"success": True})


async def handle_index(request: web.Request) -> web.Response:
    html_path = FRONTEND / "broadcast.html"
    if not html_path.exists():
        return web.Response(text="broadcast.html not found", status=404)
    return web.Response(body=html_path.read_bytes(), content_type="text/html")


async def handle_sdk_js(request: web.Request) -> web.Response:
    js_path = (
        FRONTEND / "node_modules" / "@sanseng" / "liveavatar-js-sdk"
        / "dist" / "index.full.umd.js"
    )
    return web.Response(body=js_path.read_bytes(), content_type="application/javascript")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if not DEEPSEEK_API_KEY:
        print("⚠️  DEEPSEEK_API_KEY not set — LLM features won't work")
    if not API_KEY:
        print("⚠️  LIVEAVATAR_API_KEY not set")
        sys.exit(1)

    setup_logging()
    _patch_ws_client()
    logger = logging.getLogger(__name__)
    logger.info("Starting E-commerce Broadcast Agent on port %d", HTTP_PORT)

    app = web.Application()
    app.router.add_post("/api/broadcast/start", handle_broadcast_start)
    app.router.add_post("/api/broadcast/stop", handle_broadcast_stop)
    app.router.add_post("/api/broadcast/pause", handle_broadcast_pause)
    app.router.add_post("/api/broadcast/resume", handle_broadcast_resume)
    app.router.add_post("/api/broadcast/skip", handle_broadcast_skip)
    app.router.add_get("/api/broadcast/status", handle_broadcast_status)
    app.router.add_post("/api/comment", handle_comment)
    app.router.add_post("/api/product/generate", handle_product_generate)
    app.router.add_post("/api/product/scripts", handle_product_scripts)
    app.router.add_post("/api/product/reload", handle_product_reload)
    app.router.add_get("/api/session-info", handle_session_info)
    app.router.add_post("/api/start-session", handle_start_session)
    app.router.add_post("/api/stop-session", handle_stop_session)
    app.router.add_get("/", handle_index)
    app.router.add_get("/sdk.js", handle_sdk_js)

    app.on_startup.append(lambda _app: init_components())
    app.on_shutdown.append(lambda _app: shutdown_broadcast())

    web.run_app(app, host="0.0.0.0", port=HTTP_PORT)


if __name__ == "__main__":
    main()
