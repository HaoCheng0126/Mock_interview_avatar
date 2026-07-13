# Interview Avatar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first usable `interview` digital human mode that asks interview questions, waits for candidate voice answers, tracks question/exchange IDs with SDK metadata, evaluates answers, asks bounded follow-ups, and produces a final report.

**Architecture:** Add an independent `interview/` package following the `talkshow` session/route shape and the `teaching` ASR-gating pattern. Keep product state in `InterviewController`, config in `InterviewManager`, LLM-facing logic in small planner/evaluator/report modules, and LiveAvatar event bridging in `InterviewListener`.

**Tech Stack:** Python 3.14 runtime in this workspace, aiohttp, PyYAML, existing `LlmClient`, upgraded `liveavatar-channel-sdk==0.2.5`, pytest, pytest-asyncio.

## Global Constraints

- This is a new mode, not a prompt-only variant of `chat`.
- Phase one focuses on one interviewer and one candidate.
- Candidate answers by voice using Developer ASR events.
- Avatar speech uses platform TTS through `agent.send_prompt(text, metadata=...)`.
- Every event belonging to a business exchange carries the same metadata IDs in `data`.
- Runtime feedback is short; detailed feedback is generated at the end.
- Out of scope: resume file upload, ATS workflows, multiple interviewers, visual candidate analysis, live score display.
- Current `/Users/luffer/workspace/liveavatar-ws-integration-demo/python-agent` is not a git repository, so task commit steps are replaced by `git status` verification only.

---

## File Structure

- Create `interview/__init__.py`: package marker.
- Create `interview/models.py`: dataclasses and enums for config, questions, exchanges, evaluations, reports, and state.
- Create `interview/interview_manager.py`: YAML config loader and validation.
- Create `interview/question_planner.py`: deterministic first-pass question/follow-up planning with optional LLM hooks.
- Create `interview/answer_evaluator.py`: JSON evaluation parser and fallback evaluator.
- Create `interview/report_generator.py`: final report assembly from recorded exchanges.
- Create `interview/controller.py`: state machine, prompt metadata, answer handling, follow-up loop, final report.
- Create `interview/listener.py`: LiveAvatar listener, Developer ASR callbacks, ASR gating, metadata propagation.
- Create `interview/agent.py`: aiohttp app, LiveAvatar session lifecycle, routes.
- Create `config/interview.yaml`: default interview profile.
- Create `frontend/interview.html`: minimal operator UI.
- Create `tests/interview/`: focused tests for each unit and route surface.
- Modify `requirements.txt`: require upgraded local/PyPI SDK version if pinned.
- Modify `README.md`: add interview mode run instructions after implementation.

---

### Task 1: Interview Models And Config Loader

**Files:**
- Create: `interview/__init__.py`
- Create: `interview/models.py`
- Create: `interview/interview_manager.py`
- Create: `config/interview.yaml`
- Create: `tests/interview/__init__.py`
- Create: `tests/interview/test_interview_manager.py`

**Interfaces:**
- Produces: `InterviewState`, `InterviewConfig`, `QuestionSpec`, `Exchange`, `Evaluation`, `InterviewReport`.
- Produces: `InterviewManager(config_path).config`, `.get_question_specs()`, `.build_opening_text()`.
- Consumes: PyYAML only.

- [ ] **Step 1: Write the failing config loader tests**

Add `tests/interview/test_interview_manager.py`:

```python
from pathlib import Path

import pytest

from interview.interview_manager import InterviewManager


def test_loads_default_interview_config():
    manager = InterviewManager(Path("config/interview.yaml"))

    assert manager.config.title == "Python 后端工程师模拟面试"
    assert manager.config.lang == "zh"
    assert manager.config.max_probe_per_question == 2
    assert manager.config.interviewer.name == "林面试官"
    assert manager.get_question_specs()[0].section_id == "project_deep_dive"
    assert manager.get_question_specs()[0].question_id == "q_project_deep_dive_001"


def test_build_opening_text_mentions_role_and_duration():
    manager = InterviewManager(Path("config/interview.yaml"))

    text = manager.build_opening_text()

    assert "Python 后端工程师" in text
    assert "20 分钟" in text
    assert "准备好了" in text


def test_rejects_config_without_required_question_sets(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        """
interview:
  title: "Bad"
interviewer:
  name: "面试官"
candidate:
  target_role: "Backend"
question_sets: []
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="question_sets"):
        InterviewManager(path)
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/interview/test_interview_manager.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'interview'`.

