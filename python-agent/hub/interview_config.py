"""Read/write config/interview.yaml for the hub's interview settings page.

The page edits two kinds of content:
  * structured form fields — interview basics, interviewer persona, candidate,
    LLM prompt templates, spoken phrases, workflow parameters
  * a raw YAML block — ``rubric`` + ``question_sets`` (too nested for a form)

Saving validates the merged document with the real InterviewManager parser
before writing, so the file on disk can never become unloadable. Fields equal
to their built-in defaults are stripped so the YAML stays minimal.
"""

from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from interview import prompts as prompt_defaults  # noqa: E402
from interview.interview_manager import InterviewManager  # noqa: E402

DEFAULT_INTERVIEW_YAML_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "interview.yaml"
)

FORM_SECTIONS = ("interview", "interviewer", "candidate", "prompts", "speech", "workflow")

_PROMPT_DEFAULTS = {
    "system": prompt_defaults.DEFAULT_SYSTEM_PROMPT,
    "evaluator": prompt_defaults.DEFAULT_EVALUATOR_PROMPT,
    "follow_up_decider": prompt_defaults.DEFAULT_FOLLOW_UP_DECIDER_PROMPT,
    "report": prompt_defaults.DEFAULT_REPORT_PROMPT,
}

_SPEECH_DEFAULTS = {
    "opening_template": prompt_defaults.DEFAULT_OPENING_TEMPLATE,
    "answer_acknowledgements": prompt_defaults.DEFAULT_ANSWER_ACKNOWLEDGEMENTS,
    "final_answer_acknowledgements": prompt_defaults.DEFAULT_FINAL_ANSWER_ACKNOWLEDGEMENTS,
    "follow_up_prefixes": prompt_defaults.DEFAULT_FOLLOW_UP_PREFIXES,
    "first_question_transition": prompt_defaults.DEFAULT_FIRST_QUESTION_TRANSITION,
    "next_question_transition": prompt_defaults.DEFAULT_NEXT_QUESTION_TRANSITION,
    "skip_transition": prompt_defaults.DEFAULT_SKIP_TRANSITION,
    "closing": prompt_defaults.DEFAULT_CLOSING,
    "termination": prompt_defaults.DEFAULT_TERMINATION,
    "thinking_checks": [
        {"after_seconds": seconds, "text": text}
        for seconds, text in prompt_defaults.DEFAULT_THINKING_CHECKS
    ],
}

_WORKFLOW_DEFAULTS = dict(prompt_defaults.DEFAULT_WORKFLOW)

_KNOWLEDGE_DEFAULTS = {
    "max_chars": prompt_defaults.DEFAULT_KNOWLEDGE_MAX_CHARS,
    "entries": [],
}


