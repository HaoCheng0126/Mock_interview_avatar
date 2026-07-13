"""Session lifecycle — global state holders + init/start/stop orchestration."""

from __future__ import annotations

import asyncio
import logging
import uuid

from liveavatar_channel_sdk import (
    AvatarAgent,
    AvatarAgentConfig,
)

from teaching.config import (
    API_KEY,
    AVATAR_ID,
    BASE_URL,
    VOICE_ID,
    DASHSCOPE_API_KEY,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    COURSE_PATH,
    COURSE_NAME,
)
from teaching.asr_manager import QwenAsrManager
from teaching.listener import TeachingListener
from teaching.teaching_controller import (
    TeachingController,
    TeachingState,
    LECTURE_SYSTEM_PROMPT,
)
from teaching.course_manager import CourseManager
from teaching.persona_manager import PersonaManager
from teaching.classmate_engine import ClassmateEngine
from teaching.pacing_engine import PacingEngine
from teaching.manager_agent import ManagerAgent  # legacy alias
from teaching.course_generator import CourseGenerator
from teaching.tts_client import TtsClient
import teaching.ws_patch as _ws_patch

from llm_client import LlmClient


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_agent: AvatarAgent | None = None
_listener: TeachingListener | None = None
_asr_manager: QwenAsrManager | None = None
_llm_client: LlmClient | None = None
_controller: TeachingController | None = None
_course_manager: CourseManager | None = None
_persona: PersonaManager | None = None
_tts_client: TtsClient | None = None

_AGE_EMOJI = {"4-6": "🏃", "7-8": "🕵️", "9-10": "⚔️"}


def _make_classmate_llm(name: str, system_prompt: str) -> LlmClient:
    """Factory: create an LLM client for a classmate."""
    return LlmClient(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL,
                     model=DEEPSEEK_MODEL, system_prompt=system_prompt)


_classmates: ClassmateEngine | None = None
_pacing: PacingEngine | None = None
_manager: ManagerAgent | None = None  # deprecated alias
_generator: CourseGenerator | None = None
_session_info: dict = {}


# -- accessors for routes -----------------------------------------------------

def get_agent() -> AvatarAgent | None:
    return _agent

def get_listener() -> TeachingListener | None:
    return _listener

def get_controller() -> TeachingController | None:
    return _controller

def get_course_manager() -> CourseManager | None:
    return _course_manager

def get_generator() -> CourseGenerator | None:
    return _generator

def get_course_name() -> str:
    return COURSE_NAME

def get_course_path():
    return COURSE_PATH

def get_persona() -> PersonaManager | None:
    return _persona

def get_tts_client() -> TtsClient | None:
    return _tts_client

def get_classmates() -> ClassmateEngine | None:
    return _classmates

def _set_scene_ready_hook(hook) -> None:
    _ws_patch._scene_ready_hook = hook


# ---------------------------------------------------------------------------
# WebSocket broadcast
# ---------------------------------------------------------------------------

import json as _json

_ws_clients: set = set()  # web.WebSocketResponse instances


def ws_register(client) -> None:
    _ws_clients.add(client)


def ws_unregister(client) -> None:
    _ws_clients.discard(client)


async def ws_broadcast(msg: dict) -> None:
    """Push a JSON message to all connected WebSocket clients."""
    if not _ws_clients:
        return
    payload = _json.dumps(msg, ensure_ascii=False)
    dead = set()
    for ws in _ws_clients:
        try:
            await ws.send_str(payload)
        except Exception:
            dead.add(ws)
    for ws in dead:
        _ws_clients.discard(ws)