- [ ] **Step 3: Implement models and manager**

Create `interview/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class InterviewState(str, Enum):
    IDLE = "idle"
    OPENING = "opening"
    ASKING = "asking"
    LISTENING = "listening"
    ANALYZING = "analyzing"
    PROBING = "probing"
    TRANSITIONING = "transitioning"
    CLOSING = "closing"
    COMPLETED = "completed"


@dataclass
class InterviewerConfig:
    name: str
    style: str = ""
    rules: list[str] = field(default_factory=list)


@dataclass
class CandidateConfig:
    target_role: str
    background: str = ""


@dataclass
class QuestionSpec:
    section_id: str
    section_title: str
    question_id: str
    prompt: str
    required: bool = True


@dataclass
class InterviewConfig:
    title: str
    lang: str
    duration_minutes: int
    difficulty: str
    max_probe_per_question: int
    interviewer: InterviewerConfig
    candidate: CandidateConfig
    rubric_dimensions: list[str]
    questions: list[QuestionSpec]


@dataclass
class Evaluation:
    score: int
    dimensions: dict[str, int]
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    follow_up_needed: bool = False
    follow_up_question: str = ""


@dataclass
class Exchange:
    exchange_id: str
    question_id: str
    section_id: str
    type: str
    prompt_id: str
    prompt_text: str
    prompt_type: str
    parent_exchange_id: str | None = None
    probe_index: int = 0
    answer_request_id: str | None = None
    answer_text: str = ""
    evaluation: Evaluation | None = None


@dataclass
class InterviewReport:
    summary: str
    overall_score: int
    strengths: list[str]
    weaknesses: list[str]
    recommendations: list[str]
    exchanges: list[Exchange]
```

Create `interview/interview_manager.py`:

```python
from __future__ import annotations

from pathlib import Path

import yaml

from interview.models import (
    CandidateConfig,
    InterviewConfig,
    InterviewerConfig,
    QuestionSpec,
)


class InterviewManager:
    def __init__(self, config_path: Path | str) -> None:
        self._config_path = Path(config_path)
        self.config = self._load()

    def get_question_specs(self) -> list[QuestionSpec]:
        return list(self.config.questions)

    def build_opening_text(self) -> str:
        cfg = self.config
        return (
            f"你好，我是{cfg.interviewer.name}。"
            f"接下来我们会进行一场约 {cfg.duration_minutes} 分钟的"
            f"{cfg.candidate.target_role}模拟面试。"
            "我会一次只问一个问题，并根据你的回答适度追问。"
            "准备好了我们就开始。"
        )

    def _load(self) -> InterviewConfig:
        raw = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
        interview = raw.get("interview") or {}
        interviewer_raw = raw.get("interviewer") or {}
        candidate_raw = raw.get("candidate") or {}
        question_sets = raw.get("question_sets") or []
        if not question_sets:
            raise ValueError("question_sets must contain at least one question")

        questions = []
        for index, item in enumerate(question_sets, start=1):
            section_id = str(item.get("id") or f"section_{index}")
            questions.append(
                QuestionSpec(
                    section_id=section_id,
                    section_title=str(item.get("title") or section_id),
                    question_id=str(item.get("question_id") or f"q_{section_id}_{index:03d}"),
                    prompt=str(item.get("prompt") or item.get("title") or section_id),
                    required=bool(item.get("required", True)),
                )
            )

        rubric = raw.get("rubric") or {}
        return InterviewConfig(
            title=str(interview.get("title") or "Interview"),
            lang=str(interview.get("lang") or "zh"),
            duration_minutes=int(interview.get("duration_minutes") or 20),
            difficulty=str(interview.get("difficulty") or "mid"),
            max_probe_per_question=int(interview.get("max_probe_per_question") or 2),
            interviewer=InterviewerConfig(
                name=str(interviewer_raw.get("name") or "面试官"),
                style=str(interviewer_raw.get("style") or ""),
                rules=[str(rule) for rule in interviewer_raw.get("rules") or []],
            ),
            candidate=CandidateConfig(
                target_role=str(candidate_raw.get("target_role") or "候选人"),
                background=str(candidate_raw.get("background") or ""),
            ),
            rubric_dimensions=[str(item) for item in rubric.get("dimensions") or []],
            questions=questions,
        )
```

