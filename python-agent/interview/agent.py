#!/usr/bin/env python3
"""Interview digital human agent."""

from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import sys
import uuid
from pathlib import Path
from time import perf_counter

from aiohttp import web

sys.path.insert(0, str(Path(__file__).parent.parent))

from liveavatar_channel_sdk import AvatarAgent, AvatarAgentConfig

from interview.answer_evaluator import AnswerEvaluator
from interview.controller import InterviewController
from interview.follow_up_decider import FollowUpDecider
from interview.interview_manager import InterviewManager
from interview.listener import InterviewListener
from interview.models import InterviewState
from interview.profile import apply_profile, extract_resume_text, read_profile
from interview.question_planner import QuestionPlanner
from interview.report_generator import ReportGenerator
from interview.session_store import JsonInterviewStore
from llm_client import LlmClient

HERE = Path(__file__).parent
FRONTEND = HERE.parent.parent / "frontend"

API_KEY = os.getenv("LIVEAVATAR_API_KEY", "")
AVATAR_ID = os.getenv("LIVEAVATAR_AVATAR_ID", "")
BASE_URL = os.getenv(
    "LIVEAVATAR_BASE_URL", "https://facemarket.ai/vih/dispatcher"
)
VOICE_ID = os.getenv("LIVEAVATAR_VOICE_ID", None)
SANDBOX = os.getenv("LIVEAVATAR_SANDBOX", "").strip().lower() in {"1", "true", "yes", "on"}
HTTP_PORT = int(os.getenv("INTERVIEW_HTTP_PORT", "8083"))

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

CONFIG_PATH = Path(
    os.getenv(
        "INTERVIEW_CONFIG_PATH",
        str(Path(__file__).parent.parent / "config" / "interview.yaml"),
    )
)
STORAGE_DIR = Path(os.getenv("INTERVIEW_STORAGE_DIR", "/tmp/liveavatar-interviews"))

_agent: AvatarAgent | None = None
_controller: InterviewController | None = None
_listener: InterviewListener | None = None
_asr_manager = None
_session_info: dict = {}
_last_interview_status: dict | None = None
NO_STORE_HEADERS = {"Cache-Control": "no-store"}


def json_response(data, **kwargs):
    return web.json_response(
        data, dumps=lambda obj: _json.dumps(obj, ensure_ascii=False), **kwargs
    )


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def build_controller(agent) -> InterviewController:
    manager = InterviewManager(CONFIG_PATH)
    cfg = manager.config
    persona = manager.persona_context()
    llm_client = LlmClient(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        model=DEEPSEEK_MODEL,
        system_prompt=manager.build_system_prompt(),
    )
    return InterviewController(
        agent=agent,
        manager=manager,
        planner=QuestionPlanner(manager.get_question_specs()),
        evaluator=AnswerEvaluator(
            llm_client, prompt_template=cfg.prompts.evaluator, context=persona
        ),
        report_generator=ReportGenerator(
            llm_client, prompt_template=cfg.prompts.report, context=persona
        ),
        follow_up_decider=FollowUpDecider(
            llm_client, prompt_template=cfg.prompts.follow_up_decider, context=persona
        ),
        session_store=JsonInterviewStore(STORAGE_DIR),
        interview_id=f"iv_{uuid.uuid4().hex[:8]}",
        thinking_checks=[
            (check.after_seconds, check.text) for check in cfg.speech.thinking_checks
        ],
        hard_timeout_seconds=cfg.workflow.hard_timeout_seconds,
        opening_to_question_delay_seconds=cfg.workflow.opening_to_question_delay_seconds,
        prompt_playback_timeout_seconds=cfg.workflow.prompt_playback_timeout_seconds,
        candidate_speech_grace_seconds=cfg.workflow.candidate_speech_grace_seconds,
        evaluation_join_timeout_seconds=cfg.workflow.evaluation_join_timeout_seconds,
        max_skipped_questions=cfg.workflow.max_skipped_questions,
        max_consecutive_skipped_questions=cfg.workflow.max_consecutive_skipped_questions,
        speech_config=cfg.speech,
        on_terminal=handle_interview_terminal,
    )


