#!/usr/bin/env python3
"""Hub console — unified settings page + agent process manager.

Serves frontend/hub.html and a small REST API:

  GET  /api/config          → current settings (secrets masked)
  POST /api/config          → merge + persist settings
  GET  /api/agents          → status of all agents (running/external/stopped)
  POST /api/agents/start    → {"name": "chat"} spawn agent with settings as env
  POST /api/agents/stop     → {"name": "chat"} terminate managed agent
  GET  /api/agents/logs     → ?name=chat last stdout/stderr lines

Agents are spawned as child processes of this hub with the saved settings
injected as environment variables — no agent code changes required.
Binds 127.0.0.1 only: the console edits credentials and must stay local.

Usage:
  python hub/hub.py     # http://localhost:8000  (HUB_PORT to override)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections import deque
from pathlib import Path

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub.config_store import (  # noqa: E402
    AGENTS,
    apply_update,
    build_agent_env,
    load_settings,
    mask_settings,
    save_settings,
)
from hub.interview_config import (  # noqa: E402
    build_preview as build_interview_preview,
    read_config as read_interview_config,
    save_config as save_interview_config,
)
from hub.connection_test import (  # noqa: E402
    check_asr_connection,
    check_llm_connection,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("hub")

HERE = Path(__file__).resolve().parent
PYTHON_AGENT_DIR = HERE.parent
FRONTEND = PYTHON_AGENT_DIR.parent / "frontend"

HUB_PORT = int(os.getenv("HUB_PORT", "8000"))
LOG_LINES_MAX = 300
STARTUP_GRACE_S = 1.5
STOP_TIMEOUT_S = 5


class AgentProcess:
    """One managed agent child process + its recent output."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.proc: asyncio.subprocess.Process | None = None
        self.logs: deque[str] = deque(maxlen=LOG_LINES_MAX)
        self._reader: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self, settings: dict) -> None:
        spec = AGENTS[self.name]
        env = {**os.environ, **build_agent_env(settings, self.name)}
        self.logs.clear()
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            spec["script"],
            cwd=PYTHON_AGENT_DIR,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._reader = asyncio.create_task(self._read_output())
        logger.info("started %s (pid=%s)", self.name, self.proc.pid)

    async def _read_output(self) -> None:
        assert self.proc and self.proc.stdout
        async for line in self.proc.stdout:
            self.logs.append(line.decode("utf-8", errors="replace").rstrip())

    async def stop(self) -> None:
        if not self.is_running:
            return
        assert self.proc
        self.proc.terminate()
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=STOP_TIMEOUT_S)
        except asyncio.TimeoutError:
            self.proc.kill()
            await self.proc.wait()
        logger.info("stopped %s", self.name)


_managed: dict[str, AgentProcess] = {name: AgentProcess(name) for name in AGENTS}


async def _port_in_use(port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=0.4
        )
        writer.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


def _agent_port(settings: dict, name: str) -> int:
    return settings["agents"].get(name, {}).get("port", AGENTS[name]["default_port"])


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def index_handler(request: web.Request) -> web.Response:
    return web.Response(
        body=(FRONTEND / "hub.html").read_bytes(), content_type="text/html"
    )


async def get_config_handler(request: web.Request) -> web.Response:
    return web.json_response(mask_settings(load_settings()))


async def post_config_handler(request: web.Request) -> web.Response:
    try:
        incoming = await request.json()
        updated = apply_update(load_settings(), incoming)
    except ValueError as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)
    save_settings(updated)
    return web.json_response({"success": True, "config": mask_settings(updated)})


async def agents_status_handler(request: web.Request) -> web.Response:
    settings = load_settings()
    result = []
    for name, spec in AGENTS.items():
        managed = _managed[name]
        port = _agent_port(settings, name)
        if managed.is_running:
            status = "running"
        elif await _port_in_use(port):
            status = "external"
        else:
            status = "stopped"
        result.append(
            {
                "name": name,
                "label": spec["label"],
                "desc": spec["desc"],
                "port": port,
                "url": f"http://localhost:{port}",
                "status": status,
                "pid": managed.proc.pid if managed.is_running else None,
            }
        )
    return web.json_response(result)


def _known_agent_or_none(name: str | None) -> str | None:
    return name if name in AGENTS else None


