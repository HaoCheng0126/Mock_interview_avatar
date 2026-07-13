"""Teaching Agent configuration — env vars, logging, helpers."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

HERE = Path(__file__).parent          # teaching/
ROOT = HERE.parent                     # python-agent/
FRONTEND = ROOT.parent / "frontend"    # liveavatar-ws-integration-demo/frontend/

# -- LiveAvatar --
API_KEY = os.getenv("LIVEAVATAR_API_KEY", "")
AVATAR_ID = os.getenv("LIVEAVATAR_AVATAR_ID", "")
BASE_URL = os.getenv(
    "LIVEAVATAR_BASE_URL", "https://liveavatar.aimiai.com/vih/dispatcher"
)
VOICE_ID = os.getenv("LIVEAVATAR_VOICE_ID", None)
HTTP_PORT = int(os.getenv("TEACHING_HTTP_PORT", "8082"))

# -- DashScope ASR --
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_ASR_MODEL = os.getenv(
    "DASHSCOPE_ASR_MODEL", "qwen3-asr-flash-realtime"
)
DASHSCOPE_ASR_URL = os.getenv(
    "DASHSCOPE_ASR_URL",
    "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
)

# -- LLM --
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv(
    "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
)
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

# -- Course --
_raw_course = os.getenv("TEACHING_COURSE", "thinking")
if not re.search(r'_\d+-\d+$', _raw_course):
    COURSE_NAME = f"{_raw_course}_4-10"
else:
    COURSE_NAME = _raw_course
COURSE_PATH = ROOT / "config" / "courses" / f"{COURSE_NAME}.yaml"


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
    for name in ("httpx", "httpcore", "asyncio", "aiohttp", "websockets",
                 "dashscope.audio.qwen_omni.omni_realtime"):
        logging.getLogger(name).setLevel(logging.WARNING)


def json_response(data, **kwargs):
    from aiohttp import web
    return web.json_response(
        data, dumps=lambda obj: json.dumps(obj, ensure_ascii=False), **kwargs
    )
