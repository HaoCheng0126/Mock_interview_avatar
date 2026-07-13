"""Tests for teaching_agent session startup behavior."""

import pytest

import teaching.session as _sess


class FakeStartResult:
    user_token = "user-token"
    sfu_url = "sfu-url"
    session_id = "session-id"


class FakeAvatarAgent:
    instances = []

    def __init__(self, config, listener):
        self.config = config
        self._config = config     # used by _patch_rest_start monkey-patch
        self.listener = listener
        self.stopped = False
        FakeAvatarAgent.instances.append(self)

    async def start(self):
        return FakeStartResult()

    async def stop(self):
        self.stopped = True

    # Stub _rest_start for monkey-patch in start_avatar_session
    async def _rest_start(self):
        return FakeStartResult()


class FakeListener:
    def __init__(self):
        self.agent = None
        self.controller = None
        self.reset_count = 0

    def reset_runtime_state(self):
        self.reset_count += 1

    def set_controller(self, controller):
        self.controller = controller


class FakeCourseManager:
    pass


class FakeLlmClient:
    pass


def _reset_globals():
    _sess._agent = None
    _sess._listener = None
    _sess._controller = None
    _sess._course_manager = None
    _sess._llm_client = None
    _sess._persona = None
    _sess._manager = None
    _sess._pacing = None
    _sess._classmates = None
    _sess._session_info = {}
    _sess._set_scene_ready_hook(None)
    FakeAvatarAgent.instances = []


@pytest.mark.asyncio
async def test_start_avatar_session_creates_fresh_room(monkeypatch):
    _reset_globals()
    listener = FakeListener()
    monkeypatch.setattr(_sess, "AvatarAgent", FakeAvatarAgent)
    monkeypatch.setattr(_sess, "API_KEY", "api-key")
    monkeypatch.setattr(_sess, "AVATAR_ID", "avatar-id")
    _sess._listener = listener
    _sess._course_manager = FakeCourseManager()
    _sess._llm_client = FakeLlmClient()

    result = await _sess.start_avatar_session()

    assert result == {
        "userToken": "user-token",
        "sfuUrl": "sfu-url",
        "sessionId": "session-id",
    }
    assert _sess._session_info == result
    assert _sess._agent is FakeAvatarAgent.instances[-1]
    assert listener.agent is _sess._agent
    assert listener.controller is _sess._controller
    from teaching.ws_patch import _scene_ready_hook
    assert _scene_ready_hook is not None


@pytest.mark.asyncio
async def test_start_avatar_session_stops_previous_session(monkeypatch):
    _reset_globals()
    old_agent = FakeAvatarAgent(None, None)
    _sess._agent = old_agent
    _sess._controller = type("FakeController", (), {"stop": lambda self: None})()
    _sess._listener = FakeListener()
    _sess._course_manager = FakeCourseManager()
    _sess._llm_client = FakeLlmClient()
    monkeypatch.setattr(_sess, "AvatarAgent", FakeAvatarAgent)
    monkeypatch.setattr(_sess, "API_KEY", "api-key")
    monkeypatch.setattr(_sess, "AVATAR_ID", "avatar-id")

    await _sess.start_avatar_session()

    assert old_agent.stopped is True
    assert _sess._agent is not old_agent