Create `config/interview.yaml` with the sample from the spec plus explicit prompts:

```yaml
interview:
  title: "Python 后端工程师模拟面试"
  lang: zh
  duration_minutes: 20
  difficulty: mid
  max_probe_per_question: 2

interviewer:
  name: "林面试官"
  style: "专业、克制、适度追问，不打断候选人"
  rules:
    - "一次只问一个问题"
    - "追问最多连续两次"
    - "不直接给标准答案"
    - "结束后再集中反馈"

candidate:
  target_role: "Python 后端工程师"
  background: "有 3 年后端经验，熟悉 FastAPI、MySQL、Redis"

rubric:
  dimensions:
    - technical_depth
    - problem_solving
    - communication
    - project_ownership
    - reliability_awareness

question_sets:
  - id: project_deep_dive
    question_id: q_project_deep_dive_001
    title: "项目深挖"
    prompt: "请介绍一个你最近主导或深度参与的后端项目，重点讲你负责的部分、技术难点和最终结果。"
    required: true
  - id: python_backend
    question_id: q_python_backend_001
    title: "后端技术"
    prompt: "请说明你在 Python 后端服务中如何处理接口性能和可靠性问题。"
    required: true
  - id: behavior
    question_id: q_behavior_001
    title: "行为面试"
    prompt: "请讲一次你和他人对技术方案有分歧的经历，以及你是如何推进的。"
    required: false
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
pytest tests/interview/test_interview_manager.py -q
```

Expected: PASS.

- [ ] **Step 5: Verify repository state**

Run:

```bash
git status --short
```

Expected in this workspace: `fatal: not a git repository` or changed files listed if the repo is later initialized.

---

### Task 2: Planner, Evaluator, And Report Generator

**Files:**
- Create: `interview/question_planner.py`
- Create: `interview/answer_evaluator.py`
- Create: `interview/report_generator.py`
- Create: `tests/interview/test_question_planner.py`
- Create: `tests/interview/test_answer_evaluator.py`
- Create: `tests/interview/test_report_generator.py`

**Interfaces:**
- Consumes: `QuestionSpec`, `Exchange`, `Evaluation`, `InterviewReport`.
- Produces: `QuestionPlanner.next_question(asked_question_ids: set[str]) -> QuestionSpec | None`.
- Produces: `QuestionPlanner.follow_up_from(evaluation: Evaluation) -> str | None`.
- Produces: `AnswerEvaluator.evaluate(question_text: str, answer_text: str) -> Evaluation`.
- Produces: `ReportGenerator.generate(exchanges: list[Exchange]) -> InterviewReport`.

- [ ] **Step 1: Write failing tests**

Add `tests/interview/test_question_planner.py`:

```python
from interview.models import Evaluation, QuestionSpec
from interview.question_planner import QuestionPlanner


def test_next_question_returns_first_unasked_required_question():
    planner = QuestionPlanner([
        QuestionSpec("project", "项目", "q1", "项目问题", True),
        QuestionSpec("backend", "后端", "q2", "后端问题", True),
    ])

    assert planner.next_question(set()).question_id == "q1"
    assert planner.next_question({"q1"}).question_id == "q2"
    assert planner.next_question({"q1", "q2"}) is None


def test_follow_up_uses_evaluator_question_when_needed():
    evaluation = Evaluation(
        score=3,
        dimensions={},
        follow_up_needed=True,
        follow_up_question="你如何保证幂等？",
    )

    assert QuestionPlanner([]).follow_up_from(evaluation) == "你如何保证幂等？"
```

Add `tests/interview/test_answer_evaluator.py`:

```python
from interview.answer_evaluator import AnswerEvaluator


class FakeLlm:
    def __init__(self, text):
        self.text = text

    async def generate(self, prompt, max_tokens=512):
        return self.text


async def test_evaluator_parses_json_response():
    evaluator = AnswerEvaluator(FakeLlm("""
{
  "score": 4,
  "dimensions": {"depth": 4},
  "strengths": ["结构清楚"],
  "weaknesses": ["缺少数据"],
  "followUpNeeded": true,
  "followUpQuestion": "具体指标是什么？"
}
"""))

    result = await evaluator.evaluate("项目问题", "我做了订单系统")

    assert result.score == 4
    assert result.dimensions["depth"] == 4
    assert result.follow_up_needed is True
    assert result.follow_up_question == "具体指标是什么？"


async def test_evaluator_falls_back_for_invalid_json():
    evaluator = AnswerEvaluator(FakeLlm("not json"))

    result = await evaluator.evaluate("项目问题", "简短回答")

    assert result.score == 2
    assert result.follow_up_needed is True
    assert "再具体" in result.follow_up_question
```

