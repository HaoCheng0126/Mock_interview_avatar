from interview import agent as interview_agent
from interview.models import InterviewState


class Closable:
    def __init__(self):
        self.closed = False

    async def stop(self):
        self.closed = True

    async def close(self):
        self.closed = True


class Stoppable:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


class StatusController:
    def __init__(self, status):
        self.status = status

    def get_status(self):
        return self.status


async def test_status_returns_idle_without_session():
    await interview_agent.stop_interview_session()

    resp = await interview_agent.handle_interview_status(None)

    assert resp.status == 200
    assert resp.text == '{"state": "idle"}'


def test_interview_agent_does_not_import_teaching_asr_manager():
    source = interview_agent.Path(interview_agent.__file__).read_text(encoding="utf-8")

    assert "teaching.asr_manager" not in source


def test_interview_frontend_does_not_reference_talkshow():
    html = (interview_agent.FRONTEND / "interview.html").read_text(encoding="utf-8")

    assert "talkshow" not in html.lower()


def test_interview_agent_logs_startup_phase_timings():
    source = interview_agent.Path(interview_agent.__file__).read_text(encoding="utf-8")

    assert "asyncio.create_task" in source
    assert "asyncio.gather" in source
    assert "perf_counter" in source
    assert "startup timing" in source
    assert "asr_connect_ms" in source
    assert "avatar_start_ms" in source
    assert "total_ms" in source


def test_interview_agent_hooks_scene_ready_from_ws_protocol():
    source = interview_agent.Path(interview_agent.__file__).read_text(encoding="utf-8")

    assert "_patch_ws_client" not in source
    assert "_scene_ready_hook" not in source


async def test_sdk_js_handler_serves_frontend_sdk():
    resp = await interview_agent.handle_sdk_js(None)

    assert resp.status == 200
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.content_type == "application/javascript"
    assert b"LivekitSDK" in resp.body


async def test_index_handler_disables_browser_cache():
    resp = await interview_agent.handle_index(None)

    assert resp.headers["Cache-Control"] == "no-store"


async def test_audio_input_route_updates_listener_gate():
    await interview_agent.stop_interview_session()
    listener = interview_agent.InterviewListener()
    interview_agent._listener = listener

    class Request:
        async def json(self):
            return {"enabled": True}

    resp = await interview_agent.handle_interview_audio_input(Request())

    assert resp.status == 200
    assert listener.audio_input_enabled is True

    class DisableRequest:
        async def json(self):
            return {"enabled": False}

    resp = await interview_agent.handle_interview_audio_input(DisableRequest())

    assert resp.status == 200
    assert listener.audio_input_enabled is False


async def test_terminal_callback_releases_interview_session_resources():
    await interview_agent.stop_interview_session()
    agent = Closable()
    asr_manager = Closable()
    listener = interview_agent.InterviewListener()
    controller = object()
    interview_agent._agent = agent
    interview_agent._asr_manager = asr_manager
    interview_agent._listener = listener
    interview_agent._controller = controller
    interview_agent._session_info = {"sessionId": "s1"}

    await interview_agent.handle_interview_terminal()

    assert agent.closed is True
    assert asr_manager.closed is True
    assert interview_agent._agent is None
    assert interview_agent._asr_manager is None
    assert interview_agent._listener is None
    assert interview_agent._controller is None
    assert interview_agent._session_info == {}


async def test_terminal_callback_keeps_final_status_after_releasing_resources():
    await interview_agent.stop_interview_session()
    interview_agent._controller = StatusController(
        {"state": "completed", "finalReport": {"summary": "ok"}}
    )
    interview_agent._agent = Closable()
    interview_agent._session_info = {"sessionId": "s1"}

    await interview_agent.handle_interview_terminal()
    resp = await interview_agent.handle_interview_status(None)

    assert resp.text == '{"state": "completed", "finalReport": {"summary": "ok"}}'
    assert interview_agent._controller is None
    assert interview_agent._agent is None


async def test_interview_js_handler_disables_browser_cache():
    resp = await interview_agent.handle_interview_js(None)

    assert resp.headers["Cache-Control"] == "no-store"


def test_session_exposes_asr_availability():
    source = interview_agent.Path(interview_agent.__file__).read_text(encoding="utf-8")

    assert source.count('"asrAvailable"') >= 2  # start-session response + session-info


class IdleController:
    def __init__(self):
        self.state = InterviewState.IDLE
        self.started = False
        self.scene_ready_replayed = False

    async def start(self):
        self.started = True

    async def mark_scene_ready(self):
        self.scene_ready_replayed = True

    def get_status(self):
        return {"state": self.state.value}


class RunningController(IdleController):
    def __init__(self):
        super().__init__()
        self.state = InterviewState.LISTENING


async def test_interview_start_rebuilds_idle_controller_for_fresh_config():
    await interview_agent.stop_interview_session()
    stale = IdleController()
    listener = interview_agent.InterviewListener()
    interview_agent._agent = Closable()
    interview_agent._listener = listener
    interview_agent._controller = stale

    class Request:
        pass

    resp = await interview_agent.handle_interview_start(Request())

    assert resp.status == 200
    # A preheated (idle) session must reload config saved after preheat.
    assert interview_agent._controller is not stale
    assert listener.controller is interview_agent._controller
    await interview_agent.stop_interview_session()


async def test_interview_start_keeps_running_controller():
    await interview_agent.stop_interview_session()
    running = RunningController()
    interview_agent._agent = Closable()
    interview_agent._listener = interview_agent.InterviewListener()
    interview_agent._controller = running

    resp = await interview_agent.handle_interview_start(None)

    assert resp.status == 200
    assert interview_agent._controller is running
    assert running.started is True
    await interview_agent.stop_interview_session()


async def test_interview_start_replays_scene_ready_seen_during_preheat():
    await interview_agent.stop_interview_session()
    running = RunningController()
    listener = interview_agent.InterviewListener()
    await listener.on_scene_ready()  # arrived during preheat, no controller yet
    interview_agent._agent = Closable()
    interview_agent._listener = listener
    interview_agent._controller = running

    await interview_agent.handle_interview_start(None)

    assert listener.scene_ready_seen is True
    assert running.scene_ready_replayed is True
    await interview_agent.stop_interview_session()


async def test_interview_stop_route_releases_session_resources():
    await interview_agent.stop_interview_session()
    agent = Closable()
    controller = Stoppable()
    interview_agent._agent = agent
    interview_agent._controller = controller
    interview_agent._session_info = {"sessionId": "s1"}

    resp = await interview_agent.handle_interview_stop(None)

    assert resp.status == 200
    assert controller.stopped is True
    assert agent.closed is True
    assert interview_agent._agent is None
    assert interview_agent._controller is None
    assert interview_agent._session_info == {}
