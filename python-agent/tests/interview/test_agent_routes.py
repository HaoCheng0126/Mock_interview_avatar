import ast
import asyncio
import inspect
from types import SimpleNamespace

from interview import agent as interview_agent
from interview.asr_manager import QwenAsrManager
from interview.models import InterviewState
from interview.profile_analyzer import CandidateBrief


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

    def begin_stop_report(self):
        self.stopped = True

        async def done():
            return True

        return interview_agent.asyncio.create_task(done())

    def get_status(self):
        return {
            "state": "report_generating",
            "interviewId": "iv_test",
            "reportGeneration": {
                "state": "generating",
                "stage": "preprocessing",
                "percent": 3,
            },
            "transcript": [],
            "finalReport": None,
        }


class StatusController:
    def __init__(self, status):
        self.status = status

    def get_status(self):
        return self.status


class DirectAsrListener:
    def __init__(self):
        self.started = []
        self.stopped = 0
        self.audio_packets = []
        self.caption_sinks = []

    def add_caption_sink(self, sink):
        self.caption_sinks.append(sink)

    def remove_caption_sink(self, sink):
        self.caption_sinks.remove(sink)

    def start_direct_audio_input(self, exchange_id):
        self.started.append(exchange_id)
        return True

    def stop_direct_audio_input(self):
        self.stopped += 1

    def feed_direct_audio(self, packet):
        self.audio_packets.append(packet)


class FakeAsrSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []
        self.closed = False

    async def prepare(self, _request):
        return self

    async def send_json(self, payload, **_kwargs):
        self.sent.append(payload)
        await asyncio.sleep(0)

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(0)
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)


def direct_asr_message(payload):
    return SimpleNamespace(
        type=interview_agent.web.WSMsgType.TEXT,
        data=interview_agent._json.dumps(payload),
    )


async def test_status_returns_idle_without_session():
    await interview_agent.stop_interview_session()

    resp = await interview_agent.handle_interview_status(None)

    assert resp.status == 200
    assert resp.text == '{"state": "idle"}'


async def test_status_can_query_background_report_by_interview_id():
    report_status = {
        "state": "report_generating",
        "interviewId": "iv_background",
        "reportGeneration": {"state": "generating", "percent": 42},
        "transcript": [],
        "finalReport": None,
    }
    active_status = {
        "state": "listening",
        "interviewId": "iv_active",
        "transcript": [],
        "finalReport": None,
    }
    interview_agent._report_controllers["iv_background"] = StatusController(
        report_status
    )
    interview_agent._report_contexts["iv_background"] = {}
    interview_agent._controller = StatusController(active_status)

    class Request:
        query = {"interviewId": "iv_background"}
        cookies = {}

    response = await interview_agent.handle_interview_status(Request())

    assert response.status == 200
    assert '"interviewId": "iv_background"' in response.text
    assert '"percent": 42' in response.text
    interview_agent._controller = None
    interview_agent._report_controllers.pop("iv_background", None)
    interview_agent._report_contexts.pop("iv_background", None)


def test_interview_agent_does_not_import_teaching_asr_manager():
    source = interview_agent.Path(interview_agent.__file__).read_text(encoding="utf-8")

    assert "teaching.asr_manager" not in source


def test_interview_asr_has_no_text_corpus_builder():
    agent_source = interview_agent.Path(interview_agent.__file__).read_text(encoding="utf-8")
    asr_source = (
        interview_agent.Path(interview_agent.__file__).parent / "asr_manager.py"
    ).read_text(encoding="utf-8")

    assert "_build_asr_corpus" not in agent_source
    assert "corpus_text=" not in agent_source
    assert "corpus_text=None" in asr_source


def test_asr_constructors_cannot_receive_interview_text_context():
    assert list(inspect.signature(interview_agent._build_asr_manager).parameters) == [
        "listener"
    ]
    assert "corpus_text" not in inspect.signature(QwenAsrManager).parameters
    for filename in ("asr_manager.py", "listener.py", "volcano_asr.py"):
        source = (
            interview_agent.Path(interview_agent.__file__).parent / filename
        ).read_text(encoding="utf-8")
        names = {
            node.id.lower()
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Name)
        }
        assert not names.intersection(
            {"jd_text", "resume_text", "question_bank", "knowledge_base"}
        )


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


