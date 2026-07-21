"""Read/write config/interview.yaml for the hub's interview settings page.

The page edits structured form fields — interview basics, interviewer persona,
candidate, the positions list (岗位 → 业务题库 + 核心考察点), LLM prompt templates,
spoken phrases, workflow and plan parameters.

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

FORM_SECTIONS = (
    "interview", "interviewer", "candidate", "prompts", "speech", "workflow", "plan",
    "company_knowledge",
)

COMPANY_KNOWLEDGE_CATEGORIES = {
    "company_overview",
    "products_business",
    "customers_market",
    "culture_values",
    "business_scenarios",
    "tech_stack",
    "other",
}

_PROMPT_DEFAULTS = {
    "system": prompt_defaults.DEFAULT_SYSTEM_PROMPT,
    "evaluator": prompt_defaults.DEFAULT_EVALUATOR_PROMPT,
    "follow_up_decider": prompt_defaults.DEFAULT_FOLLOW_UP_DECIDER_PROMPT,
    "report": prompt_defaults.DEFAULT_REPORT_PROMPT,
    "planner": prompt_defaults.DEFAULT_PLANNER_PROMPT,
    "closing_comment": prompt_defaults.DEFAULT_CLOSING_COMMENT_PROMPT,
}

_SPEECH_DEFAULTS = {
    "self_intro_prompt": prompt_defaults.DEFAULT_SELF_INTRO_PROMPT,
    "prep_template": prompt_defaults.DEFAULT_PREP_TEMPLATE,
    "opening_template": prompt_defaults.DEFAULT_OPENING_TEMPLATE,
    "answer_acknowledgements": prompt_defaults.DEFAULT_ANSWER_ACKNOWLEDGEMENTS,
    "final_answer_acknowledgements": prompt_defaults.DEFAULT_FINAL_ANSWER_ACKNOWLEDGEMENTS,
    "follow_up_prefixes": prompt_defaults.DEFAULT_FOLLOW_UP_PREFIXES,
    "first_question_transition": prompt_defaults.DEFAULT_FIRST_QUESTION_TRANSITION,
    "next_question_transitions": prompt_defaults.DEFAULT_NEXT_QUESTION_TRANSITIONS,
    "skip_transition": prompt_defaults.DEFAULT_SKIP_TRANSITION,
    "closing": prompt_defaults.DEFAULT_CLOSING,
    "termination": prompt_defaults.DEFAULT_TERMINATION,
    "thinking_checks": [
        {"after_seconds": seconds, "text": text}
        for seconds, text in prompt_defaults.DEFAULT_THINKING_CHECKS
    ],
}

_WORKFLOW_DEFAULTS = dict(prompt_defaults.DEFAULT_WORKFLOW)

_PLAN_DEFAULTS = dict(prompt_defaults.DEFAULT_PLAN)


def read_config(path: Path = DEFAULT_INTERVIEW_YAML_PATH) -> dict[str, Any]:
    """Return form fields (with defaults filled in) + questions/rubric as YAML text."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    speech_raw = {**(raw.get("speech") or {})}
    if not speech_raw.get("next_question_transitions"):
        legacy_transition = str(speech_raw.get("next_question_transition") or "").strip()
        if legacy_transition:
            speech_raw["next_question_transitions"] = [legacy_transition]
        else:
            # An explicitly stored [] means "use runtime defaults", not "show an
            # empty editor". Drop it before merging so the admin page displays
            # the same effective phrases that candidates actually hear and see.
            speech_raw.pop("next_question_transitions", None)
    speech_raw.pop("next_question_transition", None)
    form = {
        "interview": {**(raw.get("interview") or {})},
        "interviewer": {**(raw.get("interviewer") or {})},
        "prompts": {**_PROMPT_DEFAULTS, **(raw.get("prompts") or {})},
        "speech": {**copy.deepcopy(_SPEECH_DEFAULTS), **speech_raw},
        "workflow": {**_WORKFLOW_DEFAULTS, **(raw.get("workflow") or {})},
        "plan": {**_PLAN_DEFAULTS, **(raw.get("plan") or {})},
        "company_knowledge": {
            "max_interview_chars": 4000,
            "max_internal_chars": 3000,
            **(raw.get("company_knowledge") or {}),
        },
    }
    return {
        **form,
        "positions": raw.get("positions") or [],
        "defaults": {"prompts": dict(_PROMPT_DEFAULTS)},
    }


def save_config(
    incoming: dict[str, Any], path: Path = DEFAULT_INTERVIEW_YAML_PATH
) -> None:
    """Merge the form into a full document, validate, then write."""
    doc = _build_document(incoming)
    _validate_with_real_parser(doc)
    Path(path).write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )


