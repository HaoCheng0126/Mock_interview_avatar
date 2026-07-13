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
    assert len(manager.get_question_specs()) >= 8
    competencies = {item.competency for item in manager.get_question_specs()}
    assert "technical_depth" in competencies
    assert "problem_solving" in competencies
    assert "role_fit" in competencies


def test_build_opening_text_mentions_role_and_duration():
    manager = InterviewManager(Path("config/interview.yaml"))

    text = manager.build_opening_text()

    assert "Python 后端工程师" in text
    assert "20 分钟" in text
    assert "先简单聊聊" in text
    assert "不用背答案" in text
    assert "模拟面试" not in text
    assert "准备好了" not in text


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