async def start_agent_handler(request: web.Request) -> web.Response:
    body = await request.json()
    name = _known_agent_or_none(body.get("name"))
    if name is None:
        return web.json_response({"success": False, "error": "未知 agent"}, status=400)
    managed = _managed[name]
    if managed.is_running:
        return web.json_response({"success": False, "error": "已在运行中"}, status=409)
    settings = load_settings()
    port = _agent_port(settings, name)
    if await _port_in_use(port):
        return web.json_response(
            {"success": False, "error": f"端口 {port} 已被占用（可能已在 hub 外启动）"},
            status=409,
        )
    await managed.start(settings)
    await asyncio.sleep(STARTUP_GRACE_S)
    if not managed.is_running:
        tail = "\n".join(list(managed.logs)[-10:])
        return web.json_response(
            {"success": False, "error": f"启动即退出：\n{tail}"}, status=500
        )
    return web.json_response(
        {"success": True, "url": f"http://localhost:{port}", "pid": managed.proc.pid}
    )


async def stop_agent_handler(request: web.Request) -> web.Response:
    body = await request.json()
    name = _known_agent_or_none(body.get("name"))
    if name is None:
        return web.json_response({"success": False, "error": "未知 agent"}, status=400)
    if not _managed[name].is_running:
        return web.json_response(
            {"success": False, "error": "该 agent 不是由 hub 启动的，无法停止"}, status=409
        )
    await _managed[name].stop()
    return web.json_response({"success": True})


async def agent_logs_handler(request: web.Request) -> web.Response:
    name = _known_agent_or_none(request.query.get("name"))
    if name is None:
        return web.json_response({"success": False, "error": "未知 agent"}, status=400)
    return web.json_response({"success": True, "lines": list(_managed[name].logs)})


async def interview_config_page_handler(request: web.Request) -> web.Response:
    return web.Response(
        body=(FRONTEND / "hub-interview.html").read_bytes(), content_type="text/html"
    )


async def get_interview_config_handler(request: web.Request) -> web.Response:
    try:
        return web.json_response(read_interview_config())
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def post_interview_config_handler(request: web.Request) -> web.Response:
    try:
        incoming = await request.json()
        save_interview_config(incoming)
    except ValueError as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)
    return web.json_response({"success": True})


async def interview_config_js_handler(request: web.Request) -> web.Response:
    return web.Response(
        body=(FRONTEND / "hub-interview.js").read_bytes(),
        content_type="application/javascript",
    )


async def post_interview_preview_handler(request: web.Request) -> web.Response:
    try:
        incoming = await request.json()
        preview = build_interview_preview(incoming)
    except ValueError as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)
    return web.json_response({"success": True, **preview})


async def _merged_from_request(request: web.Request) -> dict:
    """Merge the (possibly masked) form payload onto saved settings so tests run
    with real secrets even before the user hits Save."""
    try:
        incoming = await request.json()
    except Exception:
        incoming = {}
    return apply_update(load_settings(), incoming)


async def test_llm_handler(request: web.Request) -> web.Response:
    result = await check_llm_connection(await _merged_from_request(request))
    return web.json_response(result)


async def test_asr_handler(request: web.Request) -> web.Response:
    result = await check_asr_connection(await _merged_from_request(request))
    return web.json_response(result)


async def _cleanup(app: web.Application) -> None:
    await asyncio.gather(*(m.stop() for m in _managed.values()))


def main() -> None:
    app = web.Application()
    app.router.add_get("/", index_handler)
    app.router.add_get("/api/config", get_config_handler)
    app.router.add_post("/api/config", post_config_handler)
    app.router.add_post("/api/config/test-llm", test_llm_handler)
    app.router.add_post("/api/config/test-asr", test_asr_handler)
    app.router.add_get("/api/agents", agents_status_handler)
    app.router.add_post("/api/agents/start", start_agent_handler)
    app.router.add_post("/api/agents/stop", stop_agent_handler)
    app.router.add_get("/api/agents/logs", agent_logs_handler)
    app.router.add_get("/interview-config", interview_config_page_handler)
    app.router.add_get("/interview-config.js", interview_config_js_handler)
    app.router.add_get("/api/interview-config", get_interview_config_handler)
    app.router.add_post("/api/interview-config", post_interview_config_handler)
    app.router.add_post("/api/interview-config/preview", post_interview_preview_handler)
    app.on_cleanup.append(_cleanup)
    logger.info("🎛  Hub console at http://localhost:%d", HUB_PORT)
    web.run_app(app, host="127.0.0.1", port=HUB_PORT)


if __name__ == "__main__":
    main()