Add `tests/interview/test_report_generator.py`:

```python
from interview.models import Evaluation, Exchange
from interview.report_generator import ReportGenerator


def test_report_summarizes_exchanges():
    exchange = Exchange(
        exchange_id="ex_001",
        question_id="q1",
        section_id="project",
        type="main_question",
        prompt_id="prompt_001",
        prompt_text="项目问题",
        prompt_type="main_question",
        answer_text="我做了订单系统",
        evaluation=Evaluation(
            score=4,
            dimensions={"depth": 4},
            strengths=["结构清楚"],
            weaknesses=["缺少数据"],
        ),
    )

    report = ReportGenerator().generate([exchange])

    assert report.overall_score == 4
    assert "结构清楚" in report.strengths
    assert "缺少数据" in report.weaknesses
    assert report.exchanges == [exchange]
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/interview/test_question_planner.py tests/interview/test_answer_evaluator.py tests/interview/test_report_generator.py -q
```

Expected: FAIL with missing modules.

- [ ] **Step 3: Implement minimal modules**

Implement deterministic planner, JSON parser with fallback, and simple report aggregation. Do not add LLM planning yet.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
pytest tests/interview/test_question_planner.py tests/interview/test_answer_evaluator.py tests/interview/test_report_generator.py -q
```

Expected: PASS.

---

### Task 3: Interview Controller State Machine

**Files:**
- Create: `interview/controller.py`
- Create: `tests/interview/test_controller.py`

**Interfaces:**
- Consumes: `InterviewManager`, `QuestionPlanner`, `AnswerEvaluator`, `ReportGenerator`.
- Produces: `InterviewController.start()`, `.stop()`, `.notify_platform_idle()`, `.handle_answer(answer_request_id: str, text: str)`, `.get_status()`.
- Produces: metadata helper with keys `interviewId`, `sectionId`, `questionId`, `exchangeId`, `promptId`, `promptType`.

- [ ] **Step 1: Write failing controller tests**

Add `tests/interview/test_controller.py`:

```python
from unittest.mock import AsyncMock

import pytest

from interview.answer_evaluator import AnswerEvaluator
from interview.controller import InterviewController
from interview.interview_manager import InterviewManager
from interview.question_planner import QuestionPlanner
from interview.report_generator import ReportGenerator
from interview.models import Evaluation, InterviewState


class StaticEvaluator:
    def __init__(self, evaluations):
        self.evaluations = list(evaluations)

    async def evaluate(self, question_text, answer_text):
        return self.evaluations.pop(0)


@pytest.fixture
def controller():
    manager = InterviewManager("config/interview.yaml")
    agent = AsyncMock()
    agent.send_prompt = AsyncMock()
    return InterviewController(
        agent=agent,
        manager=manager,
        planner=QuestionPlanner(manager.get_question_specs()),
        evaluator=StaticEvaluator([
            Evaluation(
                score=3,
                dimensions={"depth": 3},
                follow_up_needed=True,
                follow_up_question="请具体讲讲幂等设计。",
            ),
            Evaluation(score=4, dimensions={"depth": 4}, follow_up_needed=False),
        ]),
        report_generator=ReportGenerator(),
        interview_id="iv_test",
    )


@pytest.mark.asyncio
async def test_start_sends_opening_then_first_question_with_metadata(controller):
    await controller.start()

    prompts = controller._agent.send_prompt.await_args_list
    assert prompts[0].args[0].startswith("你好")
    assert prompts[1].args[0].startswith("请介绍")
    metadata = prompts[1].kwargs["metadata"]
    assert metadata["interviewId"] == "iv_test"
    assert metadata["questionId"] == "q_project_deep_dive_001"
    assert metadata["exchangeId"] == "ex_001"
    assert metadata["promptType"] == "main_question"
    assert controller.state == InterviewState.LISTENING


