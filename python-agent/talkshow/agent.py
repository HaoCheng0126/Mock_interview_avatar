#!/usr/bin/env python3
"""Talk show digital human agent."""

from __future__ import annotations

import json as _json
import logging
import os
import sys
from pathlib import Path

from aiohttp import web

sys.path.insert(0, str(Path(__file__).parent.parent))

from liveavatar_channel_sdk import AgentListener, AvatarAgent, AvatarAgentConfig

from llm_client import LlmClient
from talkshow.controller import TalkshowController
from talkshow.script_generator import TalkshowScriptGenerator
from talkshow.show_manager import ShowManager

HERE = Path(__file__).parent
FRONTEND = HERE.parent.parent / "frontend"

API_KEY = os.getenv("LIVEAVATAR_API_KEY", "")
AVATAR_ID = os.getenv("LIVEAVATAR_AVATAR_ID", "")
BASE_URL = os.getenv(
    "LIVEAVATAR_BASE_URL", "https://liveavatar.aimiai.com/vih/dispatcher"
)
VOICE_ID = os.getenv("LIVEAVATAR_VOICE_ID", None)
HTTP_PORT = int(os.getenv("TALKSHOW_HTTP_PORT", "8082"))

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

CONFIG_PATH = Path(
    os.getenv(
        "TALKSHOW_CONFIG_PATH",
        str(Path(__file__).parent.parent / "config" / "talkshow.yaml"),
    )
)

_ws_patched = False
_scene_ready_hook: callable | None = None

_agent: AvatarAgent | None = None
_controller: TalkshowController | None = None
_show_manager: ShowManager | None = None
_script_generator: TalkshowScriptGenerator | None = None
_llm_client: LlmClient | None = None
_session_info: dict = {}


def _patch_ws_client() -> None:
    global _ws_patched
    if _ws_patched:
        return
    _ws_patched = True
    from liveavatar_channel_sdk._ws_client import _AvatarWsClient as _Cls

    _orig_handle_text = _Cls._handle_text

    async def _patched_handle_text(self, raw: str) -> None:
        logging.getLogger(__name__).debug("RAW RECV: %s", raw[:600])
        if _scene_ready_hook and '"event":"scene.ready"' in raw:
            try:
                await _scene_ready_hook()
            except Exception as exc:
                logging.getLogger(__name__).error("scene.ready hook failed: %s", exc)
        await _orig_handle_text(self, raw)

    async def _patched_send_json(self, message: dict) -> None:
        logging.getLogger(__name__).debug(
            "RAW SEND: %s", _json.dumps(message, ensure_ascii=False)[:600]
        )
        raw = _json.dumps(message, ensure_ascii=False)
        await self._ws.send(raw)

    _Cls._handle_text = _patched_handle_text
    _Cls.send_json = _patched_send_json


def json_response(data, **kwargs):
    return web.json_response(
        data, dumps=lambda obj: _json.dumps(obj, ensure_ascii=False), **kwargs
    )


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


async def init_components() -> None:
    global _show_manager, _script_generator, _llm_client

    _llm_client = LlmClient(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        model=DEEPSEEK_MODEL,
        system_prompt="你是一个专业脱口秀编剧。输出自然、口语化、有节目感的中文内容。",
    )
    _show_manager = ShowManager(CONFIG_PATH)
    _script_generator = TalkshowScriptGenerator(_llm_client)


async def start_talkshow_session() -> tuple[str, str]:
    global _agent, _controller, _session_info, _scene_ready_hook

    await stop_talkshow_session()
    if _show_manager is None or _script_generator is None:
        raise RuntimeError("Components not initialized")

    config = _build_avatar_config(_show_manager)
    listener = _TalkshowListener()
    _agent = AvatarAgent(config, listener)
    result = await _agent.start()

    _session_info = {
        "userToken": result.user_token,
        "sfuUrl": result.sfu_url,
        "sessionId": result.session_id,
    }

    _controller = TalkshowController(_agent, _show_manager, _script_generator)
    listener.set_controller(_controller)

    async def _on_scene_ready() -> None:
        if _controller and _controller.state.value == "idle":
            await _controller.start()

    _scene_ready_hook = _on_scene_ready
    return result.user_token, result.sfu_url


