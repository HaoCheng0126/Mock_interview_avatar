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

import aiohttp.web_protocol as aiohttp_web_protocol
import yaml
from aiohttp import web


# Python 3.14 + aiohttp 3.13 may raise EINVAL while enabling keepalive on a
# localhost socket.  Without this guard the Hub keeps listening on port 8000
# but every new browser/API request hangs in connection_made().
_aiohttp_tcp_keepalive = aiohttp_web_protocol.tcp_keepalive


def _safe_aiohttp_tcp_keepalive(transport) -> None:
    try:
        _aiohttp_tcp_keepalive(transport)
    except OSError as exc:
        logging.getLogger(__name__).debug("TCP keepalive unavailable: %s", exc)


aiohttp_web_protocol.tcp_keepalive = _safe_aiohttp_tcp_keepalive

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub.config_store import (  # noqa: E402
    AGENTS,
    apply_avatar_platform_update,
    apply_update,
    build_agent_env,
    default_settings,
    effective_avatar_platform,
    load_settings,
    mask_settings,
    report_llm,
    save_settings,
)
from hub.interview_config import (  # noqa: E402
    build_preview as build_interview_preview,
    read_config as read_interview_config,
    save_config as save_interview_config,
)
from interview.roster import (  # noqa: E402
    delete_avatar as roster_delete_avatar,
    entries as roster_entries,
    ensure_avatar_configs,
    find_avatar,
    load_roster,
    resolve_avatar,
    save_roster,
)
from interview.prep_assets import warm_roster_prep_assets  # noqa: E402
from interview.profile import extract_resume_text  # noqa: E402
from interview.enterprise_store import (  # noqa: E402
    EnterpriseStore,
)
from hub.connection_test import (  # noqa: E402
    check_asr_connection,
    check_llm_connection,
    check_platform_connection,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("hub")
NO_STORE_HEADERS = {"Cache-Control": "no-store"}

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
_enterprise_store = EnterpriseStore()


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


async def enterprise_page_handler(request: web.Request) -> web.Response:
    return web.Response(
        body=(FRONTEND / "hub-enterprise.html").read_bytes(),
        content_type="text/html",
        headers=NO_STORE_HEADERS,
    )


async def enterprise_js_handler(request: web.Request) -> web.Response:
    return web.Response(
        body=(FRONTEND / "hub-enterprise.js").read_bytes(),
        content_type="application/javascript",
        headers=NO_STORE_HEADERS,
    )


async def get_config_handler(request: web.Request) -> web.Response:
    settings = load_settings()
    data = mask_settings(settings)
    report_model = report_llm(settings)
    data["_runtime"] = {
        "report_llm": {
            "requested_provider": report_model["requested_provider"],
            "provider": report_model["provider"],
            "fallback": report_model["fallback"],
            "model": report_model["model"],
            "configured": bool(report_model["api_key"]),
        }
    }
    data["_defaults"] = {
        "interview": {
            "report_prompt_modules": default_settings()["interview"][
                "report_prompt_modules"
            ]
        }
    }
    return web.json_response(data)


async def _apply_config_to_running_agents(settings: dict) -> dict:
    """Restart hub-managed running agents so the just-saved config takes effect
    immediately (agents read credentials from their startup env). Externally
    started agents can't be restarted by the hub — flag them for the UI."""
    restarted, external = [], []
    for name, managed in _managed.items():
        if managed.is_running:
            await managed.stop()
            try:
                await managed.start(settings)
                restarted.append(name)
            except Exception as e:
                logger.warning("restart %s after config save failed: %s", name, e)
        elif await _port_in_use(_agent_port(settings, name)):
            external.append(name)
    return {"restarted": restarted, "external": external}


async def post_config_handler(request: web.Request) -> web.Response:
    try:
        incoming = await request.json()
        updated = apply_update(load_settings(), incoming)
    except ValueError as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)
    save_settings(updated)
    effect = await _apply_config_to_running_agents(updated)
    return web.json_response(
        {"success": True, "config": mask_settings(updated), **effect}
    )


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


def _avatar_entry(request: web.Request):
    roster = load_roster()
    ensure_avatar_configs(roster)
    slug = request.query.get("avatar")
    return (find_avatar(roster, slug) if slug else None) or resolve_avatar(roster)


def _avatar_config_path(request: web.Request):
    """Resolve the interview.yaml path for the ?avatar=<slug> query (default avatar
    when absent), ensuring the file exists first."""
    return _avatar_entry(request).config_path()