async def test_report_debug_handler_disables_browser_cache():
    resp = await interview_agent.handle_report_debug(None)

    assert resp.headers["Cache-Control"] == "no-store"


async def test_audio_input_route_updates_listener_gate():
    await interview_agent.stop_interview_session()
    listener = interview_agent.InterviewListener()
    listener.set_controller(
        type(
            "Controller",
            (),
            {
                "state": InterviewState.LISTENING,
                "current_answer_metadata": lambda self: {"exchangeId": "ex_001"},
            },
        )()
    )
    interview_agent._listener = listener

    class Request:
        async def json(self):
            return {"enabled": True, "exchangeId": "ex_001"}

    resp = await interview_agent.handle_interview_audio_input(Request())

    assert resp.status == 200
    assert listener.audio_input_enabled is True

    class DisableRequest:
        async def json(self):
            return {"enabled": False}

    resp = await interview_agent.handle_interview_audio_input(DisableRequest())

    assert resp.status == 200
    assert listener.audio_input_enabled is False


async def test_old_page_stop_beacon_cannot_stop_new_session():
    await interview_agent.stop_interview_session()
    agent = Closable()
    controller = Stoppable()
    interview_agent._agent = agent
    interview_agent._controller = controller
    interview_agent._session_info = {"sessionId": "session_new"}

    class Request:
        query = {"sessionId": "session_old"}

    response = await interview_agent.handle_stop_session(Request())

    assert response.status == 200
    assert "staleSessionIgnored" in response.text
    assert agent.closed is False
    assert interview_agent._controller is controller
    await interview_agent.release_interview_session_resources()


async def test_explicit_page_close_releases_enterprise_session():
    await interview_agent.stop_interview_session()
    agent = Closable()
    controller = Stoppable()
    interview_agent._agent = agent
    interview_agent._controller = controller
    interview_agent._enterprise_record_id = "enterprise_old"
    interview_agent._session_info = {"sessionId": "session_enterprise"}

    class Request:
        query = {"sessionId": "session_enterprise", "release": "1"}

    response = await interview_agent.handle_stop_session(Request())

    assert response.status == 200
    assert agent.closed is True
    assert interview_agent._agent is None
    assert interview_agent._enterprise_record_id == ""


async def test_transient_enterprise_disconnect_still_preserves_session():
    await interview_agent.stop_interview_session()
    agent = Closable()
    controller = Stoppable()
    interview_agent._agent = agent
    interview_agent._controller = controller
    interview_agent._enterprise_record_id = "enterprise_old"
    interview_agent._session_info = {"sessionId": "session_enterprise"}

    class Request:
        query = {"sessionId": "session_enterprise"}

    response = await interview_agent.handle_stop_session(Request())

    assert response.status == 200
    assert "preserved" in response.text
    assert agent.closed is False
    assert interview_agent._enterprise_record_id == "enterprise_old"
    await interview_agent.stop_interview_session()


async def test_direct_asr_stop_without_flush_stops_immediately(monkeypatch):
    listener = DirectAsrListener()
    socket = FakeAsrSocket(
        [
            direct_asr_message(
                {"type": "start", "sampleRate": 16000, "exchangeId": "ex_001"}
            ),
            direct_asr_message({"type": "stop", "flushFinal": False}),
        ]
    )
    monkeypatch.setattr(
        interview_agent.web, "WebSocketResponse", lambda **_kwargs: socket
    )
    originals = (
        interview_agent._listener,
        interview_agent._asr_manager,
        interview_agent._session_info,
    )
    interview_agent._listener = listener
    interview_agent._asr_manager = object()
    interview_agent._session_info = {"sessionId": "session_1"}

    try:
        await interview_agent.handle_interview_asr_socket(
            SimpleNamespace(query={"sessionId": "session_1"})
        )
    finally:
        (
            interview_agent._listener,
            interview_agent._asr_manager,
            interview_agent._session_info,
        ) = originals

    assert listener.started == ["ex_001"]
    assert listener.stopped == 1
    assert listener.audio_packets == []
    assert {"type": "started", "exchangeId": "ex_001"} in socket.sent
    assert {"type": "stopped", "exchangeId": "ex_001"} in socket.sent