@pytest.mark.asyncio
async def test_answer_can_trigger_one_follow_up(controller):
    await controller.start()

    await controller.handle_answer("req_a1", "我做了订单系统")

    follow_up = controller._agent.send_prompt.await_args_list[-1]
    assert follow_up.args[0] == "请具体讲讲幂等设计。"
    assert follow_up.kwargs["metadata"]["promptType"] == "follow_up"
    assert controller.state == InterviewState.LISTENING


@pytest.mark.asyncio
async def test_second_answer_closes_question_and_moves_next(controller):
    await controller.start()
    await controller.handle_answer("req_a1", "我做了订单系统")
    await controller.handle_answer("req_a2", "用业务唯一键保证幂等")

    status = controller.get_status()
    assert status["currentQuestion"]["questionId"] == "q_python_backend_001"
    assert status["state"] == "listening"
    assert status["questionsCompleted"] == 1
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/interview/test_controller.py -q
```

Expected: FAIL with missing `interview.controller`.

- [ ] **Step 3: Implement controller minimally**

Implement `InterviewController` with:

- `_exchange_seq` and `_prompt_seq` counters.
- `_ask_question(question, prompt_type, parent_exchange_id=None, probe_index=0)`.
- Metadata passed to `agent.send_prompt(text, metadata=metadata)`.
- `handle_answer()` stores `answer_request_id`, `answer_text`, and evaluation.
- Follow-up only when `follow_up_needed` and `probe_index < max_probe_per_question`.
- Otherwise mark current question complete and ask next question.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
pytest tests/interview/test_controller.py -q
```

Expected: PASS.

---

### Task 4: Interview Listener And ASR Metadata Propagation

**Files:**
- Create: `interview/listener.py`
- Create: `tests/interview/test_listener.py`

**Interfaces:**
- Consumes: `InterviewController.current_answer_metadata() -> dict`.
- Produces: `InterviewListener._on_speech_started()`, `._on_speech_stopped()`, `._on_asr_interim(text)`, `._on_asr_transcript(text)`.
- Produces: Developer ASR SDK calls with `metadata=...`.

- [ ] **Step 1: Write failing listener tests**

Add `tests/interview/test_listener.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from interview.listener import InterviewListener
from interview.models import InterviewState


@pytest.mark.asyncio
async def test_speech_events_send_metadata_only_while_listening():
    controller = MagicMock()
    controller.state = InterviewState.LISTENING
    controller.current_answer_metadata.return_value = {
        "interviewId": "iv_test",
        "questionId": "q1",
        "exchangeId": "ex_001",
        "answerId": "ans_001",
    }
    controller.handle_answer = AsyncMock()

    agent = AsyncMock()
    listener = InterviewListener()
    listener.agent = agent
    listener.set_controller(controller)

    await listener._on_speech_started()
    await listener._on_asr_interim("hello")
    await listener._on_speech_stopped()
    await listener._on_asr_transcript("hello world")

    start_metadata = agent.send_voice_start.await_args.kwargs["metadata"]
    final_metadata = agent.send_asr_final.await_args.kwargs["metadata"]
    assert start_metadata["exchangeId"] == "ex_001"
    assert final_metadata["answerId"] == "ans_001"
    controller.handle_answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_ignores_speech_when_not_listening():
    controller = MagicMock()
    controller.state = InterviewState.ASKING
    agent = AsyncMock()
    listener = InterviewListener()
    listener.agent = agent
    listener.set_controller(controller)

    await listener._on_speech_started()

    agent.send_voice_start.assert_not_called()
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/interview/test_listener.py -q
```

Expected: FAIL with missing listener.

- [ ] **Step 3: Implement listener**

Follow `teaching/listener.py` but keep only interview behavior:

- Accept speech only in `InterviewState.LISTENING`.
- Generate one SDK `request_id` per answer.
- Send `send_voice_start`, `send_asr_partial`, `send_voice_finish`, `send_asr_final` with controller metadata.
- Call `controller.handle_answer(request_id, text)` after final transcript.
- Ignore 1-character transcripts.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
pytest tests/interview/test_listener.py -q
```

Expected: PASS.

---

### Task 5: Agent Routes And Minimal Frontend

**Files:**
- Create: `interview/agent.py`
- Create: `frontend/interview.html`
- Create: `tests/interview/test_agent_routes.py`
- Create: `tests/interview/test_frontend.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: controller/listener/manager from earlier tasks.
- Produces HTTP routes:
  - `GET /`
  - `POST /api/start-session`
  - `POST /api/stop-session`
  - `POST /api/interview/start`
  - `POST /api/interview/stop`
  - `GET /api/interview/status`
  - `GET /api/session-info`