async def get_interview_config_handler(request: web.Request) -> web.Response:
    try:
        return web.json_response(read_interview_config(_avatar_config_path(request)))
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def post_interview_config_handler(request: web.Request) -> web.Response:
    try:
        incoming = await request.json()
        save_interview_config(incoming, _avatar_config_path(request))
    except ValueError as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)
    try:
        await asyncio.to_thread(
            warm_roster_prep_assets, load_roster(), [_avatar_entry(request).slug]
        )
    except Exception as e:
        logger.warning("warm prep assets after interview config save failed: %s", e)
    return web.json_response({"success": True})


async def get_roster_handler(request: web.Request) -> web.Response:
    """Full roster (with credentials) for the admin console."""
    roster = load_roster()
    ensure_avatar_configs(roster)
    return web.json_response(roster)


async def post_roster_handler(request: web.Request) -> web.Response:
    try:
        incoming = await request.json()
        existing = {item.slug: item for item in roster_entries(load_roster())}
        for item in incoming.get("avatars") or []:
            old = existing.get(str(item.get("slug") or ""))
            next_type = str(item.get("usage_type") or "practice")
            if (
                old
                and old.usage_type != next_type
                and _enterprise_store.avatar_has_records(old.slug)
            ):
                raise ValueError("该虚拟人已有企业邀请或报告，不能修改面试类型")
        roster = save_roster(incoming)
        ensure_avatar_configs(roster)
    except ValueError as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)
    try:
        await asyncio.to_thread(warm_roster_prep_assets, roster)
    except Exception as e:
        logger.warning("warm prep assets after roster save failed: %s", e)
    return web.json_response({"success": True, "roster": roster})


def _avatar_platform_payload(slug: str, settings: dict) -> dict:
    effective = effective_avatar_platform(settings, slug)
    masked = mask_settings(settings)
    global_platform = masked["platform"]
    custom = global_platform.get("avatar_profiles", {}).get(slug) or {
        "api_key": "",
        "base_url": global_platform.get("base_url", ""),
        "sandbox": global_platform.get("sandbox", ""),
    }
    return {
        "success": True,
        "use_global": bool(effective["use_global"]),
        "global": {
            "api_key": global_platform.get("api_key", ""),
            "configured": bool(settings["platform"].get("api_key")),
            "base_url": global_platform.get("base_url", ""),
            "sandbox": global_platform.get("sandbox", ""),
        },
        "custom": custom,
    }


async def get_avatar_platform_handler(request: web.Request) -> web.Response:
    avatar = _avatar_entry(request)
    return web.json_response(_avatar_platform_payload(avatar.slug, load_settings()))


async def post_avatar_platform_handler(request: web.Request) -> web.Response:
    avatar = _avatar_entry(request)
    try:
        incoming = await request.json()
        settings = apply_avatar_platform_update(load_settings(), avatar.slug, incoming)
        save_settings(settings)
    except ValueError as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=400)
    return web.json_response(_avatar_platform_payload(avatar.slug, settings))


async def test_avatar_platform_handler(request: web.Request) -> web.Response:
    avatar = _avatar_entry(request)
    try:
        incoming = await request.json()
        settings = apply_avatar_platform_update(load_settings(), avatar.slug, incoming)
    except ValueError as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=400)
    platform = effective_avatar_platform(settings, avatar.slug)
    platform.update(
        {
            "avatar_id": str(incoming.get("avatar_id") or avatar.avatar_id),
            "voice_id": str(incoming.get("voice_id") or avatar.voice_id),
        }
    )
    result = await check_platform_connection({"platform": platform})
    return web.json_response(result)


async def delete_roster_avatar_handler(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        slug = str(body.get("slug") or "")
        if _enterprise_store.avatar_has_records(slug):
            raise ValueError("该虚拟人已有企业邀请或报告，请先删除相关记录")
        roster = roster_delete_avatar(load_roster(), slug)
        settings = apply_avatar_platform_update(
            load_settings(), slug, {"use_global": True}
        )
        save_settings(settings)
    except ValueError as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)
    return web.json_response({"success": True, "roster": roster})


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


async def test_platform_handler(request: web.Request) -> web.Response:
    result = await check_platform_connection(await _merged_from_request(request))
    return web.json_response(result)


async def test_asr_handler(request: web.Request) -> web.Response:
    result = await check_asr_connection(await _merged_from_request(request))
    return web.json_response(result)


async def enterprise_list_handler(request: web.Request) -> web.Response:
    _enterprise_store.cleanup()
    return web.json_response(
        {
            "success": True,
            "records": [
                _enterprise_record_for_admin(record)
                for record in _enterprise_store.list()
            ],
        }
    )


def _enterprise_invite_url(token: str) -> str:
    if not token:
        return ""
    port = _agent_port(load_settings(), "interview")
    return f"http://localhost:{port}/enterprise?token={token}"


