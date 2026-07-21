"""Tests for hub.interview_config — interview.yaml read/split/validate/write (positions)."""

import pytest
import yaml

from hub.interview_config import build_preview, read_config, save_config
from interview import prompts as prompt_defaults


BASE_YAML = """
interview:
  title: "测试面试"
  duration_minutes: 15
interviewer:
  name: "测试面试官"
  style: "犀利"
  rules: ["只问技术"]
candidate:
  target_role: "测试工程师"
positions:
  - name: "后端岗位"
    match_keywords: [python, 后端]
    business_questions:
      - "请介绍你自己。"
    core_competencies: "重点考察后端服务的可靠性：重试、幂等、降级。"
"""


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "interview.yaml"
    path.write_text(BASE_YAML, encoding="utf-8")
    return path


def test_read_fills_prompt_and_workflow_defaults(config_path):
    data = read_config(config_path)
    assert data["interviewer"]["name"] == "测试面试官"
    assert data["prompts"]["evaluator"] == prompt_defaults.DEFAULT_EVALUATOR_PROMPT
    assert data["workflow"]["hard_timeout_seconds"] == 75.0
    assert data["workflow"]["foreground_evaluation_timeout_seconds"] == 5.0


def test_read_displays_effective_default_transitions_for_stored_empty_list(config_path):
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    raw.setdefault("speech", {})["next_question_transitions"] = []
    config_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    data = read_config(config_path)

    assert data["speech"]["next_question_transitions"] == (
        prompt_defaults.DEFAULT_NEXT_QUESTION_TRANSITIONS
    )
    assert data["speech"]["skip_transition"] == prompt_defaults.DEFAULT_SKIP_TRANSITION


def test_read_returns_positions_not_legacy_blocks(config_path):
    data = read_config(config_path)
    assert "questions_yaml" not in data
    assert "knowledge" not in data
    positions = data["positions"]
    assert len(positions) == 1
    assert positions[0]["name"] == "后端岗位"
    assert positions[0]["match_keywords"] == ["python", "后端"]
    assert positions[0]["core_competencies"] == "重点考察后端服务的可靠性：重试、幂等、降级。"
    assert positions[0]["business_questions"] == ["请介绍你自己。"]


def test_save_roundtrip_preserves_form_and_positions(config_path):
    data = read_config(config_path)
    data["interviewer"]["style"] = "温和鼓励"
    data["speech"]["skip_transition"] = "换一题。"
    data["workflow"]["hard_timeout_seconds"] = 60
    data["positions"][0]["business_questions"].append("如何做缓存一致性？")
    save_config(data, config_path)

    reloaded = read_config(config_path)
    assert reloaded["interviewer"]["style"] == "温和鼓励"
    assert reloaded["speech"]["skip_transition"] == "换一题。"
    assert reloaded["workflow"]["hard_timeout_seconds"] == 60
    assert len(reloaded["positions"][0]["business_questions"]) == 2
    assert reloaded["prompts"]["report"] == prompt_defaults.DEFAULT_REPORT_PROMPT


def test_structured_question_competency_roundtrip(config_path):
    data = read_config(config_path)
    data["positions"][0]["business_questions"] = [
        {"prompt": "如何设计等待体验？", "competency": "时间维度与情感化设计"}
    ]

    save_config(data, config_path)

    stored = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert stored["positions"][0]["business_questions"] == [
        {"prompt": "如何设计等待体验？", "competency": "时间维度与情感化设计"}
    ]


def test_save_strips_form_defaults_but_keeps_positions(config_path):
    data = read_config(config_path)
    data["speech"]["skip_transition"] = "换一题。"
    save_config(data, config_path)
    stored = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert stored["speech"] == {"skip_transition": "换一题。"}
    assert "prompts" not in stored
    assert "workflow" not in stored
    assert stored["positions"][0]["name"] == "后端岗位"


