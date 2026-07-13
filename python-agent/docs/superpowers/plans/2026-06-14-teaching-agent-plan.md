# Teaching Digital Human — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a teaching digital human agent for children aged 4-10, with chapter-based lectures, interruptible Q&A, quizzes, and interactive components.

**Architecture:** Four new Python modules with zero modifications to existing code. `course_component.py` (pure dataclass) → `course_manager.py` (YAML loading) → `teaching_controller.py` (state machine) → `teaching_agent.py` (HTTP server + ASR wiring). All reuse `llm_client.py` and Live Avatar SDK.

**Tech Stack:** Python 3.11+, aiohttp, PyYAML, DashScope Qwen ASR, DeepSeek LLM, Live Avatar SDK

---

### Task 1: `course_component.py` — Component message dataclass

**Files:**
- Create: `python-agent/course_component.py`
- Create: `python-agent/tests/test_course_component.py`

- [ ] **Step 1: Write test file**

```python
"""Tests for course_component.py — component message dataclass."""

import json
import time
import pytest
from course_component import ComponentMessage, COMPONENT_TYPES


class TestComponentMessage:
    def test_create_visual_card_message(self):
        msg = ComponentMessage(
            type="visual_card",
            action="show",
            data={"id": "what_is_logic", "title": "火眼金睛", "content": "学会判断对错"},
            timestamp=1718352000000,
        )
        assert msg.type == "visual_card"
        assert msg.action == "show"
        assert msg.data["id"] == "what_is_logic"
        assert msg.timestamp == 1718352000000

    def test_create_quiz_message(self):
        msg = ComponentMessage(
            type="quiz",
            action="show",
            data={
                "question": "小明掉进了哪个小陷阱？",
                "options": [
                    {"key": "A", "text": "「大家都这样」陷阱 🐑"},
                    {"key": "B", "text": "「大人说的都对」陷阱 👨‍🏫"},
                ],
                "chapter_id": "fallacy_types",
            },
            timestamp=1718352000000,
        )
        assert msg.type == "quiz"
        assert len(msg.data["options"]) == 2

    def test_create_encouragement_message(self):
        msg = ComponentMessage(
            type="encouragement",
            action="show",
            data={"text": "太棒了！🌟", "style": "star"},
            timestamp=1718352000000,
        )
        assert msg.type == "encouragement"
        assert msg.data["style"] == "star"

    def test_create_raise_hand_message(self):
        msg = ComponentMessage(
            type="raise_hand",
            action="update",
            data={"enabled": False},
            timestamp=1718352000000,
        )
        assert msg.type == "raise_hand"
        assert msg.data["enabled"] is False

    def test_all_component_types_defined(self):
        expected = {
            "visual_card", "mindmap", "quiz", "quiz_result",
            "raise_hand", "interaction_prompt", "chapter_indicator",
            "lecture_progress", "encouragement",
        }
        assert expected.issubset(set(COMPONENT_TYPES))

    def test_message_serializable_to_json(self):
        msg = ComponentMessage(
            type="quiz_result",
            action="show",
            data={"correct": True, "explanation": "答对了！"},
            timestamp=1718352000000,
        )
        d = {"type": msg.type, "action": msg.action, "data": msg.data, "timestamp": msg.timestamp}
        serialized = json.dumps(d, ensure_ascii=False)
        assert "quiz_result" in serialized
        assert "答对了" in serialized

    def test_mindmap_component(self):
        msg = ComponentMessage(
            type="mindmap",
            action="show",
            data={"id": "fallacy_types", "title": "逻辑小陷阱", "image_url": "/assets/mindmap.png"},
            timestamp=1718352000000,
        )
        assert msg.data["image_url"] == "/assets/mindmap.png"

    def test_chapter_indicator_component(self):
        msg = ComponentMessage(
            type="chapter_indicator",
            action="show",
            data={"current": 1, "total": 3, "title": "小故事"},
            timestamp=1718352000000,
        )
        assert msg.data["current"] == 1
        assert msg.data["total"] == 3

    def test_lecture_progress_component(self):
        msg = ComponentMessage(
            type="lecture_progress",
            action="update",
            data={"segment_current": 2, "segment_total": 5},
            timestamp=1718352000000,
        )
        assert msg.data["segment_current"] == 2

    def test_interaction_prompt_component(self):
        msg = ComponentMessage(
            type="interaction_prompt",
            action="show",
            data={"text": "你会跟着一起跑吗？", "chapter_id": "intro"},
            timestamp=1718352000000,
        )
        assert msg.data["chapter_id"] == "intro"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python-agent && python -m pytest tests/test_course_component.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'course_component'`

- [ ] **Step 3: Write `course_component.py`**

```python
"""Component message protocol for teaching agent ↔ frontend communication.

Agent pushes component messages via Live Avatar's send_custom_event.
Frontend renders components based on type and action fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# All valid component types
COMPONENT_TYPES = [
    "visual_card",
    "mindmap",
    "quiz",
    "quiz_result",
    "raise_hand",
    "interaction_prompt",
    "chapter_indicator",
    "lecture_progress",
    "encouragement",
]


@dataclass
class ComponentMessage:
    """A message pushed from agent to frontend to control teaching UI components.

    Attributes:
        type: Component type — one of COMPONENT_TYPES.
        action: Lifecycle action — "show", "hide", or "update".
        data: Component-specific payload (see per-type schema).
        timestamp: Unix timestamp in milliseconds.
    """

    type: str
    action: str
    data: dict = field(default_factory=dict)
    timestamp: int = 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python-agent && python -m pytest tests/test_course_component.py -v`
Expected: 10 tests PASS

- [ ] **Step 5: Commit**

```bash
cd python-agent && git add course_component.py tests/test_course_component.py
git commit -m "feat: add course_component.py — component message dataclass for teaching frontend
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `course_manager.py` — Course YAML loading and validation

**Files:**
- Create: `python-agent/course_manager.py`
- Create: `python-agent/tests/test_course_manager.py`

- [ ] **Step 1: Write test file**

```python
"""Tests for course_manager.py — YAML course loading and validation."""

import tempfile
from pathlib import Path
import pytest
from course_manager import CourseManager, CourseLoadError, ValidationError


VALID_COURSE_YAML = """
course:
  title: "思维小达人"
  lang: zh
  default_tts_speed: 0.9
  assets:
    cards:
      - id: "card1"
        title: "卡片1"
        content: "测试内容"
        image: "/assets/test.png"
chapters:
  - id: "intro"
    title: "引入章节"
    skeleton:
      - "第一句话"
      - "第二句话"
    interaction:
      prompt: "你觉得呢？"
      expect_keywords: ["觉得", "好"]
    visual:
      type: card
      ref: "card1"
  - id: "quiz_chapter"
    title: "测验章节"
    skeleton:
      - "讲解内容"
    quiz:
      question: "这是什么？"
      options:
        - { key: "A", text: "选项A", correct: true }
        - { key: "B", text: "选项B", correct: false }
      explanation_correct: "答对了！"
      explanation_wrong: "再想想"
"""


@pytest.fixture
def course_yaml_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(VALID_COURSE_YAML)
        path = Path(f.name)
    yield path
    path.unlink(missing_ok=True)


class TestCourseManagerLoad:
    def test_load_valid_course(self, course_yaml_file):
        cm = CourseManager(course_yaml_file)
        course = cm.get_course()
        assert course["title"] == "思维小达人"
        assert course["lang"] == "zh"
        assert course["default_tts_speed"] == 0.9

    def test_load_nonexistent_file(self):
        with pytest.raises(CourseLoadError, match="not found"):
            CourseManager(Path("/nonexistent/course.yaml"))

    def test_load_missing_title(self):
        yaml = "course:\n  lang: zh\nchapters: []"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml)
            p = Path(f.name)
        try:
            with pytest.raises(ValidationError, match="title"):
                CourseManager(p)
        finally:
            p.unlink(missing_ok=True)

    def test_load_missing_lang(self):
        yaml = "course:\n  title: Test\nchapters: []"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml)
            p = Path(f.name)
        try:
            with pytest.raises(ValidationError, match="lang"):
                CourseManager(p)
        finally:
            p.unlink(missing_ok=True)


class TestChapterAccess:
    def test_get_chapter_by_id(self, course_yaml_file):
        cm = CourseManager(course_yaml_file)
        ch = cm.get_chapter("intro")
        assert ch["title"] == "引入章节"
        assert len(ch["skeleton"]) == 2

    def test_get_chapter_not_found(self, course_yaml_file):
        cm = CourseManager(course_yaml_file)
        with pytest.raises(ValueError, match="not found"):
            cm.get_chapter("nonexistent")

    def test_get_chapters_count(self, course_yaml_file):
        cm = CourseManager(course_yaml_file)
        assert cm.get_chapter_count() == 2

    def test_get_first_chapter(self, course_yaml_file):
        cm = CourseManager(course_yaml_file)
        ch = cm.get_first_chapter()
        assert ch["id"] == "intro"

    def test_get_next_chapter(self, course_yaml_file):
        cm = CourseManager(course_yaml_file)
        nxt = cm.get_next_chapter("intro")
        assert nxt["id"] == "quiz_chapter"

    def test_get_next_chapter_last_returns_none(self, course_yaml_file):
        cm = CourseManager(course_yaml_file)
        nxt = cm.get_next_chapter("quiz_chapter")
        assert nxt is None


class TestValidation:
    def test_empty_skeleton_raises(self):
        yaml = """
course:
  title: Test
  lang: zh
chapters:
  - id: "bad"
    title: "Bad"
    skeleton: []
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml)
            p = Path(f.name)
        try:
            with pytest.raises(ValidationError, match="skeleton"):
                CourseManager(p)
        finally:
            p.unlink(missing_ok=True)

    def test_quiz_no_correct_option_raises(self):
        yaml = """
course:
  title: Test
  lang: zh