def _enterprise_record_for_admin(record: dict) -> dict:
    data = dict(record)
    data["invite_url"] = _enterprise_invite_url(str(record.get("invite_token") or ""))
    return data


async def enterprise_candidates_handler(request: web.Request) -> web.Response:
    return web.json_response(
        {"success": True, "candidates": _enterprise_store.list_candidates()}
    )


async def enterprise_candidate_create_handler(request: web.Request) -> web.Response:
    form = await request.post()
    resume_text = str(form.get("resume_text") or "").strip()
    resume_filename = ""
    resume_file = form.get("resume_file")
    try:
        if resume_file is not None and getattr(resume_file, "file", None):
            data = resume_file.file.read()
            if len(data) > 10 * 1024 * 1024:
                raise ValueError("简历文件不能超过 10MB")
            if data:
                resume_filename = str(resume_file.filename or "")
                resume_text = extract_resume_text(resume_filename, data)
        candidate = _enterprise_store.create_candidate(
            name=str(form.get("name") or ""),
            contact=str(form.get("contact") or ""),
            source=str(form.get("source") or ""),
            resume_filename=resume_filename,
            resume_text=resume_text,
        )
    except ValueError as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=400)
    return web.json_response({"success": True, "candidate": candidate})


async def enterprise_candidate_detail_handler(request: web.Request) -> web.Response:
    candidate = _enterprise_store.get_candidate(request.match_info["candidate_id"])
    if candidate is None:
        return web.json_response({"success": False, "error": "候选人不存在"}, status=404)
    return web.json_response({"success": True, "candidate": candidate})


async def enterprise_candidate_delete_handler(request: web.Request) -> web.Response:
    if not _enterprise_store.delete_candidate(request.match_info["candidate_id"]):
        return web.json_response({"success": False, "error": "候选人不存在"}, status=404)
    return web.json_response({"success": True})


async def enterprise_positions_handler(request: web.Request) -> web.Response:
    return web.json_response(
        {"success": True, "positions": _enterprise_store.list_positions()}
    )


async def enterprise_position_create_handler(request: web.Request) -> web.Response:
    body = await request.json()
    try:
        position = _enterprise_store.create_position(
            title=str(body.get("title") or ""),
            jd=str(body.get("jd") or ""),
        )
    except ValueError as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=400)
    return web.json_response({"success": True, "position": position})


async def enterprise_position_update_handler(request: web.Request) -> web.Response:
    body = await request.json()
    try:
        position = _enterprise_store.update_position(
            request.match_info["position_id"],
            title=str(body.get("title") or ""),
            jd=str(body.get("jd") or ""),
        )
    except ValueError as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=400)
    if position is None:
        return web.json_response({"success": False, "error": "岗位不存在"}, status=404)
    return web.json_response({"success": True, "position": position})


async def enterprise_position_delete_handler(request: web.Request) -> web.Response:
    if not _enterprise_store.delete_position(request.match_info["position_id"]):
        return web.json_response({"success": False, "error": "岗位不存在"}, status=404)
    return web.json_response({"success": True})


async def enterprise_create_handler(request: web.Request) -> web.Response:
    body = await request.json()
    slug = str(body.get("avatar_slug") or "")
    avatar = find_avatar(load_roster(), slug)
    if avatar is None or avatar.usage_type != "enterprise":
        return web.json_response(
            {"success": False, "error": "请选择企业招聘型虚拟人"}, status=400
        )
    position = _enterprise_store.get_position(str(body.get("position_id") or ""))
    if position is None:
        return web.json_response(
            {"success": False, "error": "请选择候选人应聘岗位"}, status=400
        )
    candidate = _enterprise_store.get_candidate(str(body.get("candidate_id") or ""))
    if candidate is None:
        return web.json_response(
            {"success": False, "error": "请选择已上传简历的候选人"}, status=400
        )
    try:
        interview_snapshot = yaml.safe_load(
            avatar.config_path().read_text(encoding="utf-8")
        ) or {}
    except (OSError, yaml.YAMLError) as exc:
        return web.json_response(
            {"success": False, "error": f"读取面试配置失败：{exc}"}, status=400
        )
    try:
        record, token = _enterprise_store.create_invite(
            avatar.slug,
            int(body.get("expires_days") or 7),
            avatar.public(),
            candidate=candidate,
            position_id=position["id"],
            target_role=position["title"],
            jd_text=position["jd"],
            position_snapshot=position,
            interview_config_snapshot=interview_snapshot,
        )
    except (TypeError, ValueError) as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=400)
    invite_url = _enterprise_invite_url(token)
    return web.json_response(
        {
            "success": True,
            "record": _enterprise_record_for_admin(record),
            "invite_url": invite_url,
        }
    )