async def start_interview_session() -> tuple[str, str]:
    global _agent, _controller, _listener, _asr_manager, _session_info, _last_interview_status

    logger = logging.getLogger(__name__)
    total_started_at = perf_counter()
    asr_connect_ms = 0
    avatar_start_ms = 0
    controller_build_ms = 0

    await stop_interview_session()
    _last_interview_status = None

    _listener = InterviewListener()
    if DASHSCOPE_API_KEY:
        from interview.asr_manager import QwenAsrManager

        _asr_manager = QwenAsrManager(
            on_transcript=_listener._on_asr_transcript,
            on_speech_started=_listener._on_speech_started,
            on_speech_stopped=_listener._on_speech_stopped,
            on_interim=_listener._on_asr_interim,
        )
        _listener.asr_manager = _asr_manager
    else:
        # WebSocket Agent 模式下 ASR 始终由开发者提供（见官方接入指南），
        # 平台侧没有 ASR 兜底 — 缺少 DashScope Key 时语音作答不可用。
        logger.warning(
            "DASHSCOPE_API_KEY 未配置 — 语音作答不可用，候选人仅能通过文本输入回答"
        )

    voice_config = None
    voice_speed_raw = os.getenv("LIVEAVATAR_VOICE_SPEED", "").strip()
    if voice_speed_raw:
        try:
            voice_config = {"speed": float(voice_speed_raw)}
        except ValueError:
            logger.warning("LIVEAVATAR_VOICE_SPEED 不是数字，已忽略: %r", voice_speed_raw)

    config = AvatarAgentConfig(
        api_key=API_KEY,
        avatar_id=AVATAR_ID,
        base_url=BASE_URL,
        sandbox=SANDBOX,
        developer_asr=True,
        developer_tts=False,
        voice_id=VOICE_ID,
        voice_config=voice_config,
        timeout=30.0,
    )
    _agent = AvatarAgent(config, _listener)
    _listener.agent = _agent

    async def _connect_asr() -> None:
        nonlocal asr_connect_ms
        if _asr_manager is None:
            return
        started_at = perf_counter()
        await _asr_manager.connect()
        asr_connect_ms = round((perf_counter() - started_at) * 1000)

    async def _start_avatar():
        nonlocal avatar_start_ms
        started_at = perf_counter()
        result = await _agent.start()
        avatar_start_ms = round((perf_counter() - started_at) * 1000)
        return result

    asr_task = asyncio.create_task(_connect_asr())
    avatar_task = asyncio.create_task(_start_avatar())
    try:
        _, result = await asyncio.gather(asr_task, avatar_task)
    except Exception:
        await stop_interview_session()
        raise

    started_at = perf_counter()
    _controller = build_controller(_agent)
    controller_build_ms = round((perf_counter() - started_at) * 1000)
    _listener.set_controller(_controller)

    _session_info = {
        "userToken": result.user_token,
        "sfuUrl": result.sfu_url,
        "sessionId": result.session_id,
        "asrAvailable": _asr_manager is not None,
    }
    logger.info(
        "startup timing: asr_connect_ms=%s avatar_start_ms=%s "
        "controller_build_ms=%s total_ms=%s",
        asr_connect_ms,
        avatar_start_ms,
        controller_build_ms,
        round((perf_counter() - total_started_at) * 1000),
    )
    return result.user_token, result.sfu_url


async def stop_interview_session() -> None:
    global _agent, _controller, _listener, _asr_manager, _session_info, _last_interview_status
    _last_interview_status = None
    if _controller is not None:
        stopper = getattr(_controller, "stop", None)
        if stopper is not None:
            await stopper()
    await release_interview_session_resources()


async def handle_interview_terminal(_state=None) -> None:
    global _last_interview_status
    if _controller is not None:
        status_getter = getattr(_controller, "get_status", None)
        if status_getter is not None:
            _last_interview_status = status_getter()
    await release_interview_session_resources()


async def release_interview_session_resources() -> None:
    global _agent, _controller, _listener, _asr_manager, _session_info
    _controller = None
    if _asr_manager is not None:
        try:
            await _asr_manager.close()
        except Exception:
            pass
        _asr_manager = None
    if _agent is not None:
        try:
            await _agent.stop()
        except Exception:
            pass
        _agent = None
    _listener = None
    _session_info = {}


async def handle_index(request: web.Request) -> web.Response:
    return web.FileResponse(FRONTEND / "interview.html", headers=NO_STORE_HEADERS)


async def handle_interview_js(request: web.Request) -> web.Response:
    return web.FileResponse(FRONTEND / "interview.js", headers=NO_STORE_HEADERS)


async def handle_sdk_js(request: web.Request) -> web.Response:
    js_path = (
        FRONTEND
        / "node_modules"
        / "@sanseng"
        / "liveavatar-js-sdk"
        / "dist"
        / "index.full.umd.js"
    )
    if not js_path.exists():
        return web.Response(status=404)
    return web.Response(
        body=js_path.read_bytes(),
        content_type="application/javascript",
        headers=NO_STORE_HEADERS,
    )