- [ ] **Step 1: Write failing route and frontend tests**

Add `tests/interview/test_agent_routes.py`:

```python
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from interview import agent as interview_agent


async def test_status_returns_idle_without_session(monkeypatch):
    app = await interview_agent.create_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.get("/api/interview/status")
        data = await resp.json()
        assert resp.status == 200
        assert data["state"] == "idle"
    finally:
        await client.close()
```

Add `tests/interview/test_frontend.py`:

```python
from pathlib import Path


def test_interview_frontend_uses_interview_routes():
    html = Path("frontend/interview.html").read_text(encoding="utf-8")

    assert "/api/start-session" in html
    assert "/api/interview/status" in html
    assert "/api/interview/start" in html
    assert "currentQuestion" in html
    assert "finalReport" in html
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/interview/test_agent_routes.py tests/interview/test_frontend.py -q
```

Expected: FAIL with missing files/routes.

- [ ] **Step 3: Implement route skeleton and frontend**

Follow `talkshow/agent.py` structure:

- `INTERVIEW_HTTP_PORT=8083`
- `INTERVIEW_CONFIG_PATH=config/interview.yaml`
- `create_app()` for tests and `web.run_app(create_app())` for script mode.
- `start_interview_session()` starts LiveAvatar with `developer_asr=bool(DASHSCOPE_API_KEY)`.
- Wire `QwenAsrManager` from `teaching.asr_manager` into `InterviewListener` when Developer ASR is enabled.
- Serve `frontend/interview.html`.

Frontend must be minimal:

- Connect/disconnect buttons.
- Start/stop interview buttons.
- Avatar container matching existing SDK page conventions.
- Poll `/api/interview/status`.
- Render state, current question, latest transcript, progress, final report.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
pytest tests/interview/test_agent_routes.py tests/interview/test_frontend.py -q
```

Expected: PASS.

---

### Task 6: Full Verification And Documentation

**Files:**
- Modify: `README.md`
- Create: `interview/QUICKSTART.md`

**Interfaces:**
- Consumes all prior tasks.
- Produces operator documentation.

- [ ] **Step 1: Add docs tests or distribution safety coverage if needed**

If `tests/test_distribution_safety.py` checks package folders, update it to include `interview`.

- [ ] **Step 2: Run focused interview tests**

Run:

```bash
pytest tests/interview -q
```

Expected: PASS.

- [ ] **Step 3: Run relevant existing mode tests**

Run:

```bash
pytest tests/talkshow tests/teaching/test_speech_gating.py tests/test_llm_client.py -q
```

Expected: PASS.

- [ ] **Step 4: Run all tests if dependency environment allows**

Run:

```bash
pytest -q
```

Expected: PASS or document unrelated pre-existing failures with exact failing tests.

- [ ] **Step 5: Manual smoke command**

Run:

```bash
INTERVIEW_HTTP_PORT=8083 python interview/agent.py
```

Expected: server starts on `http://localhost:8083`. Stop it after confirming startup logs.

- [ ] **Step 6: Verify repository state**

Run:

```bash
git status --short
```

Expected in this workspace: `fatal: not a git repository` or changed files listed if the repo is later initialized.

---

## Self-Review

**Spec coverage:** The tasks cover a new `interview` mode, explicit states, YAML config, one-question-at-a-time loop, SDK metadata on prompt and ASR events, `question_id`/`exchange_id` separation, bounded follow-ups, final report, route surface, and minimal frontend.

**Known implementation boundary:** This plan uses deterministic planner/evaluator/report behavior for phase one tests. LLM calls are isolated behind `AnswerEvaluator` so richer prompts can be added later without changing controller contracts.

**Placeholder scan:** No vague placeholder language remains. Each task names files, interfaces, commands, and expected results.

**Type consistency:** `InterviewState`, `QuestionSpec`, `Exchange`, `Evaluation`, and `InterviewReport` names are used consistently across tasks.