def read_config(path: Path = DEFAULT_INTERVIEW_YAML_PATH) -> dict[str, Any]:
    """Return form fields (with defaults filled in) + questions/rubric as YAML text."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    form = {
        "interview": {**(raw.get("interview") or {})},
        "interviewer": {**(raw.get("interviewer") or {})},
        "candidate": {**(raw.get("candidate") or {})},
        "prompts": {**_PROMPT_DEFAULTS, **(raw.get("prompts") or {})},
        "speech": {**copy.deepcopy(_SPEECH_DEFAULTS), **(raw.get("speech") or {})},
        "workflow": {**_WORKFLOW_DEFAULTS, **(raw.get("workflow") or {})},
        "knowledge": {
            **copy.deepcopy(_KNOWLEDGE_DEFAULTS),
            **(raw.get("knowledge") or {}),
        },
    }
    questions_doc = {
        "rubric": raw.get("rubric") or {"dimensions": []},
        "question_sets": raw.get("question_sets") or [],
    }
    return {
        **form,
        "questions_yaml": yaml.safe_dump(
            questions_doc, allow_unicode=True, sort_keys=False, width=100
        ),
        "defaults": {"prompts": dict(_PROMPT_DEFAULTS)},
    }


def save_config(
    incoming: dict[str, Any], path: Path = DEFAULT_INTERVIEW_YAML_PATH
) -> None:
    """Merge form + questions YAML into a full document, validate, then write."""
    doc = _build_document(incoming)
    _validate_with_real_parser(doc)
    Path(path).write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )


def build_preview(incoming: dict[str, Any]) -> dict[str, str]:
    """Render the final system prompt / opening text for the given (unsaved)
    form state, using the real InterviewManager pipeline. Raises ValueError on
    invalid config so the page can surface problems before saving."""
    doc = _build_document(incoming)
    manager = _load_manager_from_doc(doc)
    return {
        "system_prompt": manager.build_system_prompt(),
        "opening_text": manager.build_opening_text(),
    }


def _build_document(incoming: dict[str, Any]) -> dict[str, Any]:
    questions_doc = _parse_questions_yaml(incoming.get("questions_yaml") or "")
    doc = {
        "interview": _clean_section(incoming.get("interview")),
        "interviewer": _clean_section(incoming.get("interviewer")),
        "candidate": _clean_section(incoming.get("candidate")),
        "rubric": questions_doc["rubric"],
        "question_sets": questions_doc["question_sets"],
    }
    prompts = _strip_defaults(_clean_section(incoming.get("prompts")), _PROMPT_DEFAULTS)
    speech = _strip_defaults(_clean_section(incoming.get("speech")), _SPEECH_DEFAULTS)
    workflow = _strip_defaults(
        _clean_section(incoming.get("workflow")), _WORKFLOW_DEFAULTS, numeric=True
    )
    knowledge = _clean_knowledge(incoming.get("knowledge"))
    if prompts:
        doc["prompts"] = prompts
    if speech:
        doc["speech"] = speech
    if workflow:
        doc["workflow"] = workflow
    if knowledge:
        doc["knowledge"] = knowledge
    return doc


def _clean_knowledge(raw: Any) -> dict[str, Any]:
    """Normalize the knowledge section; return {} when it matches defaults."""
    if not isinstance(raw, dict):
        return {}
    entries = []
    for item in raw.get("entries") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        if not title and not content:
            continue
        entries.append(
            {
                "title": title or f"资料 {len(entries) + 1}",
                "content": content,
                "enabled": bool(item.get("enabled", True)),
            }
        )
    try:
        max_chars = int(raw.get("max_chars") or _KNOWLEDGE_DEFAULTS["max_chars"])
    except (TypeError, ValueError):
        raise ValueError(f"知识库字数上限必须是数字：{raw.get('max_chars')!r}")
    result: dict[str, Any] = {}
    if entries:
        result["entries"] = entries
    if max_chars != _KNOWLEDGE_DEFAULTS["max_chars"]:
        result["max_chars"] = max_chars
    return result


def _parse_questions_yaml(text: str) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"题库 YAML 语法错误：{e}")
    if not isinstance(parsed, dict):
        raise ValueError("题库 YAML 必须包含 rubric 和 question_sets 两个键")
    question_sets = parsed.get("question_sets")
    if not isinstance(question_sets, list) or not question_sets:
        raise ValueError("question_sets 必须是非空列表")
    for index, item in enumerate(question_sets, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 题必须是字典结构")
        for key in ("id", "title", "prompt"):
            if not str(item.get(key) or "").strip():
                raise ValueError(f"第 {index} 题缺少必填字段 {key}")
    rubric = parsed.get("rubric") or {"dimensions": []}
    if not isinstance(rubric, dict) or not isinstance(
        rubric.get("dimensions", []), list
    ):
        raise ValueError("rubric.dimensions 必须是列表")
    return {"rubric": rubric, "question_sets": question_sets}


def _clean_section(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    cleaned = {}
    for key, value in raw.items():
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                continue
        if isinstance(value, list):
            value = [item for item in value if str(item).strip() != ""]
        cleaned[str(key)] = value
    return cleaned


def _strip_defaults(
    section: dict[str, Any], defaults: dict[str, Any], *, numeric: bool = False
) -> dict[str, Any]:
    result = {}
    for key, value in section.items():
        default = defaults.get(key)
        if numeric and default is not None:
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"工作流参数 {key} 必须是数字：{value!r}")
            if number == float(default):
                continue
            result[key] = int(number) if number.is_integer() else number
            continue
        elif isinstance(value, str) and isinstance(default, str):
            if value.strip() == default.strip():
                continue
        elif value == default:
            continue
        result[key] = value
    return result


def _load_manager_from_doc(doc: dict[str, Any]) -> InterviewManager:
    """Run the real InterviewManager parser on an in-memory document."""
    text = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        try:
            return InterviewManager(f.name)
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"配置校验失败：{e}")


def _validate_with_real_parser(doc: dict[str, Any]) -> None:
    """The InterviewManager parser is the source of truth."""
    _load_manager_from_doc(doc)