async def ws_send_state_sync(client) -> None:
    """Send full state snapshot to a newly connected client."""
    ctrl = get_controller()
    if ctrl is None:
        return
    cm = get_course_manager()
    chapter = None
    if ctrl._current_chapter_id and cm:
        for i in range(cm.get_chapter_count()):
            ch = cm.get_chapter_by_index(i)
            if ch and ch["id"] == ctrl._current_chapter_id:
                chapter = ch
                current_idx = i
                break
        else:
            current_idx = 0
    else:
        current_idx = 0

    state = ctrl.state.value
    sync = {
        "type": "state_sync",
        "state": state,
        "currentChapterIndex": current_idx,
        "totalChapters": cm.get_chapter_count() if cm else 0,
        "courseEnded": ctrl._course_closed.is_set(),
    }
    if chapter:
        sync["currentChapter"] = {
            "id": chapter["id"],
            "title": chapter.get("title", ""),
            "skeleton": [
                ctrl._step_text(step) for step in chapter.get("skeleton", [])
            ],
        }
    # Quiz
    if chapter and chapter.get("quiz") and state == "quizzing":
        sync["quiz"] = {
            "question": chapter["quiz"]["question"],
            "options": chapter["quiz"]["options"],
            "chapter_id": chapter["id"],
            "started_at": ctrl._quiz_started_at,
            "timeout_s": 90,
        }
    # Interaction
    if chapter and chapter.get("interaction") and state == "waiting_interact":
        sync["interaction"] = {
            "text": chapter["interaction"]["prompt"],
            "chapter_id": chapter["id"],
        }
    # Visual
    if chapter and chapter.get("visual"):
        ref = chapter["visual"]["ref"]
        if cm:
            for c in cm.get_cards():
                if c["id"] == ref:
                    sync["visual"] = {
                        "type": chapter["visual"]["type"],
                        "id": c.get("id", ""),
                        "title": c.get("title", ""),
                        "content": c.get("content", ""),
                        "image": c.get("image"),
                    }
                    break
    await client.send_str(_json.dumps(sync, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def init_teaching() -> None:
    global _agent, _listener, _asr_manager, _llm_client, _controller
    global _course_manager, _session_info
    logger = logging.getLogger(__name__)

    if not COURSE_PATH.exists():
        raise FileNotFoundError(
            f"Course config not found: {COURSE_PATH}"
        )
    _course_manager = CourseManager(COURSE_PATH)
    course = _course_manager.get_course()
    logger.info(
        "📚 Loaded course: %s (%d chapters)",
        course["title"],
        _course_manager.get_chapter_count(),
    )

    _llm_client = LlmClient(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        model=DEEPSEEK_MODEL,
        system_prompt=LECTURE_SYSTEM_PROMPT,
    )

    use_dev_asr = bool(DASHSCOPE_API_KEY)
    if use_dev_asr:
        _asr_manager = QwenAsrManager(on_transcript=None)
    else:
        logger.warning(
            "⚠️  DASHSCOPE_API_KEY not set -- Platform ASR fallback"
        )
        _asr_manager = None

    _listener = TeachingListener(
        asr_manager=_asr_manager, llm_client=_llm_client
    )
    if _asr_manager:
        _asr_manager._on_transcript = _listener._on_asr_transcript
        _asr_manager._on_speech_started = _listener._on_speech_started
        _asr_manager._on_speech_stopped = _listener._on_speech_stopped
        _asr_manager._on_interim = _listener._on_asr_interim
        _asr_manager._on_error = _listener._on_asr_error
        # ASR connect deferred to start_avatar_session() for faster startup

    # --- v2: Persona + Classmates + Manager ---
    global _persona, _classmates, _pacing, _manager, _generator
    _persona = PersonaManager(_course_manager._raw)
    logger.info("👩‍🏫 Teacher: %s", _persona.teacher_name)
    if _persona.has_classmates:
        logger.info("👦 Classmates: %s", ", ".join(c["name"] for c in _persona.classmates))

    global _tts_client
    _tts_client = TtsClient(api_key=DASHSCOPE_API_KEY)
    _classmates = ClassmateEngine(_persona, _make_classmate_llm, tts_client=_tts_client)
    _pacing = PacingEngine(classmate_engine=_classmates)
    _manager = _pacing  # legacy alias
    _generator = CourseGenerator(_llm_client, COURSE_PATH.parent)

    _agent = None
    _controller = None
    _session_info = {}


async def stop_active_avatar_session() -> None:
    global _agent, _controller, _session_info
    if _controller:
        _controller.stop()
        _controller = None
    if _agent:
        await _agent.stop()
        _agent = None
    if _listener:
        _listener.reset_runtime_state()
    _session_info = {}


def _patch_rest_start(agent: AvatarAgent, speed: float) -> None:
    """Monkey-patch _rest_start to inject voiceConfig.speed into the session/start body."""
    from liveavatar_channel_sdk.session_models import SessionStartResult, SessionStartError
    from liveavatar_channel_sdk.avatar_agent import _ERROR_CODE_MAP, ErrorCode

    async def _patched(self_agent) -> object:
        body: dict = {"avatarId": self_agent._config.avatar_id}
        if self_agent._config.voice_id is not None:
            body["voiceId"] = self_agent._config.voice_id
        body["voiceConfig"] = {"speed": speed}

        resp = await self_agent._http_client.post("/v1/session/start", json=body)
        resp.raise_for_status()
        payload = resp.json()

        code = payload.get("code", -1)
        if code != 0:
            error_code = _ERROR_CODE_MAP.get(code, ErrorCode.SESSION_START_FAILED)
            raise SessionStartError(
                code=code, error_code=error_code,
                message=payload.get("message", "Unknown error"),
            )

        data = payload["data"]
        return SessionStartResult(
            session_id=data["sessionId"],
            sfu_url=data["sfuUrl"],
            user_token=data["userToken"],
            agent_token=data.get("agentToken"),
            agent_ws_url=data.get("agentWsUrl"),
        )

    agent._rest_start = _patched.__get__(agent, type(agent))


async def start_avatar_session() -> dict:
    global _agent, _controller, _session_info
    logger = logging.getLogger(__name__)
    if not _listener or not _course_manager or not _llm_client:
        raise RuntimeError("Teaching agent is not initialized")
    if not API_KEY or not AVATAR_ID:
        raise RuntimeError("LiveAvatar API key or avatar id is missing")

    await stop_active_avatar_session()

    # Ensure ASR is connected (deferred from init_teaching for faster startup)
    if _asr_manager and _asr_manager._conversation is None:
        await _asr_manager.connect()

    config = AvatarAgentConfig(
        api_key=API_KEY,
        avatar_id=AVATAR_ID,
        base_url=BASE_URL,
        developer_asr=bool(DASHSCOPE_API_KEY),
        developer_tts=False,
        voice_id=VOICE_ID,
        timeout=30.0,
    )
    _agent = AvatarAgent(config, _listener)
    _listener.agent = _agent

    # Inject voiceConfig.speed from course persona tts_speed
    tts_speed = float(_persona.teacher_speed) if _persona else 1.0
    _patch_rest_start(_agent, tts_speed)

    result = await _agent.start()
    _session_info = {
        "userToken": result.user_token,
        "sfuUrl": result.sfu_url,
        "sessionId": result.session_id,
    }
    logger.info("📋 sessionId: %s", result.session_id)

    _controller = TeachingController(
        agent=_agent,
        course_manager=_course_manager,
        llm_client=_llm_client,
        persona_manager=_persona,
        manager_agent=_manager,
        pacing_engine=_pacing,
        classmate_engine=_classmates,
    )
    _listener.set_controller(_controller)

    async def _on_scene_ready():
        if _controller and _controller.state == TeachingState.IDLE:
            _controller.start()

    _set_scene_ready_hook(_on_scene_ready)
    return dict(_session_info)


async def shutdown_teaching() -> None:
    global _agent, _asr_manager, _controller
    await stop_active_avatar_session()
    if _asr_manager:
        await _asr_manager.close()
        _asr_manager = None
    logging.getLogger(__name__).info("Teaching shutdown complete")
