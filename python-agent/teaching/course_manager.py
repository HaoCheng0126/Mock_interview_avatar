"""Course Manager — loads and validates teaching course YAML config."""

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

    def get_course(self) -> dict:
        return self._raw.get("course", {})

    def get_chapter(self, chapter_id: str) -> dict:
        idx = self._chapter_index.get(chapter_id)
        if idx is None:
            raise ValueError(f"Chapter '{chapter_id}' not found in course")
        return self._chapters[idx]

    def get_first_chapter(self) -> dict:
        if not self._chapters:
            raise ValueError("Course has no chapters")
        return self._chapters[0]

    def get_next_chapter(self, current_chapter_id: str) -> dict | None:
        idx = self._chapter_index.get(current_chapter_id)
        if idx is None:
            raise ValueError(f"Chapter '{current_chapter_id}' not found")
        next_idx = idx + 1
        if next_idx >= len(self._chapters):
            return None
        return self._chapters[next_idx]

    def get_chapter_by_index(self, index: int) -> dict | None:
        if 0 <= index < len(self._chapters):
            return self._chapters[index]
        return None

    def get_chapter_count(self) -> int:
        return len(self._chapters)

    def get_cards(self) -> list[dict]:
        assets = self._raw.get("course", {}).get("assets", {}) or {}
        return assets.get("cards", [])

    def get_mindmaps(self) -> dict:
        assets = self._raw.get("course", {}).get("assets", {}) or {}
        return assets.get("mindmaps", {})

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
                raise ValidationError(f"Chapter '{cid}' has empty skeleton")
            for j, step in enumerate(skeleton):
                if isinstance(step, dict) and not str(step.get("text", "")).strip():
                    raise ValidationError(f"Chapter '{cid}' skeleton step {j} missing 'text'")
            quiz = ch.get("quiz")
            if quiz is not None:
                self._validate_quiz(quiz, cid)

    @staticmethod
    def _validate_quiz(quiz: dict, chapter_id: str) -> None:
        if not quiz.get("question"):
            raise ValidationError(f"Quiz in chapter '{chapter_id}' missing 'question'")
        options = quiz.get("options", [])
        if len(options) < 2:
            raise ValidationError(f"Quiz in chapter '{chapter_id}' needs at least 2 options")
        correct_count = sum(1 for o in options if o.get("correct"))
        if correct_count != 1:
            raise ValidationError(f"Quiz in chapter '{chapter_id}' must have exactly one correct option, found {correct_count}")
        if not quiz.get("explanation_correct"):
            raise ValidationError(f"Quiz in chapter '{chapter_id}' missing 'explanation_correct'")
        if not quiz.get("explanation_wrong"):
            raise ValidationError(f"Quiz in chapter '{chapter_id}' missing 'explanation_wrong'")