def build_preview(incoming: dict[str, Any]) -> dict[str, str]:
    """Build a unified template preview for the given (unsaved) form state.

    Unlike the runtime prompt, this preview intentionally keeps placeholders
    visible so operators can inspect the full template pack without session-
    specific values being substituted in.
    """
    doc = _build_document(incoming)
    manager = _load_manager_from_doc(doc)
    cfg = manager.config
    interviewer_rules = "\n".join(f"- {rule}" for rule in cfg.interviewer.rules) or "- （未设置）"
    full_preview = "\n\n".join(
        [
            "【面试官人设】\n"
            f"姓名：{cfg.interviewer.name}\n"
            f"风格：{cfg.interviewer.style or '（未设置）'}\n"
            f"规则：\n{interviewer_rules}",
            f"【准备阶段话术】\n{cfg.speech.prep_template}",
            f"【开场白模板】\n{cfg.speech.opening_template}",
            f"【自我介绍问题】\n{cfg.speech.self_intro_prompt}",
            f"【系统提示词】\n{cfg.prompts.system}",
            f"【出题提示词】\n{cfg.prompts.planner}",
            f"【追问判定提示词】\n{cfg.prompts.follow_up_decider}",
            f"【回答评估提示词】\n{cfg.prompts.evaluator}",
            f"【结束点评提示词】\n{cfg.prompts.closing_comment}",
        ]
    )
    system_prompt = manager.build_system_prompt()
    position_names = "、".join(position.name for position in cfg.positions) or "（未配置岗位）"
    competencies = "；".join(
        position.core_competencies
        for position in cfg.positions
        if position.core_competencies
    ) or "（未指定核心考察点）"
    system_prompt = system_prompt.replace("{position}", position_names).replace(
        "{core_competencies}", competencies
    )
    return {
        "full_preview": full_preview,
        "system_prompt": system_prompt,
        "opening_text": manager.build_opening_text(),
    }


def _build_document(incoming: dict[str, Any]) -> dict[str, Any]:
    doc = {
        "interview": _clean_section(incoming.get("interview")),
        "interviewer": _clean_section(incoming.get("interviewer")),
    }
    positions = _clean_positions(incoming.get("positions"))
    prompts = _strip_defaults(_clean_section(incoming.get("prompts")), _PROMPT_DEFAULTS)
    speech = _strip_defaults(_clean_section(incoming.get("speech")), _SPEECH_DEFAULTS)
    workflow = _strip_defaults(
        _clean_section(incoming.get("workflow")), _WORKFLOW_DEFAULTS, numeric=True
    )
    plan = _strip_defaults(
        _clean_section(incoming.get("plan")), _PLAN_DEFAULTS, numeric=True
    )
    if positions:
        doc["positions"] = positions
    if prompts:
        doc["prompts"] = prompts
    if speech:
        doc["speech"] = speech
    if workflow:
        doc["workflow"] = workflow
    if plan:
        doc["plan"] = plan
    company_knowledge = _clean_company_knowledge(incoming.get("company_knowledge"))
    if company_knowledge:
        doc["company_knowledge"] = company_knowledge
    return doc


def _clean_company_knowledge(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    entries: list[dict[str, Any]] = []
    total = 0
    for index, item in enumerate(raw.get("entries") or [], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        if not title and not content:
            continue
        if not title:
            raise ValueError(f"公司知识库第 {index} 条缺少标题")
        if len(content) > 4000:
            raise ValueError(f"公司知识库「{title}」超过 4000 字")
        total += len(content)
        if total > 30000:
            raise ValueError("公司知识库正文总计不能超过 30000 字")
        category = str(item.get("category") or "other")
        if category not in COMPANY_KNOWLEDGE_CATEGORIES:
            raise ValueError(f"公司知识库「{title}」分类无效")
        visibility = str(item.get("visibility") or "interview")
        if visibility not in {"interview", "internal"}:
            raise ValueError(f"公司知识库「{title}」可见性无效")
        entries.append(
            {
                "id": str(item.get("id") or f"knowledge-{index}"),
                "title": title,
                "category": category,
                "content": content,
                "visibility": visibility,
                "enabled": bool(item.get("enabled", True)),
            }
        )
    if len(entries) > 30:
        raise ValueError("公司知识库最多 30 条")
    return {
        "max_interview_chars": min(
            4000, max(1, int(raw.get("max_interview_chars") or 4000))
        ),
        "max_internal_chars": min(
            3000, max(1, int(raw.get("max_internal_chars") or 3000))
        ),
        "entries": entries,
    }


def _clean_positions(raw: Any) -> list[dict[str, Any]]:
    """Normalize + validate the positions list.

    Each position = name + match_keywords + business_questions (prompt plus optional
    per-question competency) + core_competencies (a free-text paragraph, kept last).
    Empty cards are dropped; a card with any content must name its position.
    """
    if not isinstance(raw, list):
        return []
    positions: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        keywords = [
            str(k).strip()
            for k in item.get("match_keywords") or []
            if str(k).strip()
        ]
        questions: list[str | dict[str, str]] = []
        for q in item.get("business_questions") or []:
            text = str((q.get("prompt") if isinstance(q, dict) else q) or "").strip()
            if not text:
                continue
            competency = str(q.get("competency") or "").strip() if isinstance(q, dict) else ""
            questions.append(
                {"prompt": text, "competency": competency} if competency else text
            )
        core_competencies = str(item.get("core_competencies") or "").strip()
        if not (name or keywords or questions or core_competencies):
            continue
        if not name:
            raise ValueError(f"第 {index} 个岗位缺少名称")
        position: dict[str, Any] = {"name": name}
        if keywords:
            position["match_keywords"] = keywords
        if questions:
            position["business_questions"] = questions
        if core_competencies:
            position["core_competencies"] = core_competencies  # paragraph, kept last
        positions.append(position)
    return positions


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