chapters:
  - id: "bad_quiz"
    title: "Bad Quiz"
    skeleton:
      - "something"
    quiz:
      question: "Q?"
      options:
        - { key: "A", text: "a", correct: false }
        - { key: "B", text: "b", correct: false }
      explanation_correct: "ok"
      explanation_wrong: "no"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml)
            p = Path(f.name)
        try:
            with pytest.raises(ValidationError, match="correct"):
                CourseManager(p)
        finally:
            p.unlink(missing_ok=True)

    def test_quiz_multiple_correct_options_raises(self):
        yaml = """
course:
  title: Test
  lang: zh
chapters:
  - id: "bad_quiz"
    title: "Bad Quiz"
    skeleton:
      - "something"
    quiz:
      question: "Q?"
      options:
        - { key: "A", text: "a", correct: true }
        - { key: "B", text: "b", correct: true }
      explanation_correct: "ok"
      explanation_wrong: "no"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml)
            p = Path(f.name)
        try:
            with pytest.raises(ValidationError, match="exactly one"):
                CourseManager(p)
        finally:
            p.unlink(missing_ok=True)


class TestAssets:
    def test_assets_cards_loaded(self, course_yaml_file):
        cm = CourseManager(course_yaml_file)
        cards = cm.get_cards()
        assert len(cards) == 1
        assert cards[0]["id"] == "card1"

    def test_no_assets_is_ok(self):
        yaml = """
course:
  title: Test
  lang: zh
chapters:
  - id: "ch1"
    title: "Chapter 1"
    skeleton:
      - "hello"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml)
            p = Path(f.name)
        try:
            cm = CourseManager(p)
            assert cm.get_cards() == []
        finally:
            p.unlink(missing_ok=True)

    def test_chapter_without_interaction_or_quiz_is_valid(self):
        yaml = """
course:
  title: Test
  lang: zh
chapters:
  - id: "plain"
    title: "Plain"
    skeleton:
      - "just text"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml)
            p = Path(f.name)
        try:
            cm = CourseManager(p)
            ch = cm.get_chapter("plain")
            assert "interaction" not in ch or ch.get("interaction") is None
            assert "quiz" not in ch or ch.get("quiz") is None
        finally:
            p.unlink(missing_ok=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python-agent && python -m pytest tests/test_course_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'course_manager'`

- [ ] **Step 3: Write `course_manager.py`**

```python
"""Course Manager — loads and validates teaching course YAML config.

Usage:
    cm = CourseManager(Path("config/courses/thinking.yaml"))
    course = cm.get_course()          # {title, lang, default_tts_speed, ...}
    ch = cm.get_chapter("intro")      # single chapter dict
    next_ch = cm.get_next_chapter("intro")  # next chapter or None
    count = cm.get_chapter_count()
    cards = cm.get_cards()            # asset cards list
"""

from __future__ import annotations

from pathlib import Path

import yaml


class CourseLoadError(Exception):
    """Raised when course YAML file cannot be loaded."""


class ValidationError(Exception):
    """Raised when course YAML content fails validation."""


class CourseManager:
    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path
        self._raw: dict = {}
        self._chapters: list[dict] = []
        self._chapter_index: dict[str, int] = {}
        self._load()

    # -- public read API ---------------------------------------------------

    def get_course(self) -> dict:
        """Return course-level metadata dict."""
        return self._raw.get("course", {})

    def get_chapter(self, chapter_id: str) -> dict:
        """Return a single chapter by id. Raises ValueError if not found."""
        idx = self._chapter_index.get(chapter_id)
        if idx is None:
            raise ValueError(f"Chapter '{chapter_id}' not found in course")
        return self._chapters[idx]

    def get_first_chapter(self) -> dict:
        """Return the first chapter."""
        if not self._chapters:
            raise ValueError("Course has no chapters")
        return self._chapters[0]

    def get_next_chapter(self, current_chapter_id: str) -> dict | None:
        """Return the next chapter after the given id, or None if last."""
        idx = self._chapter_index.get(current_chapter_id)
        if idx is None:
            raise ValueError(f"Chapter '{current_chapter_id}' not found")
        next_idx = idx + 1
        if next_idx >= len(self._chapters):
            return None
        return self._chapters[next_idx]

    def get_chapter_count(self) -> int:
        return len(self._chapters)

    def get_chapter_by_index(self, index: int) -> dict | None:
        """Return chapter at the given index, or None if out of range."""
        if 0 <= index < len(self._chapters):
            return self._chapters[index]
        return None

    def get_cards(self) -> list[dict]:
        """Return asset cards list, or empty list if none."""
        assets = self._raw.get("course", {}).get("assets", {}) or {}
        return assets.get("cards", [])

    def get_mindmaps(self) -> dict:
        """Return asset mindmaps dict, or empty dict if none."""
        assets = self._raw.get("course", {}).get("assets", {}) or {}
        return assets.get("mindmaps", {})

    # -- internal ----------------------------------------------------------

    def _load(self) -> None:
        if not self._config_path.exists():
            raise CourseLoadError(f"Course config not found: {self._config_path}")

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                self._raw = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise CourseLoadError(f"Invalid YAML in {self._config_path}: {e}")

        self._validate()
        self._chapters = self._raw.get("chapters", [])
        self._build_index()

    def _build_index(self) -> None:
        self._chapter_index = {}
        for i, ch in enumerate(self._chapters):
            cid = ch.get("id")
            if not cid:
                raise ValidationError(f"Chapter at index {i} is missing 'id'")
            if cid in self._chapter_index:
                raise ValidationError(f"Duplicate chapter id: {cid}")
            self._chapter_index[cid] = i

    def _validate(self) -> None:
        course = self._raw.get("course")
        if not course:
            raise ValidationError("Missing top-level 'course' key")

        if not course.get("title"):
            raise ValidationError("course.title is required")
        if not course.get("lang"):
            raise ValidationError("course.lang is required")

        chapters = self._raw.get("chapters", [])
        if not chapters:
            raise ValidationError("At least one chapter is required")

        seen_ids: set[str] = set()
        for i, ch in enumerate(chapters):
            cid = ch.get("id")
            if not cid:
                raise ValidationError(f"Chapter at index {i} is missing 'id'")
            if cid in seen_ids:
                raise ValidationError(f"Duplicate chapter id: {cid}")
            seen_ids.add(cid)

            if not ch.get("title"):
                raise ValidationError(f"Chapter '{cid}' is missing 'title'")

            skeleton = ch.get("skeleton", [])
            if not skeleton:
                raise ValidationError(
                    f"Chapter '{cid}' has empty skeleton — at least one point is required"
                )

            # Validate quiz if present
            quiz = ch.get("quiz")
            if quiz is not None:
                self._validate_quiz(quiz, cid)

    @staticmethod
    def _validate_quiz(quiz: dict, chapter_id: str) -> None:
        if not quiz.get("question"):
            raise ValidationError(f"Quiz in chapter '{chapter_id}' missing 'question'")
        options = quiz.get("options", [])
        if len(options) < 2:
            raise ValidationError(
                f"Quiz in chapter '{chapter_id}' needs at least 2 options"
            )
        correct_count = sum(1 for o in options if o.get("correct"))
        if correct_count != 1:
            raise ValidationError(
                f"Quiz in chapter '{chapter_id}' must have exactly one correct option, "
                f"found {correct_count}"
            )
        if not quiz.get("explanation_correct"):
            raise ValidationError(
                f"Quiz in chapter '{chapter_id}' missing 'explanation_correct'"
            )
        if not quiz.get("explanation_wrong"):
            raise ValidationError(
                f"Quiz in chapter '{chapter_id}' missing 'explanation_wrong'"
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python-agent && python -m pytest tests/test_course_manager.py -v`
Expected: 14 tests PASS

- [ ] **Step 5: Commit**

```bash
cd python-agent && git add course_manager.py tests/test_course_manager.py
git commit -m "feat: add course_manager.py — YAML course loading and validation
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `teaching_controller.py` — State machine and transitions

**Files:**
- Create: `python-agent/teaching_controller.py`
- Create: `python-agent/tests/test_teaching_controller.py`

- [ ] **Step 1: Write tests for state transitions**

```python
"""Tests for teaching_controller.py — state machine and transitions."""

import pytest
from teaching_controller import TeachingController, TeachingState


# ---- Fake dependencies for unit testing ----

class FakeAgent:
    """Minimal fake of AvatarAgent for state machine testing."""
    def __init__(self):
        self.prompts: list[str] = []
        self.custom_events: list[dict] = []
        self.cancelled_response_ids: list[str] = []
        self._interrupt_sent = False

    async def send_prompt(self, text: str) -> None:
        self.prompts.append(text)

    async def send_custom_event(self, request_id, event, data) -> None:
        self.custom_events.append({"event": event, "data": data})

    async def send_interrupt(self) -> None:
        self._interrupt_sent = True

    async def send_response_cancel(self, response_id: str) -> None:
        self.cancelled_response_ids.append(response_id)


class FakeCourseManager:
    """Fake CourseManager returning a minimal course."""
    def __init__(self):
        self._chapters = [
            {
                "id": "ch1",
                "title": "第一章",
                "skeleton": ["要点1", "要点2"],
                "visual": {"type": "card", "ref": "card1"},
                "interaction": {
                    "prompt": "你觉得呢？",
                    "expect_keywords": ["觉得"],
                },
            },
            {
                "id": "ch2",
                "title": "第二章",
                "skeleton": ["测验前讲解"],
                "quiz": {
                    "question": "选哪个？",
                    "options": [
                        {"key": "A", "text": "对", "correct": True},
                        {"key": "B", "text": "错", "correct": False},
                    ],
                    "explanation_correct": "对了！",
                    "explanation_wrong": "不对哦",
                },
            },
            {
                "id": "ch3",
                "title": "第三章（纯讲解）",
                "skeleton": ["最后的内容"],
                # no interaction, no quiz — plain chapter
            },
        ]

    def get_course(self):
        return {"title": "测试课", "lang": "zh", "default_tts_speed": 0.9}

    def get_chapter(self, cid):
        for ch in self._chapters:
            if ch["id"] == cid:
                return ch
        raise ValueError(f"Chapter '{cid}' not found")

    def get_first_chapter(self):
        return self._chapters[0]

    def get_next_chapter(self, cid):
        for i, ch in enumerate(self._chapters):
            if ch["id"] == cid:
                return self._chapters[i + 1] if i + 1 < len(self._chapters) else None
        raise ValueError(f"Chapter '{cid}' not found")

    def get_chapter_count(self):
        return len(self._chapters)

    def get_cards(self):
        return [{"id": "card1", "title": "卡片", "content": "内容"}]


class FakeLlmClient:
    """Fake LLM client returning canned responses."""
    def __init__(self):
        self.generate_calls: list[str] = []
        self._generate_response = "润色后的文本"

    def set_response(self, text: str):
        self._generate_response = text

    async def generate(self, user_text: str, max_tokens: int = 512) -> str:
        self.generate_calls.append(user_text)
        return self._generate_response

    def reset_context(self):
        pass


@pytest.fixture
def controller():
    agent = FakeAgent()
    cm = FakeCourseManager()
    llm = FakeLlmClient()
    ctrl = TeachingController(agent=agent, course_manager=cm, llm_client=llm)
    return ctrl, agent, cm, llm


class TestStateTransitions:
    def test_initial_state_is_idle(self, controller):
        ctrl, _, _, _ = controller
        assert ctrl.state == TeachingState.IDLE

    def test_start_transitions_to_lecturing(self, controller):
        ctrl, _, _, _ = controller
        ctrl.start()
        assert ctrl.state == TeachingState.LECTURING

    def test_stop_from_lecturing_returns_to_idle(self, controller):
        ctrl, _, _, _ = controller
        ctrl.start()
        ctrl.stop()
        assert ctrl.state == TeachingState.IDLE

    def test_pause_from_idle_noop(self, controller):
        ctrl, _, _, _ = controller
        ctrl.pause()
        assert ctrl.state == TeachingState.IDLE

    def test_raise_hand_from_lecturing_enters_answering(self, controller):
        ctrl, _, _, _ = controller
        ctrl.start()
        ctrl.raise_hand()
        assert ctrl.state == TeachingState.ANSWERING

    def test_raise_hand_records_breakpoint(self, controller):
        ctrl, _, _, _ = controller
        ctrl.start()
        # The controller internally tracks position; simulate by setting it
        ctrl._current_chapter_id = "ch1"
        ctrl._current_skeleton_index = 1
        ctrl.raise_hand()
        assert ctrl._breakpoint == {"chapter_id": "ch1", "skeleton_index": 1}

    def test_raise_hand_from_quizzing_noop(self, controller):
        ctrl, _, _, _ = controller
        ctrl.start()
        ctrl._state = TeachingState.QUIZZING
        ctrl.raise_hand()
        assert ctrl.state == TeachingState.QUIZZING  # unchanged

    def test_cancel_hand_from_answering_returns_to_lecturing(self, controller):
        ctrl, _, _, _ = controller
        ctrl.start()
        ctrl.raise_hand()
        ctrl.cancel_hand()
        assert ctrl.state == TeachingState.LECTURING

    def test_answer_quiz_from_quizzing_enters_quiz_result(self, controller):
        ctrl, _, _, _ = controller
        ctrl.start()
        ctrl._state = TeachingState.QUIZZING
        ctrl._current_chapter_id = "ch2"
        ctrl.answer_quiz("ch2", "A")
        assert ctrl.state == TeachingState.QUIZ_RESULT

    def test_answer_quiz_wrong_chapter_raises(self, controller):
        ctrl, _, _, _ = controller
        ctrl.start()
        ctrl._state = TeachingState.QUIZZING
        ctrl._current_chapter_id = "ch2"
        with pytest.raises(ValueError, match="not the current quiz"):
            ctrl.answer_quiz("ch1", "A")


class TestGetStatus:
    def test_get_status_returns_state_and_chapter(self, controller):
        ctrl, _, _, _ = controller
        ctrl.start()
        ctrl._current_chapter_id = "ch1"
        ctrl._current_skeleton_index = 0
        status = ctrl.get_status()
        assert status["state"] == "lecturing"
        assert status["currentChapter"] == {"id": "ch1"}
        assert status["currentChapterIndex"] == 0
        assert status["totalChapters"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python-agent && python -m pytest tests/test_teaching_controller.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `teaching_controller.py` shell with state machine**

```python
"""Teaching Controller — state machine for lecture, Q&A, and quiz flows.

