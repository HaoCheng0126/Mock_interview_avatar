"""Tests for hub.interview_config — interview.yaml read/split/validate/write."""

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
rubric:
  dimensions: [technical_depth]
question_sets:
  - id: q1
    title: "问题一"
    prompt: "请介绍你自己。"
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
    assert data["speech"]["skip_transition"] == prompt_defaults.DEFAULT_SKIP_TRANSITION
    questions = yaml.safe_load(data["questions_yaml"])
    assert questions["question_sets"][0]["id"] == "q1"
    assert questions["rubric"]["dimensions"] == ["technical_depth"]


def test_save_roundtrip_preserves_form_and_questions(config_path):
    data = read_config(config_path)
    data["interviewer"]["style"] = "温和鼓励"
    data["speech"]["skip_transition"] = "换一题。"
    data["workflow"]["hard_timeout_seconds"] = 60
    save_config(data, config_path)

    reloaded = read_config(config_path)
    assert reloaded["interviewer"]["style"] == "温和鼓励"
    assert reloaded["speech"]["skip_transition"] == "换一题。"
    assert reloaded["workflow"]["hard_timeout_seconds"] == 60
    # untouched defaults still resolve
    assert reloaded["prompts"]["report"] == prompt_defaults.DEFAULT_REPORT_PROMPT


def test_save_strips_values_equal_to_defaults(config_path):
    data = read_config(config_path)
    data["speech"]["skip_transition"] = "换一题。"
    save_config(data, config_path)
    stored = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert stored["speech"] == {"skip_transition": "换一题。"}
    assert "prompts" not in stored
    assert "workflow" not in stored


def test_save_rejects_bad_questions_yaml(config_path):
    data = read_config(config_path)
    data["questions_yaml"] = "question_sets: {not: list}"
    with pytest.raises(ValueError, match="question_sets"):
        save_config(data, config_path)

    data["questions_yaml"] = "question_sets:\n  - id: q1\n    title: ''\n    prompt: 'x'"
    with pytest.raises(ValueError, match="title"):
        save_config(data, config_path)

    data["questions_yaml"] = "question_sets: [\n"
    with pytest.raises(ValueError, match="语法错误"):
        save_config(data, config_path)


def test_save_rejects_non_numeric_workflow(config_path):
    data = read_config(config_path)
    data["workflow"]["hard_timeout_seconds"] = "abc"
    with pytest.raises(ValueError, match="数字"):
        save_config(data, config_path)


def test_read_includes_knowledge_and_prompt_defaults(config_path):
    data = read_config(config_path)
    assert data["knowledge"]["entries"] == []
    assert data["knowledge"]["max_chars"] == prompt_defaults.DEFAULT_KNOWLEDGE_MAX_CHARS
    assert data["defaults"]["prompts"]["system"] == prompt_defaults.DEFAULT_SYSTEM_PROMPT


def test_save_knowledge_roundtrip_and_strip(config_path):
    data = read_config(config_path)
    data["knowledge"]["entries"] = [
        {"title": "岗位 JD", "content": "负责后端服务", "enabled": True},
        {"title": "", "content": "", "enabled": True},  # empty → dropped
    ]
    save_config(data, config_path)
    reloaded = read_config(config_path)
    assert reloaded["knowledge"]["entries"] == [
        {"title": "岗位 JD", "content": "负责后端服务", "enabled": True}
    ]
    # removing all entries strips the section entirely
    reloaded["knowledge"]["entries"] = []
    save_config(reloaded, config_path)
    stored = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "knowledge" not in stored


def test_preview_renders_persona_and_knowledge(config_path):
    data = read_config(config_path)
    data["interviewer"]["style"] = "温和鼓励"
    data["knowledge"]["entries"] = [
        {"title": "岗位 JD", "content": "负责后端服务", "enabled": True}
    ]
    preview = build_preview(data)
    assert "温和鼓励" in preview["system_prompt"]
    assert "负责后端服务" in preview["system_prompt"]
    assert preview["opening_text"].startswith("你好，我是测试面试官。")


def test_preview_rejects_invalid_config(config_path):
    data = read_config(config_path)
    data["questions_yaml"] = "question_sets: []"
    with pytest.raises(ValueError):
        build_preview(data)


def test_save_never_writes_invalid_file(config_path):
    original = config_path.read_text(encoding="utf-8")
    data = read_config(config_path)
    data["questions_yaml"] = "rubric: {}\nquestion_sets: []"
    with pytest.raises(ValueError):
        save_config(data, config_path)
    assert config_path.read_text(encoding="utf-8") == original