async def test_direct_asr_flush_is_cancelled_by_next_exchange(monkeypatch):
    listener = DirectAsrListener()
    socket = FakeAsrSocket(
        [
            direct_asr_message(
                {"type": "start", "sampleRate": 16000, "exchangeId": "ex_001"}
            ),
            direct_asr_message({"type": "stop", "flushFinal": True}),
            direct_asr_message(
                {"type": "start", "sampleRate": 16000, "exchangeId": "ex_002"}
            ),
            direct_asr_message({"type": "stop", "flushFinal": False}),
        ]
    )
    monkeypatch.setattr(
        interview_agent.web, "WebSocketResponse", lambda **_kwargs: socket
    )
    originals = (
        interview_agent._listener,
        interview_agent._asr_manager,
        interview_agent._session_info,
    )
    interview_agent._listener = listener
    interview_agent._asr_manager = object()
    interview_agent._session_info = {"sessionId": "session_1"}

    try:
        await interview_agent.handle_interview_asr_socket(
            SimpleNamespace(query={"sessionId": "session_1"})
        )
    finally:
        (
            interview_agent._listener,
            interview_agent._asr_manager,
            interview_agent._session_info,
        ) = originals

    assert listener.started == ["ex_001", "ex_002"]
    assert listener.stopped == 2
    assert 0 < len(listener.audio_packets) < 35
    assert {"type": "started", "exchangeId": "ex_002"} in socket.sent
    assert {"type": "stopped", "exchangeId": "ex_002"} in socket.sent


def test_generate_jd_route_is_removed():
    source = interview_agent.Path(interview_agent.__file__).read_text(encoding="utf-8")
    assert "/api/interview/generate-jd" not in source
    assert "handle_generate_jd" not in source


def test_report_retry_route_is_registered():
    source = interview_agent.Path(interview_agent.__file__).read_text(encoding="utf-8")
    assert '"/api/interview/report/retry"' in source
    assert "handle_interview_report_retry" in source


async def test_profile_route_keeps_missing_jd_empty_and_builds_brief_once():
    calls = []

    async def fake_analyze():
        calls.append(interview_agent._candidate_profile)
        return CandidateBrief(
            target_role="产品经理",
            has_jd=False,
            candidate_summary="有增长项目经验",
        )

    original_analyze = interview_agent.analyze_current_candidate
    original_profile = interview_agent._candidate_profile
    original_brief = interview_agent._candidate_brief
    interview_agent.analyze_current_candidate = fake_analyze

    class Request:
        async def post(self):
            return {
                "target_role": "产品经理",
                "jd_text": "",
                "resume_text": "负责增长项目",
                "resume_file": None,
            }

    try:
        resp = await interview_agent.handle_post_profile(Request())
        assert resp.status == 200
        assert interview_agent._candidate_profile.jd_text == ""
        assert interview_agent._candidate_brief.has_jd is False
        assert len(calls) == 1
        assert '"has_jd": false' in resp.text
        assert "jd_auto_generated" not in resp.text
        assert "jd_source" not in resp.text
    finally:
        interview_agent.analyze_current_candidate = original_analyze
        interview_agent._candidate_profile = original_profile
        interview_agent._candidate_brief = original_brief


async def test_enterprise_profile_uses_invitation_role_and_jd_not_candidate_form():
    assigned_jd = "招聘方预设的数字人 AI 产品岗位 JD"
    marked = []

    async def fake_enterprise_record(_request):
        return {
            "id": "ent_test",
            "target_role": "数字人AI产品",
            "jd_text": assigned_jd,
        }

    async def fake_analyze():
        return CandidateBrief(
            target_role=interview_agent._candidate_profile.target_role,
            has_jd=True,
            candidate_summary="候选人画像",
        )

    async def fake_company_knowledge():
        return ("", "")

    class FakeStore:
        def mark_in_progress(self, record_id, **kwargs):
            marked.append((record_id, kwargs))

    class Request:
        async def post(self):
            return {
                "enterprise": "true",
                "target_role": "候选人试图修改的岗位",
                "jd_text": "候选人试图修改的 JD",
                "resume_text": "候选人简历",
                "resume_file": None,
                "candidate_name": "张三",
                "candidate_contact": "zhang@example.com",
            }

    originals = (
        interview_agent._enterprise_record,
        interview_agent.analyze_current_candidate,
        interview_agent.analyze_current_company_knowledge,
        interview_agent._enterprise_store,
        interview_agent._candidate_profile,
        interview_agent._candidate_brief,
    )
    interview_agent._enterprise_record = fake_enterprise_record
    interview_agent.analyze_current_candidate = fake_analyze
    interview_agent.analyze_current_company_knowledge = fake_company_knowledge
    interview_agent._enterprise_store = FakeStore()
    try:
        resp = await interview_agent.handle_post_profile(Request())
        assert resp.status == 200
        assert interview_agent._candidate_profile.target_role == "数字人AI产品"
        assert interview_agent._candidate_profile.jd_text == assigned_jd
        assert interview_agent._candidate_profile.resume_text == ""
        assert marked == [
            (
                "ent_test",
                {
                    "candidate_name": "张三",
                    "candidate_contact": "zhang@example.com",
                },
            )
        ]
    finally:
        (
            interview_agent._enterprise_record,
            interview_agent.analyze_current_candidate,
            interview_agent.analyze_current_company_knowledge,
            interview_agent._enterprise_store,
            interview_agent._candidate_profile,
            interview_agent._candidate_brief,
        ) = originals