def test_save_cleans_empty_rows_and_cards(config_path):
    data = read_config(config_path)
    data["positions"] = [
        {
            "name": "岗位A",
            "match_keywords": ["x", "", "  "],
            "business_questions": ["题1", "", "  "],
            "core_competencies": "  这个岗位重点考察可靠性  ",
        },
        {"name": "", "business_questions": [], "core_competencies": ""},  # empty → dropped
    ]
    save_config(data, config_path)
    stored = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    positions = stored["positions"]
    assert len(positions) == 1
    assert positions[0]["match_keywords"] == ["x"]
    assert positions[0]["business_questions"] == ["题1"]  # empty rows dropped
    assert positions[0]["core_competencies"] == "这个岗位重点考察可靠性"  # trimmed


def test_position_with_content_but_no_name_raises(config_path):
    data = read_config(config_path)
    data["positions"] = [{"name": "", "business_questions": ["题干"]}]
    with pytest.raises(ValueError, match="名称"):
        save_config(data, config_path)


def test_save_rejects_non_numeric_workflow(config_path):
    data = read_config(config_path)
    data["workflow"]["hard_timeout_seconds"] = "abc"
    with pytest.raises(ValueError, match="数字"):
        save_config(data, config_path)


def test_read_includes_prompt_defaults(config_path):
    data = read_config(config_path)
    assert data["defaults"]["prompts"]["system"] == prompt_defaults.DEFAULT_SYSTEM_PROMPT


def test_preview_renders_persona_and_position(config_path):
    data = read_config(config_path)
    data["interviewer"]["style"] = "温和鼓励"
    preview = build_preview(data)
    assert "温和鼓励" in preview["system_prompt"]
    assert "后端岗位" in preview["system_prompt"]       # position name in reference block
    assert "重点考察后端服务的可靠性" in preview["system_prompt"]  # 核心考察点 paragraph
    assert preview["opening_text"].startswith("你好，我是测试面试官。")


def test_save_never_writes_invalid_file(config_path):
    original = config_path.read_text(encoding="utf-8")
    data = read_config(config_path)
    # a position with content but no name is invalid
    data["positions"] = [{"name": "", "business_questions": ["有内容但没名字"]}]
    with pytest.raises(ValueError):
        save_config(data, config_path)
    assert config_path.read_text(encoding="utf-8") == original


def test_allows_empty_positions(config_path):
    # A fully-dynamic config (no positions) is valid — the planner generates from JD.
    data = read_config(config_path)
    data["positions"] = []
    save_config(data, config_path)
    stored = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "positions" not in stored
    assert build_preview(data)["system_prompt"]


def test_read_fills_plan_and_planner_defaults(config_path):
    data = read_config(config_path)
    assert data["plan"]["resume_experiences"] == prompt_defaults.DEFAULT_RESUME_EXPERIENCES
    assert data["plan"]["business_questions"] == prompt_defaults.DEFAULT_BUSINESS_QUESTIONS
    assert data["plan"]["business_followups"] == prompt_defaults.DEFAULT_BUSINESS_FOLLOWUPS
    assert data["prompts"]["planner"] == prompt_defaults.DEFAULT_PLANNER_PROMPT
    assert data["defaults"]["prompts"]["planner"] == prompt_defaults.DEFAULT_PLANNER_PROMPT
    assert data["prompts"]["closing_comment"] == prompt_defaults.DEFAULT_CLOSING_COMMENT_PROMPT
    assert (
        data["defaults"]["prompts"]["closing_comment"]
        == prompt_defaults.DEFAULT_CLOSING_COMMENT_PROMPT
    )


def test_save_plan_roundtrip_and_strips_defaults(config_path):
    data = read_config(config_path)
    data["plan"]["resume_experiences"] = 3   # non-default → persisted
    data["plan"]["business_followups"] = 0   # non-default → persisted
    save_config(data, config_path)

    stored = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert stored["plan"] == {"resume_experiences": 3, "business_followups": 0}

    reloaded = read_config(config_path)
    assert reloaded["plan"]["resume_experiences"] == 3
    assert reloaded["plan"]["business_followups"] == 0
    assert reloaded["plan"]["business_questions"] == prompt_defaults.DEFAULT_BUSINESS_QUESTIONS


def test_save_strips_all_default_plan(config_path):
    data = read_config(config_path)
    save_config(data, config_path)  # plan untouched = all defaults
    stored = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "plan" not in stored
