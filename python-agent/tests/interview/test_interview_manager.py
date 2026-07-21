import json
from pathlib import Path

import pytest

from interview.interview_manager import InterviewManager


def test_loads_default_interview_config():
    manager = InterviewManager(Path("config/interview.yaml"))

    assert manager.config.title  # user-editable via the console; just ensure it loads
    assert manager.config.lang == "zh"
    # 新规则：单题最多追问 1 次
    assert manager.config.max_probe_per_question == 1
    assert manager.config.interviewer.name == "陈珊"
    # positions are the source of truth; questions/rubric are derived from them
    assert [p.name for p in manager.config.positions] == ["Python 后端工程师"]
    assert manager.get_question_specs()[0].section_id == "business"
    assert manager.get_question_specs()[0].question_id == "biz_001"
    assert len(manager.get_question_specs()) >= 8
    # business questions are just prompts now; 核心考察点 is a free-text paragraph
    pos = manager.config.positions[0]
    assert isinstance(pos.core_competencies, str) and "技术深度" in pos.core_competencies
    assert pos.business_questions[0].startswith("请介绍")
    assert manager.config.rubric_dimensions == []


def test_structured_business_question_keeps_assessment_point(tmp_path):
    path = tmp_path / "structured-bank.yaml"
    path.write_text(
        """
interview:
  title: UX
interviewer:
  name: 路飞
positions:
  - name: UI/UX设计师
    match_keywords: [UI/UX]
    business_questions:
      - prompt: 如何设计 AI 内容的等待体验？
        competency: 时间维度的交互设计、情感化设计
""",
        encoding="utf-8",
    )

    question = InterviewManager(path).get_question_specs()[0]

    assert question.prompt == "如何设计 AI 内容的等待体验？"
    assert question.competency == "时间维度的交互设计、情感化设计"


def test_luffy_uses_uiux_role_and_imported_question_bank():
    manager = InterviewManager(Path("config/avatars/avatar-ix4hn.yaml"))

    position = manager.config.positions[0]
    questions = manager.get_question_specs()
    assert position.name == "UI/UX设计师"
    assert len(questions) >= 25
    assert any("移动端、网页或后台系统" in question.prompt for question in questions)
    assert any("组件库" in question.prompt for question in questions)
    assert not any("Vibe Coding" in question.prompt for question in questions)
    assert not any("Unity" in question.prompt for question in questions)
    assert not any("游戏 UI/UX" in question.prompt for question in questions)
    assert not any("电商" in question.prompt for question in questions)
    assert all(question.competency for question in questions)
    assert "资深 UI/UX 设计负责人" in manager.config.interviewer.style
    assert manager.config.plan.resume_experiences == 2
    assert manager.config.plan.business_questions == 3


def test_luffy_roster_locks_the_prescribed_role_and_full_jd():
    roster = json.loads(Path("config/roster.json").read_text(encoding="utf-8"))
    luffy = next(item for item in roster["avatars"] if item["slug"] == "avatar-ix4hn")

    assert luffy["default_role"] == "UI/UX设计师"
    assert luffy["profile_locked"] is True
    assert "产品整体界面视觉设计" in luffy["default_jd"]
    assert "移动端 APP、网页、后台管理系统" in luffy["default_jd"]
    assert "Figma/Sketch/PS/AE" in luffy["default_jd"]
    assert "投递请附作品集" in luffy["default_jd"]


def test_enterprise_interviewer_is_role_bound_and_evidence_based():
    manager = InterviewManager(Path("config/avatars/avatar-i62mt.yaml"))
    combined_rules = "；".join(manager.config.interviewer.rules)
    position = manager.config.positions[0]

    assert "企业招聘负责人" in manager.config.interviewer.style
    assert "后台配置的岗位" in combined_rules
    assert "受保护" in combined_rules
    assert "同性恋" not in position.core_competencies
    assert "中性核验" in position.core_competencies
    assert len(position.business_questions) >= 6


def test_build_opening_text_mentions_role_and_duration():
    manager = InterviewManager(Path("config/interview.yaml"))

    text = manager.build_opening_text()

    # admin preview 下 target_role 保持为占位符
    assert "{target_role}" in text
    assert f"{manager.config.duration_minutes} 分钟" in text
    assert "先简单聊聊" in text
    assert "模拟面试" not in text
    assert "准备好了" not in text


def test_allows_config_without_question_sets(tmp_path):
    # An empty bank is allowed now — bank-less positions get business questions
    # generated from the JD by the session-start planner.
    path = tmp_path / "no_bank.yaml"
    path.write_text(
        """
interview:
  title: "No bank"
interviewer:
  name: "面试官"
candidate:
  target_role: "Backend"
question_sets: []
""",
        encoding="utf-8",
    )

    manager = InterviewManager(path)
    assert manager.get_question_specs() == []


def _minimal_yaml(plan_block: str = "") -> str:
    return (
        "interview:\n  title: x\n"
        "interviewer:\n  name: n\n"
        "candidate:\n  target_role: r\n"
        "question_sets: []\n" + plan_block
    )


def test_loads_plan_config_defaults(tmp_path):
    path = tmp_path / "iv.yaml"
    path.write_text(_minimal_yaml(), encoding="utf-8")

    plan = InterviewManager(path).config.plan
    assert plan.resume_experiences == 2
    assert plan.business_questions == 3
    # 新规则：每题最多追问 1 次
    assert plan.resume_followups == 1
    assert plan.business_followups == 1
    # 自我介绍不追问
    assert plan.self_intro_followups == 0
    assert plan.self_intro_followups_no_resume == 0


def test_parses_custom_plan_config(tmp_path):
    path = tmp_path / "iv.yaml"
    path.write_text(
        _minimal_yaml(
            "plan:\n"
            "  resume_experiences: 3\n"
            "  business_questions: 5\n"
            "  resume_followups: 1\n"
            "  business_followups: 0\n"
            "  self_intro_followups: 2\n"
            "  self_intro_followups_no_resume: 4\n"
        ),
        encoding="utf-8",
    )

    plan = InterviewManager(path).config.plan
    assert plan.resume_experiences == 3
    assert plan.business_questions == 5
    assert plan.resume_followups == 1
    assert plan.business_followups == 0
    assert plan.self_intro_followups == 2
    assert plan.self_intro_followups_no_resume == 4


def test_plan_config_clamps_negatives_and_falls_back_on_bad_values(tmp_path):
    path = tmp_path / "iv.yaml"
    path.write_text(
        _minimal_yaml(
            "plan:\n"
            "  resume_experiences: -1\n"
            "  business_questions: not_a_number\n"
        ),
        encoding="utf-8",
    )

    plan = InterviewManager(path).config.plan
    assert plan.resume_experiences == 0      # negative clamped to 0
    assert plan.business_questions == 3      # non-numeric → default