async def test_practice_profile_ignores_stale_enterprise_cookie():
    async def stale_enterprise_record(_request):
        return {
            "id": "old-enterprise-session",
            "target_role": "不应使用的企业岗位",
            "jd_text": "不应使用的企业 JD",
        }

    async def fake_analyze():
        return CandidateBrief(
            target_role=interview_agent._candidate_profile.target_role,
            has_jd=False,
            candidate_summary="C 端候选人",
        )

    async def fake_company_knowledge():
        return ("", "")

    class Request:
        cookies = {interview_agent.ENTERPRISE_COOKIE: "stale-cookie"}

        async def post(self):
            return {
                "enterprise": "false",
                "target_role": "产品经理",
                "jd_text": "",
                "resume_text": "三年产品经验",
                "resume_file": None,
            }

    originals = (
        interview_agent._enterprise_record,
        interview_agent.analyze_current_candidate,
        interview_agent.analyze_current_company_knowledge,
        interview_agent._candidate_profile,
        interview_agent._candidate_brief,
    )
    interview_agent._enterprise_record = stale_enterprise_record
    interview_agent.analyze_current_candidate = fake_analyze
    interview_agent.analyze_current_company_knowledge = fake_company_knowledge
    try:
        response = await interview_agent.handle_post_profile(Request())
        assert response.status == 200
        assert interview_agent._candidate_profile.target_role == "产品经理"
        assert interview_agent._candidate_profile.resume_text == "三年产品经验"
    finally:
        (
            interview_agent._enterprise_record,
            interview_agent.analyze_current_candidate,
            interview_agent.analyze_current_company_knowledge,
            interview_agent._candidate_profile,
            interview_agent._candidate_brief,
        ) = originals


async def test_stop_session_drops_source_profile_and_brief():
    interview_agent._candidate_profile = interview_agent.CandidateProfile(
        target_role="后端", resume_text="敏感简历原文"
    )
    interview_agent._candidate_brief = CandidateBrief(
        target_role="后端", candidate_summary="精简画像"
    )
    resp = await interview_agent.handle_stop_session(None)
    assert resp.status == 200
    assert interview_agent._candidate_profile.is_empty()
    assert interview_agent._candidate_brief is None


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


def test_python314_tcp_keepalive_failure_is_non_fatal(monkeypatch):
    def fail(_transport):
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(interview_agent, "_aiohttp_tcp_keepalive", fail)
    interview_agent._safe_aiohttp_tcp_keepalive(object())


async def test_asr_worklet_handler_serves_low_latency_capture_processor():
    resp = await interview_agent.handle_asr_worklet_js(None)

    assert resp.status == 200
    assert resp.headers["Cache-Control"] == "no-store"
    worklet = (interview_agent.FRONTEND / "asr-worklet.js").read_text(encoding="utf-8")
    assert 'registerProcessor("pcm16k-capture"' in worklet


