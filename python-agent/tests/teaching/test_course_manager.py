"""Tests for course_manager.py — YAML course loading and validation."""

import tempfile
from pathlib import Path
import pytest
from teaching.course_manager import CourseManager, CourseLoadError, ValidationError


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
    def test_skeleton_object_without_text_raises(self):
        yaml = """
course:
  title: Test
  lang: zh
chapters:
  - id: "bad"
    title: "Bad"
    skeleton:
      - content: "legacy content field"
        experience:
          primitive: cut_fold_unfold
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml)
            p = Path(f.name)
        try:
            with pytest.raises(ValidationError, match="skeleton.*text"):
                CourseManager(p)
        finally:
            p.unlink(missing_ok=True)

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

    def test_clothing_course_has_interactive_attribute_sort_experiences(self):
        course_path = (
            Path(__file__).parents[2]
            / "config"
            / "courses"
            / "神秘服装店的订单侦探小队的颜色与口袋大冒险_7-8.yaml"
        )
        cm = CourseManager(course_path)
        first = cm.get_first_chapter()

        assert first["skeleton"][0]["experience"]["primitive"] == "attribute_sort"
        assert first["skeleton"][0]["experience"]["props"]["target"] == {
            "color": "red",
            "pocket": True,
        }