async def enterprise_detail_handler(request: web.Request) -> web.Response:
    record = _enterprise_store.get(request.match_info["record_id"])
    if record is None:
        return web.json_response({"success": False, "error": "记录不存在"}, status=404)
    return web.json_response(
        {"success": True, "record": _enterprise_record_for_admin(record)}
    )


async def enterprise_renew_invite_handler(request: web.Request) -> web.Response:
    renewed = _enterprise_store.renew_invite(request.match_info["record_id"])
    if renewed is None:
        return web.json_response(
            {"success": False, "error": "仅待面试任务可以重新生成链接"},
            status=409,
        )
    record, token = renewed
    return web.json_response(
        {
            "success": True,
            "record": _enterprise_record_for_admin(record),
            "invite_url": _enterprise_invite_url(token),
        }
    )


async def enterprise_revoke_handler(request: web.Request) -> web.Response:
    record = _enterprise_store.revoke(request.match_info["record_id"])
    if record is None:
        return web.json_response({"success": False, "error": "记录不存在"}, status=404)
    return web.json_response({"success": True, "record": record})


async def enterprise_delete_handler(request: web.Request) -> web.Response:
    if not _enterprise_store.delete(request.match_info["record_id"]):
        return web.json_response({"success": False, "error": "记录不存在"}, status=404)
    return web.json_response({"success": True})


async def _cleanup(app: web.Application) -> None:
    task = app.get("enterprise_cleanup_task")
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await asyncio.gather(*(m.stop() for m in _managed.values()))


async def _enterprise_cleanup_loop() -> None:
    while True:
        await asyncio.sleep(24 * 60 * 60)
        await asyncio.to_thread(_enterprise_store.cleanup)


async def _startup(app: web.Application) -> None:
    await asyncio.to_thread(_enterprise_store.cleanup)
    app["enterprise_cleanup_task"] = asyncio.create_task(
        _enterprise_cleanup_loop()
    )


def main() -> None:
    app = web.Application(client_max_size=10 * 1024 * 1024)
    app.router.add_get("/", index_handler)
    app.router.add_get("/api/config", get_config_handler)
    app.router.add_post("/api/config", post_config_handler)
    app.router.add_post("/api/config/test-platform", test_platform_handler)
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
    app.router.add_get("/api/roster", get_roster_handler)
    app.router.add_post("/api/roster", post_roster_handler)
    app.router.add_post("/api/roster/delete", delete_roster_avatar_handler)
    app.router.add_get("/api/interview-platform", get_avatar_platform_handler)
    app.router.add_post("/api/interview-platform", post_avatar_platform_handler)
    app.router.add_post(
        "/api/interview-platform/test", test_avatar_platform_handler
    )
    app.router.add_get("/enterprise-interviews", enterprise_page_handler)
    app.router.add_get("/enterprise-interviews.js", enterprise_js_handler)
    app.router.add_get("/api/enterprise/interviews", enterprise_list_handler)
    app.router.add_get("/api/enterprise/positions", enterprise_positions_handler)
    app.router.add_post(
        "/api/enterprise/positions", enterprise_position_create_handler
    )
    app.router.add_put(
        "/api/enterprise/positions/{position_id}",
        enterprise_position_update_handler,
    )
    app.router.add_delete(
        "/api/enterprise/positions/{position_id}",
        enterprise_position_delete_handler,
    )
    app.router.add_get("/api/enterprise/candidates", enterprise_candidates_handler)
    app.router.add_post(
        "/api/enterprise/candidates", enterprise_candidate_create_handler
    )
    app.router.add_get(
        "/api/enterprise/candidates/{candidate_id}",
        enterprise_candidate_detail_handler,
    )
    app.router.add_delete(
        "/api/enterprise/candidates/{candidate_id}",
        enterprise_candidate_delete_handler,
    )
    app.router.add_post("/api/enterprise/invites", enterprise_create_handler)
    app.router.add_get(
        "/api/enterprise/interviews/{record_id}", enterprise_detail_handler
    )
    app.router.add_post(
        "/api/enterprise/interviews/{record_id}/revoke",
        enterprise_revoke_handler,
    )
    app.router.add_post(
        "/api/enterprise/interviews/{record_id}/renew-link",
        enterprise_renew_invite_handler,
    )
    app.router.add_delete(
        "/api/enterprise/interviews/{record_id}", enterprise_delete_handler
    )
    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    logger.info("🎛  Hub console at http://localhost:%d", HUB_PORT)
    web.run_app(app, host="127.0.0.1", port=HUB_PORT)


if __name__ == "__main__":
    main()