async def handle_start_session(request: web.Request) -> web.Response:
    try:
        user_token, sfu_url = await start_interview_session()
        return json_response(
            {
                "success": True,
                "userToken": user_token,
                "sfuUrl": sfu_url,
                "sessionId": _session_info.get("sessionId", ""),
                "asrAvailable": _session_info.get("asrAvailable", False),
            }
        )
    except Exception as exc:
        logging.getLogger(__name__).error("start-session failed: %s", exc)
        return json_response({"success": False, "error": str(exc)}, status=500)


async def handle_stop_session(request: web.Request) -> web.Response:
    await stop_interview_session()
    return json_response({"success": True})


async def handle_session_info(request: web.Request) -> web.Response:
    return json_response(_session_info)


async def handle_interview_start(request: web.Request) -> web.Response:
    global _controller
    if _controller is None or _agent is None:
        return json_response({"success": False, "error": "No active session"}, status=400)
    if _controller.state == InterviewState.IDLE:
        # The session may have been preheated before the candidate saved their
        # profile — rebuild so the controller reads the freshest config/YAML.
        _controller = build_controller(_agent)
        if _listener is not None:
            _listener.set_controller(_controller)
    await _controller.start()
    # scene.ready fires once, typically during preheat before the interview
    # starts — replay it so the (possibly rebuilt) controller can open.
    if _listener is not None and getattr(_listener, "scene_ready_seen", False):
        await _controller.mark_scene_ready()
    return json_response({"success": True, "status": _controller.get_status()})


async def handle_interview_stop(request: web.Request) -> web.Response:
    global _last_interview_status
    if _controller is not None:
        stopper = getattr(_controller, "stop", None)
        if stopper is not None:
            await stopper()
        status_getter = getattr(_controller, "get_status", None)
        if status_getter is not None:
            _last_interview_status = status_getter()
    await release_interview_session_resources()
    return json_response({"success": True})


async def handle_interview_audio_input(request: web.Request) -> web.Response:
    if _listener is None:
        return json_response({"success": False, "error": "No active session"}, status=400)
    data = await request.json()
    enabled = bool(data.get("enabled"))
    _listener.set_audio_input_enabled(enabled)
    return json_response({"success": True, "enabled": enabled})


async def handle_get_profile(request: web.Request) -> web.Response:
    return json_response(read_profile(CONFIG_PATH))


async def handle_post_profile(request: web.Request) -> web.Response:
    """Candidate profile intake: job title + JD + resume (file or pasted text)."""
    form = await request.post()
    target_role = str(form.get("target_role") or "")
    jd_text = str(form.get("jd_text") or "")
    resume_text = str(form.get("resume_text") or "")
    resume_file = form.get("resume_file")
    try:
        if resume_file is not None and getattr(resume_file, "file", None):
            data = resume_file.file.read()
            if data:
                # An uploaded file wins over pasted text.
                resume_text = extract_resume_text(resume_file.filename or "", data)
        summary = apply_profile(
            target_role=target_role,
            jd_text=jd_text,
            resume_text=resume_text,
            path=CONFIG_PATH,
        )
    except ValueError as exc:
        return json_response({"success": False, "error": str(exc)}, status=400)
    return json_response({"success": True, **summary})


async def handle_interview_status(request: web.Request) -> web.Response:
    if _controller is None:
        if _last_interview_status is not None:
            return json_response(_last_interview_status)
        return json_response({"state": InterviewState.IDLE.value})
    return json_response(_controller.get_status())


async def create_app() -> web.Application:
    # client_max_size covers resume uploads (PDF/Word up to 10 MB).
    app = web.Application(client_max_size=10 * 1024 * 1024)
    app.router.add_get("/", handle_index)
    app.router.add_get("/interview.js", handle_interview_js)
    app.router.add_get("/sdk.js", handle_sdk_js)
    app.router.add_post("/api/start-session", handle_start_session)
    app.router.add_post("/api/stop-session", handle_stop_session)
    app.router.add_get("/api/session-info", handle_session_info)
    app.router.add_post("/api/interview/start", handle_interview_start)
    app.router.add_post("/api/interview/stop", handle_interview_stop)
    app.router.add_post("/api/interview/audio-input", handle_interview_audio_input)
    app.router.add_get("/api/interview/status", handle_interview_status)
    app.router.add_get("/api/interview/profile", handle_get_profile)
    app.router.add_post("/api/interview/profile", handle_post_profile)
    return app


def main() -> None:
    setup_logging()
    web.run_app(create_app(), host="0.0.0.0", port=HTTP_PORT)


if __name__ == "__main__":
    main()
