"""HTTP route handlers for Teaching Agent."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid

import yaml
from aiohttp import web

from teaching.config import (
    COURSE_PATH,
    COURSE_NAME,
    FRONTEND,
    json_response,
)
from teaching.course_generator import _resolve_profile
from teaching.course_manager import CourseManager
from teaching.persona_manager import PersonaManager
from teaching.classmate_engine import ClassmateEngine
from teaching.pacing_engine import PacingEngine
import teaching.session as sess


_AGE_EMOJI = {"4-6": "🏃", "7-8": "🕵️", "9-10": "⚔️"}


async def handle_start_session(request: web.Request) -> web.Response:
    try:
        session_info = await sess.start_avatar_session()
    except RuntimeError as exc:
        return json_response(
            {"success": False, "error": str(exc)}, status=503
        )
    except Exception as exc:
        logging.getLogger(__name__).error("Failed to start avatar session: %s", exc)
        return json_response(
            {"success": False, "error": "Failed to start avatar session"},
            status=500,
        )
    return json_response(
        {
            "success": True,
            "userToken": session_info["userToken"],
            "sfuUrl": session_info["sfuUrl"],
            "sessionId": session_info.get("sessionId"),
        }
    )


async def handle_teaching_start(request: web.Request) -> web.Response:
    controller = sess.get_controller()
    if controller is None:
        return json_response(
            {"success": False, "error": "Not initialized"}, status=500
        )
    controller.start()
    return json_response({"success": True})


async def handle_teaching_stop(request: web.Request) -> web.Response:
    controller = sess.get_controller()
    if controller is None:
        return json_response(
            {"success": False, "error": "Not initialized"}, status=500
        )
    controller.stop()
    return json_response({"success": True})


async def handle_teaching_pause(request: web.Request) -> web.Response:
    controller = sess.get_controller()
    if controller is None:
        return json_response(
            {"success": False, "error": "Not initialized"}, status=500
        )
    controller.pause()
    return json_response({"success": True})


async def handle_teaching_resume(request: web.Request) -> web.Response:
    controller = sess.get_controller()
    if controller is None:
        return json_response(
            {"success": False, "error": "Not initialized"}, status=500
        )
    controller.resume()
    return json_response({"success": True})


async def handle_skip_chapter(request: web.Request) -> web.Response:
    controller = sess.get_controller()
    if controller is None:
        return json_response({"success": False, "error": "Not initialized"}, status=500)
    try:
        result = await controller.skip_chapter()
        return json_response(result)
    except Exception as e:
        return json_response({"success": False, "error": str(e)}, status=500)


async def handle_raise_hand(request: web.Request) -> web.Response:
    controller = sess.get_controller()
    agent = sess.get_agent()
    listener = sess.get_listener()
    if controller is None:
        return json_response(
            {"success": False, "error": "Not initialized"}, status=500
        )
    controller.raise_hand()
    await controller.stop_classmate_audio()
    # Immediately interrupt TTS and start voice session — don't wait for VAD
    if agent:
        try:
            await agent.send_interrupt()
        except Exception:
            pass
    if listener:
        if listener._hand_timeout_task:
            listener._hand_timeout_task.cancel()
            listener._hand_timeout_task = None
        listener._voice_request_id = str(uuid.uuid4())
        if agent:
            await agent.send_voice_start(listener._voice_request_id)
    return json_response({"success": True})


async def handle_cancel_hand(request: web.Request) -> web.Response:
    controller = sess.get_controller()
    listener = sess.get_listener()
    if controller is None:
        return json_response(
            {"success": False, "error": "Not initialized"}, status=500
        )
    qa_was_running = bool(listener and listener._qa_in_progress)
    # Set flag: QA flow will check this and skip transition when it finishes
    if listener:
        listener._hand_cancelled_during_qa = True
        if listener._hand_timeout_task:
            listener._hand_timeout_task.cancel()
            listener._hand_timeout_task = None
    controller.cancel_hand()
    # If QA wasn't running (student raised hand but didn't speak), resume immediately
    if not qa_was_running and controller._breakpoint:
        controller._task = asyncio.create_task(controller._resume_lecture())
    return json_response({"success": True})


async def handle_quiz_answer(request: web.Request) -> web.Response:
    controller = sess.get_controller()
    if controller is None:
        return json_response(
            {"success": False, "error": "Not initialized"}, status=500
        )
    try:
        body = await request.json()
        chapter_id = body.get("chapter_id", "")
        answer = body.get("answer", "")
    except Exception:
        return json_response(
            {"success": False, "error": "Invalid JSON"}, status=400
        )
    if not chapter_id or not answer:
        return json_response(
            {"success": False, "error": "chapter_id and answer required"},
            status=400,
        )
    try:
        controller.answer_quiz(chapter_id, answer)
        return json_response({"success": True})
    except ValueError as e:
        return json_response(
            {"success": False, "error": str(e)}, status=400
        )


async def handle_teaching_status(request: web.Request) -> web.Response:
    controller = sess.get_controller()
    if controller is None:
        return json_response(
            {"success": False, "error": "Not initialized"}, status=500
        )
    return json_response(controller.get_status())


async def handle_course_generate(request: web.Request) -> web.Response:
    generator = sess.get_generator()
    if generator is None:
        return json_response({"success": False, "error": "Not initialized"}, status=500)
    try:
        body = await request.json()
        topic = body.get("topic", "").strip()
        age = body.get("age", "4-10")
        chapter_count = int(body.get("chapter_count", 3))
    except Exception:
        return json_response({"success": False, "error": "Invalid JSON"}, status=400)
    if not topic:
        return json_response({"success": False, "error": "topic is required"}, status=400)
    try:
        result = await generator.generate(topic=topic, age=age, chapter_count=chapter_count)
        return json_response({"success": True, **result})
    except Exception as e:
        logging.getLogger(__name__).error("Course generation failed: %s", e)
        return json_response({"success": False, "error": str(e)}, status=500)


async def handle_courses_list(request: web.Request) -> web.Response:
    """GET /api/teaching/courses — list all courses grouped by age."""
    courses_dir = sess.get_course_path().parent
    groups: dict[str, dict] = {}  # age → {label, strategy, courses: []}

    for f in sorted(courses_dir.glob("*.yaml")):
        stem = f.stem  # e.g. "thinking_4-10" or "怪兽镜子大冒险_7-8"
        # Extract age from filename suffix
        m = re.match(r"^(.+)_(\d+-\d+)$", stem)
        if not m:
            continue  # skip files that don't follow {name}_{age}.yaml
        name, age = m.group(1), m.group(2)

        # Lightweight parse — only read course title + chapter count
        try:
            raw = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            course_info = raw.get("course", {})
            title = course_info.get("title", name)
            chapters_list = raw.get("chapters", [])
            chapter_count = len(chapters_list)
        except Exception:
            continue

        # Determine strategy label
        profile = _resolve_profile(age)
        strategy = profile["strategy"]
        label = profile["label"]

        if age not in groups:
            groups[age] = {
                "age": age,
                "label": f"{age}岁 · {label}",
                "strategy": f"{_AGE_EMOJI.get(age, '📚')} {strategy}",
                "courses": [],
            }
        groups[age]["courses"].append({
            "name": stem,
            "title": title,
            "chapters": chapter_count,
            "age": age,
        })

    # Build ordered result
    age_order = ["4-6", "7-8", "9-10"]
    result_groups = []
    for age in age_order:
        if age in groups:
            groups[age]["courses"].sort(key=lambda c: c["title"])
            result_groups.append(groups[age])

    return web.json_response({
        "default": "thinking_4-10",
        "current": sess.get_course_name(),
        "groups": result_groups,
    })


async def handle_courses_select(request: web.Request) -> web.Response:
    """POST /api/teaching/courses/select — switch to a different course."""
    try:
        body = await request.json()
        course_name = body.get("course_name", "").strip()
    except Exception:
        return json_response({"success": False, "error": "Invalid JSON"}, status=400)

    if not course_name:
        return json_response({"success": False, "error": "course_name required"}, status=400)

    new_path = sess.get_course_path().parent / f"{course_name}.yaml"
    if not new_path.exists():
        return json_response({"success": False, "error": f"Course not found: {course_name}"}, status=404)

    # Stop active session if any (can't switch mid-session)
    agent = sess.get_agent()
    if agent is not None:
        await sess.stop_active_avatar_session()

    # Update course globals via session
    sess._course_manager = CourseManager(new_path)
    course = sess._course_manager.get_course()
    logging.getLogger(__name__).info(
        "📚 Switched to course: %s (%d chapters)", course["title"], sess._course_manager.get_chapter_count(),
    )

    # Rebuild persona from new course config
    sess._persona = PersonaManager(sess._course_manager._raw)
    from teaching.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
    from llm_client import LlmClient

    def _mk_llm(name: str, sp: str) -> LlmClient:
        return LlmClient(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL,
                         model=DEEPSEEK_MODEL, system_prompt=sp)

    sess._classmates = ClassmateEngine(sess._persona, _mk_llm, tts_client=sess.get_tts_client())
    sess._pacing = PacingEngine(classmate_engine=sess._classmates)
    sess._manager = sess._pacing  # legacy alias
    sess._controller = None
    sess._session_info = {}

    # Also update COURSE_PATH/COURSE_NAME via config module
    import teaching.config as _cfg
    _cfg.COURSE_NAME = course_name
    _cfg.COURSE_PATH = new_path

    return json_response({
        "success": True,
        "course": {
            "name": course_name,
            "title": course["title"],
            "chapters": sess._course_manager.get_chapter_count(),
        },
    })


async def handle_courses_current(request: web.Request) -> web.Response:
    """GET /api/teaching/courses/current — info about the currently loaded course."""
    course_manager = sess.get_course_manager()
    if not course_manager:
        return json_response({"success": False, "error": "Not initialized"}, status=503)

    course = course_manager.get_course()
    # Extract age from COURSE_NAME
    age = "4-10"
    m = re.match(r".+_(\d+-\d+)$", sess.get_course_name())
    if m:
        age = m.group(1)

    return json_response({
        "name": sess.get_course_name(),
        "title": course.get("title", ""),
        "chapters": course_manager.get_chapter_count(),
        "age": age,
    })


async def handle_index(request: web.Request) -> web.Response:
    html_path = FRONTEND / "teaching.html"
    if html_path.exists():
        return web.Response(
            body=html_path.read_bytes(), content_type="text/html"
        )
    return web.Response(
        text="<h1>Teaching Agent Ready</h1>", content_type="text/html"
    )


async def handle_sdk_js(request: web.Request) -> web.Response:
    js_path = (
        FRONTEND
        / "node_modules"
        / "@sanseng"
        / "liveavatar-js-sdk"
        / "dist"
        / "index.full.umd.js"
    )
    if js_path.exists():
        return web.Response(
            body=js_path.read_bytes(), content_type="application/javascript"
        )
    return web.Response(status=404)


async def handle_whiteboard_asset(request: web.Request) -> web.Response:
    """Serve static files from frontend/teaching/whiteboard/ (JS modules, CSS)."""
    filename = request.match_info["filename"]
    # Security: prevent path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        return web.Response(status=404)
    file_path = FRONTEND / "teaching" / "whiteboard" / filename
    if not file_path.is_file():
        return web.Response(status=404)
    suffix = file_path.suffix.lower()
    content_type = {
        ".js": "application/javascript",
        ".css": "text/css",
        ".html": "text/html",
        ".svg": "image/svg+xml",
        ".png": "image/png",
    }.get(suffix, "application/octet-stream")
    return web.Response(body=file_path.read_bytes(), content_type=content_type)


# ---------------------------------------------------------------------------
# WebSocket handler — real-time push to frontend
# ---------------------------------------------------------------------------


async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    sess.ws_register(ws)
    logger = logging.getLogger(__name__)
    logger.info("🔌 WS client connected")

    # Send full state snapshot on connect
    await sess.ws_send_state_sync(ws)

    try:
        async for msg in ws:
            # Frontend can send heartbeats or ack; ignore for now
            if msg.type == web.WSMsgType.TEXT:
                pass
            elif msg.type == web.WSMsgType.ERROR:
                logger.warning("WS error: %s", ws.exception())
    finally:
        sess.ws_unregister(ws)
        logger.info("🔌 WS client disconnected")
    return ws