State machine:
    IDLE → LECTURING ⇄ ANSWERING → TRANSITIONING → LECTURING
    LECTURING → WAITING_INTERACT → PROCESSING_INTER → LECTURING
    LECTURING → QUIZZING → QUIZ_RESULT → LECTURING (next chapter)
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum

from course_component import ComponentMessage

logger = logging.getLogger(__name__)


class TeachingState(str, Enum):
    IDLE = "idle"
    LECTURING = "lecturing"
    WAITING_INTERACT = "waiting_interact"
    PROCESSING_INTER = "processing_interact"
    ANSWERING = "answering"
    TRANSITIONING = "transitioning"
    QUIZZING = "quizzing"
    QUIZ_RESULT = "quiz_result"


# Child-friendly system prompts
LECTURE_SYSTEM_PROMPT = """\
你是一位面向 4-10 岁小朋友的思维课老师，名字叫"小思老师"。
说话要像幼儿园/小学老师一样亲切可爱，用小朋友能听懂的语言。

规则：
- 每句话不超过 15 个字，用短句
- 用小朋友熟悉的事物打比方（玩具、小动物、吃东西、玩游戏）
- 称呼学生为"小朋友"或"你"
- 每次讲解完一个要点，加一句鼓励的话
- 语气温暖、活泼，像在讲故事
- 避免抽象概念，每个概念都要配一个具体的例子
- 每次根据给定的要点，扩展成 3-5 句自然的讲课语言
"""

QA_SYSTEM_PROMPT = """\
你是一位面向 4-10 岁小朋友的思维课老师"小思老师"。
你正在讲解{chapter_title}，一个小朋友举手向你提问。

规则：
- 先感谢小朋友的提问，肯定他/她（"这个问题问得真好！"）
- 用小朋友能听懂的方式回答，控制在 100 字以内
- 句子要短，用生活化的例子
- 回答完后，自然地带小朋友回到课程（"好啦，我们继续来学习..."）
- 如果小朋友的提问不清楚，温柔地请他/她再说一遍
"""

TRANSITION_PROMPT = """\
你正在给小朋友讲课，刚才小朋友提了一个问题，你已经回答完了。
现在需要自然地带小朋友回到课程内容。

刚才讲到的内容是：{context}
请用 1-2 句亲切的话，把小朋友的注意力拉回课程。
"""

INTERACTION_FEEDBACK_PROMPT = """\
你正在给小朋友讲课，你刚才问了小朋友一个问题：{question}
小朋友的回答是：{response}

请用 2-3 句亲切的话给小朋友反馈。先肯定他/她的回答，然后自然地继续讲课。
"""


class TeachingController:
    """Manages the teaching flow: lecture, interrupt, quiz.

    Lifecycle:
        start() → lecture loop → stop()
        raise_hand() → interrupt → answer → transition → resume
    """

    def __init__(self, *, agent, course_manager, llm_client, chunk_delay_ms: int = 200) -> None:
        self._agent = agent
        self._cm = course_manager
        self._llm = llm_client
        self._chunk_delay = chunk_delay_ms / 1000.0

        # State
        self._state = TeachingState.IDLE
        self._task: asyncio.Task | None = None
        self._stopped = False

        # TTS idle tracking (same pattern as broadcast_controller)
        self._tts_idle = asyncio.Event()
        self._tts_idle.set()
        self._idle_count = 0
        self._timeout_count = 0

        # Current position
        self._current_chapter_id: str | None = None
        self._current_skeleton_index: int = 0
        self._current_response_id: str | None = None

        # Breakpoint for interrupt-resume
        self._breakpoint: dict | None = None

        # Quiz: set by quiz flow, read by answer_quiz()
        self._quiz_answer: asyncio.Event | None = None
        self._quiz_chosen: str | None = None

        # Raise-hand: set by raise_hand(), waited by lecture loop
        self._hand_raised = asyncio.Event()
        self._hand_cancelled = False
        self._asr_text_queue: asyncio.Queue | None = None

    # -- public API ----------------------------------------------------------

    @property
    def state(self) -> TeachingState:
        return self._state

    def start(self) -> None:
        """Start the teaching session. Transitions IDLE → LECTURING."""
        if self._state != TeachingState.IDLE:
            return
        self._stopped = False
        self._state = TeachingState.LECTURING
        self._task = asyncio.create_task(self._run_lecture_loop())
        logger.info("📚 Teaching started")

    def stop(self) -> None:
        """Stop teaching gracefully. Transitions any state → IDLE."""
        self._stopped = True
        self._hand_raised.set()  # unblock any wait
        if self._quiz_answer:
            self._quiz_answer.set()  # unblock quiz wait
        if self._task and not self._task.done():
            self._task.cancel()
        self._state = TeachingState.IDLE
        self._task = None
        logger.info("📚 Teaching stopped")

    def pause(self) -> None:
        """Pause the lecture. No-op if not LECTURING."""
        if self._state == TeachingState.LECTURING:
            self._state = TeachingState.IDLE  # simplified: stop loop
            logger.info("⏸️  Teaching paused")

    def resume(self) -> None:
        """Resume from pause."""
        if self._state == TeachingState.IDLE and self._current_chapter_id:
            self._state = TeachingState.LECTURING
            self._task = asyncio.create_task(self._resume_lecture())
            logger.info("▶️  Teaching resumed")

    def raise_hand(self) -> None:
        """User clicked raise-hand button. Only valid in LECTURING."""
        if self._state == TeachingState.LECTURING:
            self._breakpoint = {
                "chapter_id": self._current_chapter_id,
                "skeleton_index": self._current_skeleton_index,
            }
            self._state = TeachingState.ANSWERING
            self._hand_raised.set()
            logger.info("🙋 Hand raised — entering ANSWERING")

    def cancel_hand(self) -> None:
        """User cancelled raise-hand."""
        if self._state == TeachingState.ANSWERING:
            self._hand_cancelled = True
            self._hand_raised.set()
            self._state = TeachingState.LECTURING
            logger.info("🙋 Hand cancelled — back to LECTURING")

    def answer_quiz(self, chapter_id: str, answer: str) -> None:
        """Submit quiz answer. Only valid in QUIZZING state."""
        if self._state != TeachingState.QUIZZING:
            raise ValueError("Not in QUIZZING state")
        if chapter_id != self._current_chapter_id:
            raise ValueError(
                f"Quiz answer for '{chapter_id}' but current quiz is '{self._current_chapter_id}'"
            )
        self._quiz_chosen = answer
        if self._quiz_answer:
            self._quiz_answer.set()

    def notify_platform_idle(self) -> None:
        """Called by listener when platform reports session.state=IDLE."""
        self._idle_count += 1
        self._tts_idle.set()

    def get_status(self) -> dict:
        chapters_count = self._cm.get_chapter_count()
        current_idx = 0
        if self._current_chapter_id:
            try:
                # Walk chapters to find the index of current chapter
                for i in range(chapters_count):
                    ch = self._cm.get_chapter_by_index(i)
                    if ch and ch["id"] == self._current_chapter_id:
                        current_idx = i
                        break
            except (ValueError, IndexError):
                pass
        return {
            "state": self._state.value,
            "currentChapter": (
                {"id": self._current_chapter_id}
                if self._current_chapter_id
                else None
            ),
            "currentChapterIndex": current_idx,
            "currentSkeletonIndex": self._current_skeleton_index,
            "totalChapters": chapters_count,
        }

    # -- lecture loop (stub — filled in Task 4) -----------------------------

    async def _run_lecture_loop(self) -> None:
        """Main lecture loop. Placeholder for Task 4."""
        pass

    async def _resume_lecture(self) -> None:
        """Resume lecture from breakpoint. Placeholder for Task 4."""
        pass