def test_session_exposes_asr_availability():
    source = interview_agent.Path(interview_agent.__file__).read_text(encoding="utf-8")

    assert source.count('"asrAvailable"') >= 2  # start-session response + session-info
    assert '"directAsrAvailable"' in source
    assert '"/ws/interview/asr"' in source


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

    assert resp.status == 202
    assert controller.stopped is True
    assert agent.closed is True
    assert interview_agent._agent is None
    assert interview_agent._controller is None
    assert interview_agent._report_controllers["iv_test"] is controller
    assert interview_agent._session_info == {}
    assert '"reportPending": true' in resp.text
    interview_agent._report_controllers.pop("iv_test", None)
    interview_agent._report_contexts.pop("iv_test", None)


async def test_new_session_can_start_while_previous_report_is_generating(monkeypatch):
    await interview_agent.stop_interview_session()

    class ReportController:
        state = InterviewState.REPORT_GENERATING
        _report_task = None

        def get_status(self):
            return {
                "state": "report_generating",
                "interviewId": "iv_previous",
                "transcript": [{"type": "answer", "text": "回答"}],
            }

    interview_agent._controller = ReportController()

    async def fake_start(_avatar_slug, enterprise_record_id=""):
        interview_agent._session_info = {
            "sessionId": "session_next",
            "asrAvailable": False,
            "avatar": {},
        }
        interview_agent._agent = Closable()
        return "token", "sfu"

    monkeypatch.setattr(interview_agent, "start_interview_session", fake_start)

    class Request:
        async def json(self):
            return {"avatar": "default", "enterprise": False}

    resp = await interview_agent.handle_start_session(Request())

    assert resp.status == 200
    assert "session_next" in resp.text
    assert "iv_previous" in interview_agent._report_controllers
    await interview_agent.release_interview_session_resources()
    interview_agent._report_controllers.pop("iv_previous", None)
    interview_agent._report_contexts.pop("iv_previous", None)


async def test_new_page_reclaims_idle_enterprise_room(monkeypatch):
    await interview_agent.stop_interview_session()

    class IdleController:
        state = InterviewState.IDLE

        def get_status(self):
            return {"state": "idle", "transcript": []}

    old_agent = Closable()
    interview_agent._agent = old_agent
    interview_agent._controller = IdleController()
    interview_agent._enterprise_record_id = "enterprise_old"
    interview_agent._session_info = {"sessionId": "session_old"}

    async def fake_start(_avatar_slug, enterprise_record_id=""):
        interview_agent._session_info = {
            "sessionId": "session_new",
            "asrAvailable": False,
            "avatar": {},
        }
        interview_agent._agent = Closable()
        return "token", "sfu"

    monkeypatch.setattr(interview_agent, "start_interview_session", fake_start)

    class Request:
        async def json(self):
            return {"avatar": "default", "enterprise": False}

    response = await interview_agent.handle_start_session(Request())

    assert response.status == 200
    assert old_agent.closed is True
    assert "session_new" in response.text
    await interview_agent.stop_interview_session()


async def test_empty_report_error_is_discarded_before_new_session(monkeypatch):
    await interview_agent.stop_interview_session()

    class EmptyFailedController:
        state = InterviewState.REPORT_ERROR

        def get_status(self):
            return {"state": "report_error", "transcript": [], "finalReport": None}

    old_agent = Closable()
    interview_agent._controller = EmptyFailedController()
    interview_agent._agent = old_agent

    async def fake_start(_avatar_slug, enterprise_record_id=""):
        interview_agent._session_info = {
            "sessionId": "new_session",
            "asrAvailable": False,
            "avatar": {},
        }
        interview_agent._agent = Closable()
        return "token", "sfu"

    monkeypatch.setattr(interview_agent, "start_interview_session", fake_start)

    class Request:
        async def json(self):
            return {"avatar": "default", "enterprise": False}

    response = await interview_agent.handle_start_session(Request())

    assert response.status == 200
    assert old_agent.closed is True
    assert "new_session" in response.text
    await interview_agent.release_interview_session_resources()
    interview_agent._report_controllers.pop("", None)


async def test_abandoned_preheat_does_not_generate_report_on_teardown():
    await interview_agent.stop_interview_session()

    class PreheatedController:
        state = InterviewState.IDLE

        def __init__(self):
            self.stop_called = False

        def get_status(self):
            return {"state": "idle", "transcript": []}

        async def stop(self):
            self.stop_called = True

    controller = PreheatedController()
    interview_agent._controller = controller
    interview_agent._agent = Closable()

    await interview_agent.stop_interview_session()

    assert controller.stop_called is False