def _build_avatar_config(show_manager: ShowManager) -> AvatarAgentConfig:
    return AvatarAgentConfig(
        api_key=API_KEY,
        avatar_id=AVATAR_ID,
        base_url=BASE_URL,
        developer_asr=False,
        developer_tts=False,
        voice_id=VOICE_ID,
        voice_config=show_manager.voice_config,
        timeout=30.0,
    )


async def stop_talkshow_session() -> None:
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


class _TalkshowListener(AgentListener):
    def __init__(self) -> None:
        self._controller: TalkshowController | None = None

    def set_controller(self, controller: TalkshowController) -> None:
        self._controller = controller

    async def on_session_state(self, state) -> None:
        if hasattr(state, "value") and state.value == "IDLE" and self._controller:
            self._controller.notify_platform_idle()


async def handle_index(request: web.Request) -> web.Response:
    return web.FileResponse(FRONTEND / "talkshow.html")


async def handle_start_session(request: web.Request) -> web.Response:
    try:
        user_token, sfu_url = await start_talkshow_session()
        return json_response(
            {
                "success": True,
                "userToken": user_token,
                "sfuUrl": sfu_url,
                "sessionId": _session_info.get("sessionId", ""),
            }
        )
    except Exception as exc:
        logging.getLogger(__name__).error("start-session failed: %s", exc)
        return json_response({"success": False, "error": str(exc)}, status=500)


async def handle_stop_session(request: web.Request) -> web.Response:
    await stop_talkshow_session()
    return json_response({"success": True})


async def handle_session_info(request: web.Request) -> web.Response:
    return json_response(_session_info)


async def handle_talkshow_start(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response({"success": False, "error": "Not initialized"}, status=500)
    await _controller.start()
    return json_response({"success": True})


async def handle_talkshow_stop(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response({"success": False, "error": "Not initialized"}, status=500)
    await _controller.stop()
    return json_response({"success": True})


async def handle_talkshow_pause(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response({"success": False, "error": "Not initialized"}, status=500)
    _controller.pause()
    return json_response({"success": True})


async def handle_talkshow_resume(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response({"success": False, "error": "Not initialized"}, status=500)
    _controller.resume()
    return json_response({"success": True})


async def handle_talkshow_skip(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response({"success": False, "error": "Not initialized"}, status=500)
    _controller.skip()
    return json_response({"success": True})


async def handle_talkshow_status(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response({"success": False, "error": "Not initialized"}, status=500)
    return json_response(_controller.get_status())


async def handle_talkshow_generate(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response({"success": False, "error": "Not initialized"}, status=500)
    try:
        batch = await _controller.generate_next_batch()
        return json_response(
            {"success": True, "segmentsGenerated": len(batch.segments)}
        )
    except Exception as exc:
        return json_response({"success": False, "error": str(exc)}, status=500)


async def handle_talkshow_reload(request: web.Request) -> web.Response:
    if _show_manager is None:
        return json_response({"success": False, "error": "Not initialized"}, status=500)
    try:
        _show_manager.reload()
        return json_response(
            {"success": True, "topicCount": len(_show_manager.get_topics())}
        )
    except Exception as exc:
        return json_response({"success": False, "error": str(exc)}, status=500)


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_post("/api/start-session", handle_start_session)
    app.router.add_post("/api/stop-session", handle_stop_session)
    app.router.add_get("/api/session-info", handle_session_info)
    app.router.add_post("/api/talkshow/start", handle_talkshow_start)
    app.router.add_post("/api/talkshow/stop", handle_talkshow_stop)
    app.router.add_post("/api/talkshow/pause", handle_talkshow_pause)
    app.router.add_post("/api/talkshow/resume", handle_talkshow_resume)
    app.router.add_post("/api/talkshow/skip", handle_talkshow_skip)
    app.router.add_get("/api/talkshow/status", handle_talkshow_status)
    app.router.add_post("/api/talkshow/generate", handle_talkshow_generate)
    app.router.add_post("/api/talkshow/reload", handle_talkshow_reload)
    app.router.add_static("/", FRONTEND, show_index=False)
    app.on_startup.append(lambda app: init_components())
    app.on_shutdown.append(lambda app: stop_talkshow_session())
    return app


def main() -> None:
    setup_logging()
    _patch_ws_client()
    web.run_app(create_app(), host="0.0.0.0", port=HTTP_PORT)


if __name__ == "__main__":
    main()