```

- [ ] **Step 4: Run state transition tests**

Run: `cd python-agent && python -m pytest tests/test_teaching_controller.py -v -k "State"`  
Expected: 8 tests PASS (state transition tests only)

- [ ] **Step 5: Commit**

```bash
cd python-agent && git add teaching_controller.py tests/test_teaching_controller.py
git commit -m "feat: add teaching_controller.py state machine shell with unit tests
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `teaching_controller.py` — Lecture loop implementation

**Files:**
- Modify: `python-agent/teaching_controller.py`
- Modify: `python-agent/tests/test_teaching_controller.py`

- [ ] **Step 1: Add mock lecture loop tests**

Append to `tests/test_teaching_controller.py`:

```python
class TestLectureLoop:
    @pytest.mark.asyncio
    async def test_lecture_sends_chapter_indicator(self, controller):
        ctrl, agent, cm, llm = controller
        ctrl._stopped = False
        ctrl._state = TeachingState.LECTURING

        await ctrl._broadcast_chapter(cm.get_chapter("ch1"))

        events = [e["event"] for e in agent.custom_events]
        assert "chapter_indicator" in events

    @pytest.mark.asyncio
    async def test_lecture_sends_visual_card(self, controller):
        ctrl, agent, cm, llm = controller
        ctrl._stopped = False
        ctrl._state = TeachingState.LECTURING

        await ctrl._broadcast_chapter(cm.get_chapter("ch1"))

        visual_events = [e for e in agent.custom_events if e["event"] == "visual_card"]
        assert len(visual_events) == 1
        assert visual_events[0]["data"]["id"] == "card1"

    @pytest.mark.asyncio
    async def test_lecture_polishes_skeleton_via_llm(self, controller):
        ctrl, agent, cm, llm = controller
        ctrl._stopped = False
        ctrl._state = TeachingState.LECTURING
        llm.set_response("小朋友们好，今天我们来学一个有趣的知识！")

        await ctrl._broadcast_chapter(cm.get_chapter("ch1"))

        # LLM should be called for each skeleton point
        assert len(llm.generate_calls) == 2  # ch1 has 2 skeleton points
        # Each polished text is sent as prompt
        assert len(agent.prompts) == 2

    @pytest.mark.asyncio
    async def test_lecture_skeleton_fallback_on_llm_failure(self, controller):
        ctrl, agent, cm, llm = controller
        ctrl._stopped = False
        ctrl._state = TeachingState.LECTURING

        # Make LLM fail on first call
        call_count = [0]
        async def flaky_generate(user_text, max_tokens=512):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("LLM unavailable")
            return "润色文本"
        llm.generate = flaky_generate

        await ctrl._broadcast_chapter(cm.get_chapter("ch1"))

        # First skeleton point should use raw text as fallback
        raw_skeleton = "要点1"
        assert any(raw_skeleton in p for p in agent.prompts), \
            f"Expected raw skeleton '{raw_skeleton}' in prompts: {agent.prompts}"

    @pytest.mark.asyncio
    async def test_lecture_sends_lecture_progress(self, controller):
        ctrl, agent, cm, llm = controller
        ctrl._stopped = False
        ctrl._state = TeachingState.LECTURING

        await ctrl._broadcast_chapter(cm.get_chapter("ch1"))

        progress_events = [e for e in agent.custom_events if e["event"] == "lecture_progress"]
        assert len(progress_events) == 2  # one per skeleton point
        assert progress_events[0]["data"]["segment_current"] == 1
        assert progress_events[1]["data"]["segment_current"] == 2

    @pytest.mark.asyncio
    async def test_chapter_with_interaction_enters_waiting(self, controller):
        ctrl, agent, cm, llm = controller
        ctrl._stopped = False
        ctrl._state = TeachingState.LECTURING

        await ctrl._broadcast_chapter(cm.get_chapter("ch1"))

        # ch1 has interaction, should enter WAITING_INTERACT
        assert ctrl._state == TeachingState.WAITING_INTERACT

    @pytest.mark.asyncio
    async def test_chapter_with_quiz_enters_quizzing(self, controller):
        ctrl, agent, cm, llm = controller
        ctrl._stopped = False
        ctrl._state = TeachingState.LECTURING

        await ctrl._broadcast_chapter(cm.get_chapter("ch2"))

        # ch2 has quiz, should enter QUIZZING
        assert ctrl._state == TeachingState.QUIZZING

    @pytest.mark.asyncio
    async def test_plain_chapter_advances_to_next(self, controller):
        ctrl, agent, cm, llm = controller
        ctrl._stopped = False
        ctrl._state = TeachingState.LECTURING

        await ctrl._broadcast_chapter(cm.get_chapter("ch3"))

        # ch3 has no interaction/quiz, should stay LECTURING
        assert ctrl._state == TeachingState.LECTURING

    @pytest.mark.asyncio
    async def test_full_lecture_loop(self, controller):
        ctrl, agent, cm, llm = controller
        ctrl._stopped = False
        ctrl._state = TeachingState.LECTURING

        # Simulate running the full loop manually
        ch = cm.get_first_chapter()
        while ch is not None:
            ctrl._tts_idle.set()  # simulate TTS idle
            await ctrl._broadcast_chapter(ch)
            if ctrl._state in (TeachingState.WAITING_INTERACT,):
                # Simulate interaction handling
                pass
            if ctrl._state == TeachingState.QUIZZING:
                ctrl.answer_quiz(ch["id"], "A")
                ctrl._state = TeachingState.LECTURING  # simulate quiz_result → next
            nxt = cm.get_next_chapter(ch["id"])
            ch = nxt

        # All 3 chapters should have been processed
        assert len(agent.prompts) >= 4  # at least skeleton points
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `cd python-agent && python -m pytest tests/test_teaching_controller.py::TestLectureLoop -v`
Expected: FAIL — `AttributeError` on `_broadcast_chapter`

- [ ] **Step 3: Implement lecture loop in `teaching_controller.py`**

Replace the `_run_lecture_loop` and `_resume_lecture` stubs and add `_broadcast_chapter`:

```python
    # -- lecture loop ---------------------------------------------------------

    async def _run_lecture_loop(self) -> None:
        """Main lecture loop — iterate through chapters."""
        chapter = self._cm.get_first_chapter()
        while chapter is not None and not self._stopped:
            self._current_chapter_id = chapter["id"]
            self._current_skeleton_index = 0
            logger.info("📖 Chapter: %s", chapter["title"])
            await self._broadcast_chapter(chapter)

            if self._stopped:
                return

            # Handle post-chapter flows
            if self._state == TeachingState.WAITING_INTERACT:
                await self._handle_interaction(chapter)

            if self._state == TeachingState.QUIZZING:
                await self._handle_quiz(chapter)

            # Advance to next chapter
            chapter = self._cm.get_next_chapter(chapter["id"])

        if not self._stopped:
            logger.info("📚 Course complete")
            self._state = TeachingState.IDLE

    async def _resume_lecture(self) -> None:
        """Resume lecture from breakpoint after interrupt."""
        bp = self._breakpoint
        if not bp:
            return
        chapter = self._cm.get_chapter(bp["chapter_id"])
        self._current_chapter_id = chapter["id"]
        self._current_skeleton_index = bp["skeleton_index"]
        logger.info("📖 Resuming chapter %s from skeleton[%d]", chapter["title"], bp["skeleton_index"])
        await self._broadcast_chapter(chapter, start_from=bp["skeleton_index"])

        # Continue with remaining chapters
        chapter = self._cm.get_next_chapter(chapter["id"])
        while chapter is not None and not self._stopped:
            self._current_chapter_id = chapter["id"]
            self._current_skeleton_index = 0
            await self._broadcast_chapter(chapter)

            if self._stopped:
                return
            if self._state == TeachingState.WAITING_INTERACT:
                await self._handle_interaction(chapter)
            if self._state == TeachingState.QUIZZING:
                await self._handle_quiz(chapter)

            chapter = self._cm.get_next_chapter(chapter["id"])

        if not self._stopped:
            self._state = TeachingState.IDLE

    async def _broadcast_chapter(self, chapter: dict, start_from: int = 0) -> None:
        """Broadcast all skeleton points for one chapter."""
        # Send chapter indicator
        await self._send_component("chapter_indicator", "show", {
            "title": chapter["title"],
            "chapter_id": chapter["id"],
        })

        # Send visual aid if configured
        visual = chapter.get("visual")
        if visual:
            vtype = visual["type"]
            ref = visual["ref"]
            cards = self._cm.get_cards()
            card_data = None
            for c in cards:
                if c["id"] == ref:
                    card_data = c
                    break
            if card_data:
                await self._send_component(vtype, "show", {
                    "id": card_data.get("id", ""),
                    "title": card_data.get("title", ""),
                    "content": card_data.get("content", ""),
                    "image": card_data.get("image"),
                })

        skeleton = chapter["skeleton"]
        total = len(skeleton)

        for i in range(start_from, total):
            if self._stopped:
                return

            point = skeleton[i]
            self._current_skeleton_index = i

            # Check for raise-hand interrupt
            if self._hand_raised.is_set():
                self._hand_raised.clear()
                if self._state == TeachingState.ANSWERING:
                    # Interrupted — save breakpoint and exit
                    self._breakpoint = {
                        "chapter_id": chapter["id"],
                        "skeleton_index": i,
                    }
                    return
                elif self._hand_cancelled:
                    self._hand_cancelled = False
                    # Continue normally

            # Send progress
            await self._send_component("lecture_progress", "update", {
                "segment_current": i + 1,
                "segment_total": total,
            })

            # Polish skeleton point via LLM (with fallback)
            polished = await self._polish_skeleton(point)

            # Wait for platform TTS idle
            self._tts_idle.clear()
            try:
                await asyncio.wait_for(self._tts_idle.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                self._timeout_count += 1
                logger.warning("⏰ TTS idle timeout #%d — continuing", self._timeout_count)

            # Send the prompt
            await self._agent.send_prompt(polished)
            logger.info("📢 [%d/%d] %s", i + 1, total, polished[:60])

        # Post-chapter state transitions
        if chapter.get("interaction"):
            self._state = TeachingState.WAITING_INTERACT
            # Send interaction prompt
            await self._send_component("interaction_prompt", "show", {
                "text": chapter["interaction"]["prompt"],
                "chapter_id": chapter["id"],
            })
            await self._agent.send_prompt(chapter["interaction"]["prompt"])
        elif chapter.get("quiz"):
            self._state = TeachingState.QUIZZING
        # else: stay LECTURING for next chapter

    async def _polish_skeleton(self, point: str) -> str:
        """Polish a skeleton point via LLM. Fall back to raw text on failure."""
        try:
            self._llm.reset_context()
            # Set child-friendly persona
            self._llm._system_prompt = LECTURE_SYSTEM_PROMPT
            self._llm._messages = [{"role": "system", "content": LECTURE_SYSTEM_PROMPT}]
            prompt = f"请把下面这个讲课要点扩展成生动有趣的口语讲解：\n\n{point}"
            result = await self._llm.generate(prompt, max_tokens=256)
            if result and result.strip():
                return result.strip()
        except Exception as e:
            logger.warning("LLM polish failed for '%s': %s — using raw skeleton", point[:30], e)
        return point  # fallback: raw skeleton text

    async def _send_component(self, ctype: str, action: str, data: dict) -> None:
        """Push a component message to frontend via custom event."""
        msg = ComponentMessage(
            type=ctype,
            action=action,
            data=data,
            timestamp=int(time.time() * 1000),
        )
        await self._agent.send_custom_event(
            request_id=None,
            event=ctype,
            data={"action": action, "data": data, "timestamp": msg.timestamp},
        )

    async def _handle_interaction(self, chapter: dict) -> None:
        """Handle chapter-end interaction flow."""
        logger.info("💬 Waiting for interaction response...")
        # In full implementation, this waits for VAD+ASR after the interaction prompt.
        # For now, transition back to LECTURING.
        self._state = TeachingState.LECTURING

    async def _handle_quiz(self, chapter: dict) -> None:
        """Handle chapter-end quiz flow."""
        quiz = chapter["quiz"]
        logger.info("❓ Quiz: %s", quiz["question"])

        # Send quiz component
        await self._send_component("quiz", "show", {
            "question": quiz["question"],
            "options": quiz["options"],
            "chapter_id": chapter["id"],
        })
        # Disable raise-hand during quiz
        await self._send_component("raise_hand", "update", {"enabled": False})

        # Wait for user answer
        self._quiz_answer = asyncio.Event()
        self._quiz_chosen = None
        try:
            await asyncio.wait_for(self._quiz_answer.wait(), timeout=90.0)
        except asyncio.TimeoutError:
            logger.info("⏰ Quiz timeout — showing correct answer")
            correct = next(o["text"] for o in quiz["options"] if o["correct"])
            await self._agent.send_prompt(f"没关系，老师告诉你答案哦～正确答案是：{correct}")

        # Process answer
        correct_option = next(o for o in quiz["options"] if o["correct"])
        is_correct = self._quiz_chosen == correct_option["key"]

        if is_correct:
            await self._send_component("encouragement", "show", {
                "text": "太棒了！🌟", "style": "star",
            })
            await self._agent.send_prompt(quiz["explanation_correct"])
        else:
            await self._send_component("encouragement", "show", {
                "text": "加油！💪", "style": "clap",
            })
            await self._agent.send_prompt(quiz["explanation_wrong"])

        # Show result
        await self._send_component("quiz_result", "show", {
            "correct": is_correct,
            "explanation": (
                quiz["explanation_correct"] if is_correct
                else quiz["explanation_wrong"]
            ),
            "correct_answer": correct_option["text"] if not is_correct else None,
        })

        await asyncio.sleep(4)  # Let child read

        # Re-enable raise-hand
        await self._send_component("raise_hand", "update", {"enabled": True})

        self._state = TeachingState.LECTURING
        self._quiz_answer = None
```

- [ ] **Step 4: Run lecture loop tests**

Run: `cd python-agent && python -m pytest tests/test_teaching_controller.py::TestLectureLoop -v`
Expected: 8 tests PASS

- [ ] **Step 5: Run all teaching_controller tests**

Run: `cd python-agent && python -m pytest tests/test_teaching_controller.py -v`  
Expected: 16 tests PASS

- [ ] **Step 6: Commit**

```bash
cd python-agent && git add teaching_controller.py tests/test_teaching_controller.py
git commit -m "feat: implement lecture loop with LLM polish, quiz, and interaction handlers
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `teaching_agent.py` — HTTP server with ASR integration

**Files:**
- Create: `python-agent/teaching_agent.py`

- [ ] **Step 1: Write `teaching_agent.py`**

```python
#!/usr/bin/env python3
"""Teaching Digital Human Agent — chapter-based lectures with interruptible Q&A.

Architecture:
  TeachingController — state machine for LECTURING ↔ ANSWERING ↔ QUIZZING
  CourseManager       — YAML course config loading
  Qwen ASR            — child-optimized speech recognition
  DeepSeek LLM        — skeleton polish + Q&A + transition generation

Usage:
  export DASHSCOPE_API_KEY=sk-xxx
  export DEEPSEEK_API_KEY=sk-xxx
  export LIVEAVATAR_API_KEY=lk_live_xxx
  export LIVEAVATAR_AVATAR_ID=avatar_xxx
  python teaching_agent.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import traceback
import uuid
from pathlib import Path

from aiohttp import web
from openai import AsyncOpenAI

from dashscope.audio.qwen_omni import (
    MultiModality,
    OmniRealtimeCallback,
    OmniRealtimeConversation,
)
from dashscope.audio.qwen_omni.omni_realtime import TranscriptionParams

from liveavatar_channel_sdk import (
    AgentListener,
    AvatarAgent,
    AvatarAgentConfig,
    SessionState,
)

from course_manager import CourseManager
from teaching_controller import (
    TeachingController,
    TeachingState,
    LECTURE_SYSTEM_PROMPT,
    QA_SYSTEM_PROMPT,
    TRANSITION_PROMPT,
    INTERACTION_FEEDBACK_PROMPT,
)
from llm_client import LlmClient

HERE = Path(__file__).parent
FRONTEND = HERE.parent / "frontend"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_KEY = os.getenv("LIVEAVATAR_API_KEY", "")
AVATAR_ID = os.getenv("LIVEAVATAR_AVATAR_ID", "")
BASE_URL = os.getenv("LIVEAVATAR_BASE_URL", "https://liveavatar.aimiai.com/vih/dispatcher")
VOICE_ID = os.getenv("LIVEAVATAR_VOICE_ID", None)
HTTP_PORT = int(os.getenv("TEACHING_HTTP_PORT", "8082"))

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_ASR_MODEL = os.getenv("DASHSCOPE_ASR_MODEL", "qwen3-asr-flash-realtime")
DASHSCOPE_ASR_URL = os.getenv("DASHSCOPE_ASR_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

COURSE_NAME = os.getenv("TEACHING_COURSE", "thinking")
COURSE_PATH = HERE / "config" / "courses" / f"{COURSE_NAME}.yaml"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", datefmt="%H:%M:%S")
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(handler)
    for name in ("httpx", "httpcore", "asyncio", "aiohttp", "websockets"):
        logging.getLogger(name).setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# WS client patch for scene.ready hook
# ---------------------------------------------------------------------------

_ws_patched = False
_scene_ready_hook: callable | None = None

def _patch_ws_client() -> None:
    global _ws_patched
    if _ws_patched:
        return
    _ws_patched = True
    from liveavatar_channel_sdk._ws_client import _AvatarWsClient as _Cls

    _orig_handle_text = _Cls._handle_text
    _orig_send_json = _Cls.send_json

    async def _patched_handle_text(self, raw: str) -> None:
        logging.getLogger(__name__).debug("🔽 RAW RECV: %s", raw[:600])
        if _scene_ready_hook and '"event":"scene.ready"' in raw:
            logging.getLogger(__name__).info("🎬 scene.ready → starting teaching")
            try:
                await _scene_ready_hook()
            except Exception as exc:
                logging.getLogger(__name__).error("scene.ready hook failed: %s", exc)
        await _orig_handle_text(self, raw)

    async def _patched_send_json(self, message: dict) -> None:
        logging.getLogger(__name__).debug("🔼 RAW SEND: %s", json.dumps(message, ensure_ascii=False)[:600])
        raw = json.dumps(message, ensure_ascii=False)
        await self._ws.send(raw)

    _Cls._handle_text = _patched_handle_text
    _Cls.send_json = _patched_send_json


def json_response(data, **kwargs):
    return web.json_response(data, dumps=lambda obj: json.dumps(obj, ensure_ascii=False), **kwargs)

# ---------------------------------------------------------------------------
# Qwen ASR Manager (child-optimized)
# ---------------------------------------------------------------------------

class QwenAsrManager:
    """Manages Qwen ASR realtime connection with child-optimized parameters."""

    def __init__(self, *, on_transcript, on_speech_started=None, on_speech_stopped=None,
                 on_interim=None, on_error=None) -> None:
        self._on_transcript = on_transcript
        self._on_speech_started = on_speech_started
        self._on_speech_stopped = on_speech_stopped
        self._on_interim = on_interim
        self._on_error = on_error
        self._conversation: OmniRealtimeConversation | None = None
        self._callback: AsrCallback | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reconnect_attempts = 0
        self._max_reconnect = 3
        self._closed = False

    async def connect(self) -> None:
        if not DASHSCOPE_API_KEY:
            raise RuntimeError("DASHSCOPE_API_KEY is not set")
        self._loop = asyncio.get_running_loop()
        self._closed = False
        callback = AsrCallback(
            loop=self._loop,
            on_transcript=self._on_transcript,
            on_disconnect=self._on_asr_disconnect,
            on_speech_started=self._on_speech_started,
            on_speech_stopped=self._on_speech_stopped,
            on_interim=self._on_interim,
        )
        self._callback = callback
        conversation = OmniRealtimeConversation(
            model=DASHSCOPE_ASR_MODEL,
            url=DASHSCOPE_ASR_URL,
            api_key=DASHSCOPE_API_KEY,
            callback=callback,
        )
        callback.conversation = conversation
        self._conversation = conversation
        await self._loop.run_in_executor(None, conversation.connect)
        transcription_params = TranscriptionParams(
            language="zh",
            sample_rate=16000,
            input_audio_format="pcm",
        )
        conversation.update_session(
            output_modalities=[MultiModality.TEXT],
            enable_turn_detection=True,
            turn_detection_type="server_vad",
            turn_detection_threshold=0.0,
            turn_detection_silence_duration_ms=600,  # Child-optimized: 600ms
            enable_input_audio_transcription=True,
            transcription_params=transcription_params,
        )
        self._reconnect_attempts = 0
        logging.getLogger(__name__).info("🎤 Qwen ASR connected (child mode, silence=600ms)")

    def feed_audio(self, pcm_bytes: bytes) -> None:
        conversation = self._conversation
        if conversation is None:
            return
        b64 = base64.b64encode(pcm_bytes).decode()
        conversation.append_audio(b64)

    async def close(self) -> None:
        self._closed = True
        conversation = self._conversation
        if conversation is not None:
            loop = self._loop or asyncio.get_running_loop()
            await loop.run_in_executor(None, conversation.close)
            self._conversation = None

    async def _on_asr_disconnect(self) -> None:
        if self._closed:
            return
        self._conversation = None
        await self._reconnect()

    async def _reconnect(self) -> bool:
        if self._closed:
            return False
        self._reconnect_attempts += 1
        if self._reconnect_attempts > self._max_reconnect:
            if self._on_error:
                await self._on_error("ASR_DISCONNECT", "ASR reconnect failed")
            return False
        delay = 2 ** (self._reconnect_attempts - 1)
        logging.getLogger(__name__).info("🎤 ASR reconnecting in %ds...", delay)
        await asyncio.sleep(delay)
        try:
            await self.connect()
            return True
        except Exception as e:
            logging.getLogger(__name__).error("🎤 ASR reconnect error: %s", e)
            return False


class AsrCallback(OmniRealtimeCallback):
    """Receives ASR events from DashScope WebSocket thread."""

    def __init__(self, *, loop, on_transcript, on_disconnect=None,
                 on_speech_started=None, on_speech_stopped=None, on_interim=None) -> None:
        super().__init__()
        self.conversation: OmniRealtimeConversation | None = None
        self._loop = loop
        self._on_transcript = on_transcript
        self._on_disconnect = on_disconnect
        self._on_speech_started = on_speech_started
        self._on_speech_stopped = on_speech_stopped
        self._on_interim = on_interim

    def on_open(self) -> None:
        logging.getLogger(__name__).info("🎤 ASR WebSocket opened")

    def on_close(self, code: int, msg: str) -> None:
        logging.getLogger(__name__).info("🎤 ASR closed — code=%s msg=%s", code, msg)
        if self._on_disconnect:
            asyncio.run_coroutine_threadsafe(self._on_disconnect(), self._loop)

    def on_event(self, response: dict) -> None:
        try:
            event_type = response.get("type", "")
            if event_type == "session.created":
                sid = response.get("session", {}).get("id", "?")
                logging.getLogger(__name__).info("🎤 ASR session: %s...", sid[:20])
            elif event_type == "conversation.item.input_audio_transcription.completed":
                transcript = response.get("transcript", "").strip()
                if transcript and self._on_transcript:
                    asyncio.run_coroutine_threadsafe(self._on_transcript(transcript), self._loop)
            elif event_type == "conversation.item.input_audio_transcription.text":
                stash = response.get("text", "")
                if stash and self._on_interim:
                    asyncio.run_coroutine_threadsafe(self._on_interim(stash), self._loop)
            elif event_type == "input_audio_buffer.speech_started":
                if self._on_speech_started:
                    asyncio.run_coroutine_threadsafe(self._on_speech_started(), self._loop)
            elif event_type == "input_audio_buffer.speech_stopped":
                if self._on_speech_stopped:
                    asyncio.run_coroutine_threadsafe(self._on_speech_stopped(), self._loop)
        except Exception:
            logging.getLogger(__name__).error("🎤 ASR callback error: %s", traceback.format_exc())

# ---------------------------------------------------------------------------
# Agent Listener
# ---------------------------------------------------------------------------

class TeachingListener(AgentListener):
    """Forwards platform events to TeachingController."""

    def __init__(self, *, asr_manager: QwenAsrManager | None = None,
                 llm_client: LlmClient | None = None) -> None:
        self.agent: AvatarAgent | None = None
        self.controller: TeachingController | None = None
        self.asr_manager = asr_manager
        self.llm_client = llm_client
        self._echo_cooldown_until: float = 0.0
        self._current_voice_request_id: str | None = None
        self._processing_task: asyncio.Task | None = None
        self._current_response_id: str | None = None
        self._hand_raise_timeout_task: asyncio.Task | None = None

    def set_controller(self, ctrl: TeachingController) -> None:
        self.controller = ctrl

    # -- Platform session events --

    async def on_session_init(self, session_id: str, user_id: str) -> None:
        logging.getLogger(__name__).info("⬇️  session.init | %s %s", session_id, user_id)

    async def on_session_state(self, state: SessionState) -> None:
        if hasattr(state, 'value') and state.value == 'IDLE' and self.controller:
            self.controller.notify_platform_idle()

    async def on_session_closing(self, reason: str | None) -> None:
        logging.getLogger(__name__).info("⬇️  session.closing | %s", reason)

    async def on_error(self, code: str, message: str) -> None:
        logging.getLogger(__name__).error("⬇️  error | %s %s", code, message)

    async def on_audio_frame(self, frame) -> None:
        if self.asr_manager:
            self.asr_manager.feed_audio(frame.payload)

    async def on_closed(self, code: int, reason: str) -> None:
        logging.getLogger(__name__).info("🔌 WS closed | %s %s", code, reason)

    async def on_interrupt(self) -> None:
        """Handle control.interrupt from client."""
        logging.getLogger(__name__).info("⬇️  control.interrupt")
        if self._processing_task and not self._processing_task.done():
            self._processing_task.cancel()

    # -- ASR callbacks with state-gating --

    async def _on_speech_started(self) -> None:
        ctrl = self.controller
        if ctrl is None:
            return
        if time.time() < self._echo_cooldown_until:
            return  # echo cooldown

        state = ctrl.state
        if state in (TeachingState.LECTURING,):
            return  # Ignore VAD unless hand raised
        if state == TeachingState.WAITING_INTERACT:
            # Accept free-speech during interaction
            self._current_voice_request_id = str(uuid.uuid4())
            await self.agent.send_voice_start(self._current_voice_request_id)
        elif state == TeachingState.ANSWERING:
            # Cancel current answer, start new voice
            if self._processing_task and not self._processing_task.done():
                self._processing_task.cancel()
            self._current_voice_request_id = str(uuid.uuid4())
            await self.agent.send_voice_start(self._current_voice_request_id)

    async def _on_speech_stopped(self) -> None:
        if self.agent and self._current_voice_request_id:
            await self.agent.send_voice_finish(self._current_voice_request_id)

    async def _on_asr_transcript(self, text: str) -> None:
        ctrl = self.controller
        agent = self.agent
        if ctrl is None or agent is None:
            return
        if time.time() < self._echo_cooldown_until:
            return

        state = ctrl.state
        logging.getLogger(__name__).info("🎤 ASR final [%s]: %s", state.value if state else "?", text)

        if state == TeachingState.ANSWERING:
            await self._handle_qa(text)
        elif state == TeachingState.WAITING_INTERACT:
            await self._handle_interaction_response(text)

    async def _on_asr_interim(self, text: str) -> None:
        """Forward interim results to platform."""
        agent = self.agent
        request_id = self._current_voice_request_id
        if agent and request_id:
            await agent.send_asr_partial(request_id, text, 0)

    async def _on_asr_error(self, code: str, message: str) -> None:
        if self.agent:
            await self.agent.send_error(code, message)

    # -- Q&A handling --

    async def _handle_qa(self, text: str) -> None:
        """Handle user question during ANSWERING state."""
        ctrl = self.controller
        agent = self.agent
        if ctrl is None or agent is None or self.llm_client is None:
            return

        # Cancel hand-raise timeout
        if self._hand_raise_timeout_task:
            self._hand_raise_timeout_task.cancel()
            self._hand_raise_timeout_task = None

        self._processing_task = asyncio.create_task(self._qa_flow(text))

    async def _qa_flow(self, question: str) -> None:
        ctrl = self.controller
        agent = self.agent
        if ctrl is None or agent is None or self.llm_client is None:
            return

        try:
            # Generate answer with Q&A context
            chapter = ctrl._cm.get_chapter(ctrl._breakpoint["chapter_id"]) if ctrl._breakpoint else None
            chapter_title = chapter["title"] if chapter else "课程"

            qa_prompt = QA_SYSTEM_PROMPT.format(chapter_title=chapter_title)
            self.llm_client._system_prompt = qa_prompt
            self.llm_client._messages = [{"role": "system", "content": qa_prompt}]

            response_id = str(uuid.uuid4())
            self._current_response_id = response_id
            await agent.send_response_start("qa", response_id)

            seq = 0
            async def send_chunk(delta: str):
                nonlocal seq
                await agent.send_response_chunk("qa", response_id, seq, int(time.time() * 1000), delta)
                seq += 1

            answer = await self.llm_client.generate_streaming(question, send_chunk, max_tokens=256)
            await agent.send_response_done("qa", response_id)
            self._echo_cooldown_until = time.time() + 1.5

            logging.getLogger(__name__).info("✅ QA done: %s", answer[:80])

            # Now generate transition
            await self._generate_transition()

        except asyncio.CancelledError:
            logging.getLogger(__name__).info("⏹️ QA cancelled by interrupt")
        except Exception as e:
            logging.getLogger(__name__).error("QA flow error: %s", e)
            fallback = "哎呀，老师需要想一想这个问题。我们先把刚才的内容学完，好不好？"
            await agent.send_prompt(fallback)

    async def _generate_transition(self) -> None:
        """Generate a transition phrase to return to lecture."""
        ctrl = self.controller
        agent = self.agent
        if ctrl is None or agent is None or self.llm_client is None:
            return

        ctrl._state = TeachingState.TRANSITIONING

        try:
            chapter = ctrl._cm.get_chapter(ctrl._breakpoint["chapter_id"]) if ctrl._breakpoint else None
            context = chapter["title"] if chapter else "课程内容"
            prompt = TRANSITION_PROMPT.format(context=context)

            self.llm_client.reset_context()
            self.llm_client._system_prompt = LECTURE_SYSTEM_PROMPT
            self.llm_client._messages = [{"role": "system", "content": LECTURE_SYSTEM_PROMPT}]

            transition = await self.llm_client.generate(prompt, max_tokens=128)
            transition = (transition or "").strip() or "好啦，我们继续看下一个有趣的知识吧！"

            await agent.send_prompt(transition)
            logging.getLogger(__name__).info("🔁 Transition: %s", transition[:60])

        except Exception as e:
            logging.getLogger(__name__).error("Transition generation failed: %s", e)
            await agent.send_prompt("好啦，我们继续看下一个有趣的知识吧！")

        # Resume lecture from breakpoint
        ctrl._state = TeachingState.LECTURING
        ctrl._task = asyncio.create_task(ctrl._resume_lecture())

    async def _handle_interaction_response(self, text: str) -> None:
        """Handle student response to teacher's interaction prompt."""
        ctrl = self.controller
        agent = self.agent
        if ctrl is None or agent is None or self.llm_client is None:
            return

        ctrl._state = TeachingState.PROCESSING_INTER

        try:
            chapter = ctrl._cm.get_chapter(ctrl._current_chapter_id) if ctrl._current_chapter_id else None
            question = chapter["interaction"]["prompt"] if chapter and chapter.get("interaction") else "问题"
            prompt = INTERACTION_FEEDBACK_PROMPT.format(question=question, response=text)

            self.llm_client.reset_context()
            self.llm_client._system_prompt = LECTURE_SYSTEM_PROMPT
            self.llm_client._messages = [{"role": "system", "content": LECTURE_SYSTEM_PROMPT}]

            feedback = await self.llm_client.generate(prompt, max_tokens=200)
            await agent.send_prompt(feedback or f"谢谢你告诉老师！我们继续往下看吧～")

        except Exception as e:
            logging.getLogger(__name__).error("Interaction feedback failed: %s", e)
            await agent.send_prompt("谢谢你告诉老师！我们继续往下看吧～")

        ctrl._state = TeachingState.LECTURING

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_agent: AvatarAgent | None = None
_listener: TeachingListener | None = None
_asr_manager: QwenAsrManager | None = None
_llm_client: LlmClient | None = None
_controller: TeachingController | None = None
_course_manager: CourseManager | None = None
_session_info: dict = {}


async def init_teaching() -> None:
    global _agent, _listener, _asr_manager, _llm_client, _controller, _course_manager
    global _session_info, _scene_ready_hook

    logger = logging.getLogger(__name__)

    # Load course
    if not COURSE_PATH.exists():
        logger.error("Course config not found: %s", COURSE_PATH)
        raise FileNotFoundError(f"Course config not found: {COURSE_PATH}")
    _course_manager = CourseManager(COURSE_PATH)
    course = _course_manager.get_course()
    logger.info("📚 Loaded course: %s (%d chapters)", course["title"], _course_manager.get_chapter_count())

    # Init LLM clients (lecture + Q&A)
    _llm_client = LlmClient(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        model=DEEPSEEK_MODEL,
        system_prompt=LECTURE_SYSTEM_PROMPT,
    )

    # Init ASR
    use_developer_asr = bool(DASHSCOPE_API_KEY)
    if use_developer_asr:
        _asr_manager = QwenAsrManager(on_transcript=None)
    else:
        logger.warning("⚠️  DASHSCOPE_API_KEY not set — falling back to Platform ASR")
        _asr_manager = None

    # Init listener
    _listener = TeachingListener(asr_manager=_asr_manager, llm_client=_llm_client)

    # Wire ASR callbacks
    if _asr_manager:
        _asr_manager._on_transcript = _listener._on_asr_transcript
        _asr_manager._on_speech_started = _listener._on_speech_started
        _asr_manager._on_speech_stopped = _listener._on_speech_stopped
        _asr_manager._on_interim = _listener._on_asr_interim
        _asr_manager._on_error = _listener._on_asr_error
        await _asr_manager.connect()

    # Start platform session
    config = AvatarAgentConfig(
        api_key=API_KEY,
        avatar_id=AVATAR_ID,
        base_url=BASE_URL,
        developer_asr=use_developer_asr,
        developer_tts=False,
        voice_id=VOICE_ID,
        timeout=30.0,
    )
    _agent = AvatarAgent(config, _listener)
    _listener.agent = _agent

    logger.info("🚀 Starting session with avatarId=%s", AVATAR_ID)
    result = await _agent.start()
    _session_info = {
        "userToken": result.user_token,
        "sfuUrl": result.sfu_url,
        "sessionId": result.session_id,
    }
    logger.info("📋 sessionId: %s", result.session_id)

    # Init controller
    _controller = TeachingController(
        agent=_agent,
        course_manager=_course_manager,
        llm_client=_llm_client,
    )
    _listener.set_controller(_controller)

    # Auto-start on scene.ready
    async def _on_scene_ready() -> None:
        if _controller and _controller.state == TeachingState.IDLE:
            _controller.start()
    _scene_ready_hook = _on_scene_ready


async def shutdown_teaching() -> None:
    global _agent, _asr_manager, _controller
    if _controller:
        _controller.stop()
        _controller = None
    if _asr_manager:
        await _asr_manager.close()
        _asr_manager = None
    if _agent:
        await _agent.stop()
        _agent = None
    logging.getLogger(__name__).info("Teaching shutdown complete")

# ---------------------------------------------------------------------------
# HTTP Handlers
# ---------------------------------------------------------------------------

async def handle_start_session(request: web.Request) -> web.Response:
    if not _session_info:
        return json_response({"success": False, "error": "Session not ready"}, status=503)
    return json_response({
        "success": True,
        "userToken": _session_info["userToken"],
        "sfuUrl": _session_info["sfuUrl"],
        "sessionId": _session_info.get("sessionId"),
    })

async def handle_teaching_start(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response({"success": False, "error": "Not initialized"}, status=500)
    _controller.start()
    return json_response({"success": True})

async def handle_teaching_stop(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response({"success": False, "error": "Not initialized"}, status=500)
    _controller.stop()
    return json_response({"success": True})

async def handle_teaching_pause(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response({"success": False, "error": "Not initialized"}, status=500)
    _controller.pause()
    return json_response({"success": True})

async def handle_teaching_resume(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response({"success": False, "error": "Not initialized"}, status=500)
    _controller.resume()
    return json_response({"success": True})

async def handle_raise_hand(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response({"success": False, "error": "Not initialized"}, status=500)
    _controller.raise_hand()
    # Start 15s timeout for child
    if _listener:
        async def _hand_timeout():
            await asyncio.sleep(15)
            ctrl = _controller
            if ctrl and ctrl.state == TeachingState.ANSWERING:
                ctrl.cancel_hand()
                if _agent:
                    await _agent.send_prompt("老师没听到你的声音哦，你想好了再举手告诉老师吧！")
        _listener._hand_raise_timeout_task = asyncio.create_task(_hand_timeout())
    return json_response({"success": True})

async def handle_cancel_hand(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response({"success": False, "error": "Not initialized"}, status=500)
    _controller.cancel_hand()
    if _listener and _listener._hand_raise_timeout_task:
        _listener._hand_raise_timeout_task.cancel()
    return json_response({"success": True})

async def handle_quiz_answer(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response({"success": False, "error": "Not initialized"}, status=500)
    try:
        body = await request.json()
        chapter_id = body.get("chapter_id", "")
        answer = body.get("answer", "")
    except Exception:
        return json_response({"success": False, "error": "Invalid JSON"}, status=400)
    if not chapter_id or not answer:
        return json_response({"success": False, "error": "chapter_id and answer required"}, status=400)
    try:
        _controller.answer_quiz(chapter_id, answer)
        return json_response({"success": True})
    except ValueError as e:
        return json_response({"success": False, "error": str(e)}, status=400)

async def handle_teaching_status(request: web.Request) -> web.Response:
    if _controller is None:
        return json_response({"success": False, "error": "Not initialized"}, status=500)
    return json_response(_controller.get_status())

async def handle_index(request: web.Request) -> web.Response:
    html_path = FRONTEND / "teaching.html"
    if html_path.exists():
        return web.Response(body=html_path.read_bytes(), content_type="text/html")
    return web.Response(text="<h1>Teaching Agent Ready</h1>", content_type="text/html")

async def handle_sdk_js(request: web.Request) -> web.Response:
    js_path = FRONTEND / "node_modules" / "@sanseng" / "liveavatar-js-sdk" / "dist" / "index.full.umd.js"
    if js_path.exists():
        return web.Response(body=js_path.read_bytes(), content_type="application/javascript")
    return web.Response(status=404)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not DEEPSEEK_API_KEY:
        print("⚠️  DEEPSEEK_API_KEY not set — LLM features won't work")
    if not API_KEY:
        print("⚠️  LIVEAVATAR_API_KEY not set")
    if not AVATAR_ID:
        print("⚠️  LIVEAVATAR_AVATAR_ID not set")

    setup_logging()
    _patch_ws_client()
    logger = logging.getLogger(__name__)
    logger.info("📚 Starting Teaching Agent on port %d", HTTP_PORT)

    app = web.Application()
    app.router.add_post("/api/start-session", handle_start_session)
    app.router.add_post("/api/teaching/start", handle_teaching_start)
    app.router.add_post("/api/teaching/stop", handle_teaching_stop)
    app.router.add_post("/api/teaching/pause", handle_teaching_pause)
    app.router.add_post("/api/teaching/resume", handle_teaching_resume)
    app.router.add_post("/api/teaching/raise-hand", handle_raise_hand)
    app.router.add_post("/api/teaching/cancel-hand", handle_cancel_hand)
    app.router.add_post("/api/teaching/quiz-answer", handle_quiz_answer)
    app.router.add_get("/api/teaching/status", handle_teaching_status)
    app.router.add_get("/", handle_index)
    app.router.add_get("/sdk.js", handle_sdk_js)

    app.on_startup.append(lambda _app: init_teaching())
    app.on_shutdown.append(lambda _app: shutdown_teaching())

    web.run_app(app, host="0.0.0.0", port=HTTP_PORT)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify file structure**

Run: `cd python-agent && python -c "import teaching_agent; print('Import OK')"`
Expected: `Import OK` (may need env vars for API keys, but import should succeed)

- [ ] **Step 3: Commit**

```bash
cd python-agent && git add teaching_agent.py
git commit -m "feat: add teaching_agent.py — HTTP server with child-optimized ASR and full teaching flow
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Course content — `config/courses/thinking.yaml`

**Files:**
- Create: `python-agent/config/courses/thinking.yaml`

- [ ] **Step 1: Create course config**

```bash
mkdir -p python-agent/config/courses
```

- [ ] **Step 2: Write `thinking.yaml`**

```yaml
course:
  title: "思维小达人 — 第一课：火眼金睛辨对错"
  lang: zh
  default_tts_speed: 0.9

  assets:
    cards:
      - id: "what_is_logic"
        title: "什么是火眼金睛？🧐"
        content: "就是学会判断别人说的话对不对！"
        image: "/assets/card-logic-kids.png"
      - id: "fallacy_traps"
        title: "三个小陷阱 🕳️"
        content: "大家都这样、大人说的都对、只能选一个"
        image: "/assets/card-fallacy-traps.png"

chapters:
  - id: "intro"
    title: "小故事：大家都说对，就是对吗？"
    skeleton:
      - "讲一个小故事：森林里的小动物们都往东边跑，小兔子问为什么，大家说「因为大家都在跑呀！」"
      - "问小朋友：你觉得这些小动物做得对吗？如果是你，你会跟着跑吗？"
      - "引出今天的主题：不能因为做的人多，就觉得一件事情是对的哦！"
    interaction:
      prompt: "如果是你，你会跟着大家一起跑吗？为什么呀？"
      expect_keywords: ["会", "不会", "跑", "不跑"]
    visual:
      type: card
      ref: "what_is_logic"

  - id: "fallacy_types"
    title: "三种常见的「小陷阱」"
    skeleton:
      - "第一个小陷阱叫「大家都这样」：别的小朋友都在吃糖，但这不代表你也一定要吃糖哦"
      - "第二个小陷阱叫「大人说的都对」：爸爸妈妈和老师很厉害，但他们有时候也会弄错呢"
      - "第三个小陷阱叫「只能选一个」：就像不是只有红色和蓝色，世界上还有很多好看的颜色！"
    quiz:
      question: "小明说：'我们班同学都有这个玩具，所以我也一定要买！' 小明掉进了哪个小陷阱呀？"
      options:
        - { key: "A", text: "「大家都这样」陷阱 🐑", correct: true }
        - { key: "B", text: "「大人说的都对」陷阱 👨‍🏫", correct: false }
        - { key: "C", text: "「只能选一个」陷阱 🎨", correct: false }
      explanation_correct: "太棒了！🌟 小明因为'别人都有'就想要，这就是「大家都这样」小陷阱！你真是火眼金睛！"
      explanation_wrong: "差一点点就对了！💪 再想想，小明是因为'班里的同学都有'才想要的，跟大人没关系哦～"
    visual:
      type: card
      ref: "fallacy_traps"

  - id: "practice"
    title: "来练一练你的火眼金睛！"
    skeleton:
      - "现在我们来当小侦探，看看下面这些话对不对！"
      - "小红说：'我妈妈说不可以吃太多糖，但小红还是想吃。' 这跟'大人说的都对'陷阱有关吗？"
      - "总结：今天我们学会了三个小陷阱——'大家都这样'、'大人说的都对'、'只能选一个'。以后听到这些话，要用你的火眼金睛看一看哦！"
    interaction:
      prompt: "你今天学到了什么呀？能告诉老师一个你记住的小陷阱吗？"
      expect_keywords: ["大家都这样", "大人", "只能选一个", "陷阱"]
```

- [ ] **Step 3: Validate course YAML**

Run: `cd python-agent && python -c "
from course_manager import CourseManager
from pathlib import Path
cm = CourseManager(Path('config/courses/thinking.yaml'))
print(f'Course: {cm.get_course()[\"title\"]}')
print(f'Chapters: {cm.get_chapter_count()}')
for i in range(1, cm.get_chapter_count() + 1):
    pass  # validation passes
print('Validation OK')
"`
Expected: `Course: 思维小达人 — 第一课：火眼金睛辨对错` / `Chapters: 3` / `Validation OK`

- [ ] **Step 4: Commit**

```bash
cd python-agent && git add config/courses/thinking.yaml
git commit -m "feat: add thinking course config — 3 chapters for children aged 4-10
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Run all tests & verify

- [ ] **Step 1: Run full test suite**

```bash
cd python-agent && python -m pytest tests/test_course_component.py tests/test_course_manager.py tests/test_teaching_controller.py -v
```
Expected: 34 tests PASS (10 + 14 + 10 = 34, or close to it)

- [ ] **Step 2: Run existing tests to confirm no regressions**

```bash
cd python-agent && python -m pytest tests/ -v --ignore=tests/test_course_component.py --ignore=tests/test_course_manager.py --ignore=tests/test_teaching_controller.py
```
Expected: All existing tests PASS (no regressions in broadcast/chat tests)

- [ ] **Step 3: Verify teaching_agent.py imports cleanly**

```bash
cd python-agent && python -c "
import teaching_agent
import teaching_controller
import course_manager
import course_component
print('All imports OK')
"
```
Expected: `All imports OK`

- [ ] **Step 4: Verify teaching_agent starts (dry run — will fail without API keys but should show correct startup logs)**

```bash
cd python-agent && timeout 5 python teaching_agent.py 2>&1 || true
```
Expected: Shows "Starting Teaching Agent on port 8082" and config-related warnings

- [ ] **Step 5: Commit**

```bash
cd python-agent && git add -A
git commit -m "chore: finalize teaching agent — all tests passing, imports clean
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Implementation Summary

| Task | Files | Key Deliverable |
|------|-------|----------------|
| 1 | `course_component.py` + tests | ComponentMessage dataclass |
| 2 | `course_manager.py` + tests | YAML course loading + validation |
| 3 | `teaching_controller.py` + tests | State machine + transitions |
| 4 | `teaching_controller.py` + tests | Lecture loop, quiz, interaction handlers |
| 5 | `teaching_agent.py` | Full HTTP server + ASR + listener |
| 6 | `config/courses/thinking.yaml` | 3-chapter kids thinking course |
| 7 | All tests | Verification + regression check |
