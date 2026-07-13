#!/usr/bin/env python3
"""Teaching Digital Human Agent — chapter-based lectures with interruptible Q&A.

Port 8082. Target audience: children aged 4-10.

This is the thin entry point. See the teaching/ package for implementation:
  config.py          — env vars, logging, helpers
  asr_manager.py     — Qwen ASR (DashScope real-time)
  ws_patch.py        — LiveAvatar WS client monkey-patch
  listener.py        — TeachingListener: platform events → controller
  session.py         — Global state + session lifecycle
  routes.py          — HTTP route handlers
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure python-agent/ is on sys.path so 'teaching' package is importable
_HERE = Path(__file__).parent
_PARENT = _HERE.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

import logging

from aiohttp import web

from teaching.config import (
    HTTP_PORT,
    DEEPSEEK_API_KEY,
    API_KEY,
    AVATAR_ID,
    setup_logging,
)
from teaching.ws_patch import patch_ws_client
from teaching.session import init_teaching, shutdown_teaching
from teaching.routes import (
    handle_start_session,
    handle_teaching_start,
    handle_teaching_stop,
    handle_teaching_pause,
    handle_teaching_resume,
    handle_skip_chapter,
    handle_raise_hand,
    handle_cancel_hand,
    handle_quiz_answer,
    handle_teaching_status,
    handle_course_generate,
    handle_courses_list,
    handle_courses_select,
    handle_courses_current,
    handle_index,
    handle_sdk_js,
    handle_whiteboard_asset,
    handle_ws,
)


# Keep backward-compatible re-exports for scripts that do
#   from teaching.agent import TeachingListener, QwenAsrManager, ...
from teaching.asr_manager import QwenAsrManager, AsrCallback
from teaching.listener import TeachingListener
from teaching.session import (
    get_agent,
    get_listener,
    get_controller,
    get_course_manager,
    get_generator,
    get_course_name,
    get_course_path,
    get_persona,
    get_tts_client,
    get_classmates,
    start_avatar_session,
    stop_active_avatar_session,
)

# Legacy: forward the controller globals so old `from teaching.agent import _controller` still works
from teaching.config import (
    API_KEY as _API_KEY,
    AVATAR_ID as _AVATAR_ID,
    BASE_URL as _BASE_URL,
    DASHSCOPE_API_KEY,
    DASHSCOPE_ASR_MODEL,
    DASHSCOPE_ASR_URL,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    COURSE_NAME,
    COURSE_PATH,
)


def main() -> None:
    if not DEEPSEEK_API_KEY:
        print("⚠️  DEEPSEEK_API_KEY not set")
    if not API_KEY:
        print("⚠️  LIVEAVATAR_API_KEY not set")
    if not AVATAR_ID:
        print("⚠️  LIVEAVATAR_AVATAR_ID not set")

    setup_logging()
    patch_ws_client()
    logger = logging.getLogger(__name__)
    logger.info("📚 Starting Teaching Agent on port %d", HTTP_PORT)

    app = web.Application()
    app.router.add_post("/api/start-session", handle_start_session)
    app.router.add_post("/api/teaching/start", handle_teaching_start)
    app.router.add_post("/api/teaching/stop", handle_teaching_stop)
    app.router.add_post("/api/teaching/pause", handle_teaching_pause)
    app.router.add_post("/api/teaching/resume", handle_teaching_resume)
    app.router.add_post("/api/teaching/skip-chapter", handle_skip_chapter)
    app.router.add_post("/api/teaching/raise-hand", handle_raise_hand)
    app.router.add_post("/api/teaching/cancel-hand", handle_cancel_hand)
    app.router.add_post("/api/teaching/quiz-answer", handle_quiz_answer)
    app.router.add_get("/api/teaching/status", handle_teaching_status)
    app.router.add_get("/api/teaching/ws", handle_ws)
    app.router.add_post("/api/teaching/generate", handle_course_generate)
    app.router.add_get("/api/teaching/courses", handle_courses_list)
    app.router.add_post("/api/teaching/courses/select", handle_courses_select)
    app.router.add_get("/api/teaching/courses/current", handle_courses_current)
    app.router.add_get("/", handle_index)
    app.router.add_get("/sdk.js", handle_sdk_js)
    app.router.add_get("/teaching/whiteboard/{filename}", handle_whiteboard_asset)

    app.on_startup.append(lambda _app: init_teaching())
    app.on_shutdown.append(lambda _app: shutdown_teaching())

    web.run_app(app, host="0.0.0.0", port=HTTP_PORT)


if __name__ == "__main__":
    main()
