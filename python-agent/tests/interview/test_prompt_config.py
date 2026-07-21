"""Tests for configurable prompts, speech phrases, and workflow parameters."""

from pathlib import Path

from interview import prompts as prompt_defaults
from interview.answer_evaluator import AnswerEvaluator
from interview.controller import InterviewController
from interview.follow_up_decider import FollowUpDecider
from interview.interview_manager import InterviewManager
from interview.models import QuestionSpec, SpeechConfig
from interview.prompts import (
    DEFAULT_REPORT_PROMPT_MODULES,
    build_report_prompt_from_modules,
    render_template,
)
from interview.report_generator import ReportGenerator


MINIMAL_YAML = """
interview:
  title: "测试面试"
  duration_minutes: 15
interviewer:
  name: "测试面试官"
  style: "犀利直接"
  rules:
    - "只问技术"
    - "不闲聊"
candidate:
  target_role: "测试工程师"
  background: "五年测试经验"
question_sets:
  - id: q1
    title: "问题一"
    prompt: "请介绍你自己。"
"""

CUSTOM_SECTIONS_YAML = MINIMAL_YAML + """
prompts:
  system: "系统模板：{interviewer_name}/{target_role}"
  evaluator: "评估模板：{question}|{answer}|{interviewer_style}"
  follow_up_decider: "追问模板：{payload}"
  report: "报告模板：{rubric_dimensions}|{termination_reason}"
speech:
  opening_template: "开场：{interviewer_name} 面 {target_role}，共 {duration_minutes} 分钟"
  answer_acknowledgements: ["收到一"]
  final_answer_acknowledgements: ["收到最后"]
  follow_up_prefixes: ["追问前缀。"]
  first_question_transition: "第一题来了。"
  next_question_transition: "下一题。"
  skip_transition: "跳过啦。"
  closing: "面试结束语。"
  termination: "提前终止语。"
  thinking_checks:
    - after_seconds: 10
      text: "还在想吗？"
workflow:
  hard_timeout_seconds: 30
  max_skipped_questions: 5
  max_consecutive_skipped_questions: 4
"""


class FakeLlm:
    def __init__(self, text: str = "{}") -> None:
        self.text = text
        self.prompt = ""

    async def generate(self, prompt: str, max_tokens: int = 512) -> str:
        self.prompt = prompt
        return self.text

    async def generate_once(self, prompt: str, max_tokens: int = 512) -> str:
        self.prompt = prompt
        return self.text


def _manager(tmp_path: Path, content: str) -> InterviewManager:
    path = tmp_path / "interview.yaml"
    path.write_text(content, encoding="utf-8")
    return InterviewManager(path)


# ---------------------------------------------------------------------------
# render_template
# ---------------------------------------------------------------------------


def test_render_template_substitutes_and_keeps_unknown():
    out = render_template("你好{name}，未知{other}", {"name": "小林"})
    assert out == "你好小林，未知{other}"


def test_render_template_safe_with_literal_json_braces():
    template = '输出 {"score": 1} 格式，问题：{question}'
    out = render_template(template, {"question": "Q1"})
    assert out == '输出 {"score": 1} 格式，问题：Q1'


def test_build_report_prompt_from_modules_preserves_module_order():
    prompt = build_report_prompt_from_modules(
        {
            "role_and_style": "A",
            "cover_and_summary": "B",
            "output_contract": "C",
        }
    )
    assert prompt == "A\n\nB\n\nC"
    assert DEFAULT_REPORT_PROMPT_MODULES["dimension_commentary"]


# ---------------------------------------------------------------------------
# manager: defaults & overrides
# ---------------------------------------------------------------------------


def test_defaults_match_legacy_behavior(tmp_path):
    manager = _manager(tmp_path, MINIMAL_YAML)
    opening = manager.build_opening_text()
    # admin preview 模式下 target_role / candidate_background 保持为占位符
    assert "你好，我是测试面试官。" in opening
    assert "你和{target_role}这个方向的匹配度" in opening
    assert "大概会占用你 15 分钟" in opening
    cfg = manager.config
    assert cfg.prompts.evaluator == prompt_defaults.DEFAULT_EVALUATOR_PROMPT
    assert cfg.workflow.hard_timeout_seconds == 75.0
    assert [c.after_seconds for c in cfg.speech.thinking_checks] == [20.0, 45.0]


def test_persona_context_exposes_style_rules_background(tmp_path):
    manager = _manager(tmp_path, MINIMAL_YAML)
    ctx = manager.persona_context()
    assert ctx["interviewer_name"] == "测试面试官"
    assert ctx["interviewer_style"] == "犀利直接"
    assert ctx["interviewer_rules"] == "只问技术；不闲聊"
    # admin preview 下 candidate_background 仍是占位符
    assert ctx["candidate_background"] == "{candidate_background}"


def test_system_prompt_injects_persona(tmp_path):
    manager = _manager(tmp_path, MINIMAL_YAML)
    system = manager.build_system_prompt()
    assert "测试面试官" in system
    assert "犀利直接" in system
    assert "只问技术；不闲聊" in system
    # admin preview 下 candidate_background 保持占位符
    assert "{candidate_background}" in system


def test_default_task_prompts_explicitly_inherit_interviewer_persona():
    assert "{interviewer_name}" in prompt_defaults.DEFAULT_PLANNER_PROMPT
    assert "{interviewer_style}" in prompt_defaults.DEFAULT_PLANNER_PROMPT
    assert "{interviewer_rules}" in prompt_defaults.DEFAULT_PLANNER_PROMPT
    assert "{interviewer_name}" in prompt_defaults.DEFAULT_EVALUATOR_PROMPT
    assert "{interviewer_style}" in prompt_defaults.DEFAULT_FOLLOW_UP_DECIDER_PROMPT
    assert "口头开场或收尾任务只输出自然语言" in prompt_defaults.DEFAULT_SYSTEM_PROMPT
    assert "请始终输出结构化 JSON" not in prompt_defaults.DEFAULT_SYSTEM_PROMPT


def test_custom_sections_override_everything(tmp_path):
    manager = _manager(tmp_path, CUSTOM_SECTIONS_YAML)
    cfg = manager.config
    # admin preview 下 target_role 是占位符
    assert manager.build_system_prompt() == "系统模板：测试面试官/{target_role}"
    assert manager.build_opening_text() == "开场：测试面试官 面 {target_role}，共 15 分钟"
    assert cfg.speech.skip_transition == "跳过啦。"
    assert cfg.speech.next_question_transitions == ["下一题。"]
    assert cfg.speech.answer_acknowledgements == ["收到一"]
    assert [(c.after_seconds, c.text) for c in cfg.speech.thinking_checks] == [
        (10.0, "还在想吗？")
    ]
    assert cfg.workflow.hard_timeout_seconds == 30.0
    assert cfg.workflow.max_skipped_questions == 5
    assert cfg.workflow.max_consecutive_skipped_questions == 4


# ---------------------------------------------------------------------------
# knowledge base
# ---------------------------------------------------------------------------

POSITIONS_YAML = MINIMAL_YAML + """
positions:
  - name: "后端岗位"
    match_keywords: [python, 后端]
    business_questions:
      - "请介绍你自己。"
    core_competencies: "重点考察后端服务的可靠性：重试、幂等、降级。"
"""


def test_knowledge_block_includes_position_and_competencies(tmp_path):
    manager = _manager(tmp_path, POSITIONS_YAML)
    block = manager.knowledge_block()
    assert "本场面试参考资料" in block
    # admin preview 下 position / core_competencies 都是占位符
    assert "【岗位】{position}" in block
    assert "{core_competencies}" in block


def test_system_prompt_includes_position_and_candidate_material(tmp_path):
    from interview.profile import CandidateProfile

    manager = _manager(tmp_path, POSITIONS_YAML)
    manager.apply_candidate_profile(
        # JD contains "后端" so it genuinely matches the bank position (post-fix, an
        # unrelated JD would NOT pull in the position).
        CandidateProfile(jd_text="本场 JD 内容：负责 Python 后端服务", resume_text="三年 FastAPI 经验")
    )
    system = manager.build_system_prompt()
    assert "重点考察后端服务的可靠性" in system  # persistent 核心考察点 paragraph
    assert "本场 JD 内容" in system  # ephemeral {jd}
    assert "三年 FastAPI 经验" in system  # ephemeral {resume}


def test_no_positions_or_profile_leaves_system_prompt_clean(tmp_path):
    manager = _manager(tmp_path, MINIMAL_YAML)
    system = manager.build_system_prompt()
    # admin preview 下 system prompt 末尾会附 runtime 上下文占位符块
    # （便于在控制台预览时展示 prompt 模板结构），但 runtime 不会泄露真实数据
    assert "【本场候选人 JD】" in system
    assert "【本场候选人简历】" in system
    assert "{jd}" in system
    assert "{resume}" in system
    # 真实 JD / 简历内容不会泄露到 admin preview
    assert "python" not in system.lower()
    assert not system.endswith("\n\n")


def test_reference_block_truncates_at_max_chars(tmp_path):
    from interview.profile import CandidateProfile

    manager = _manager(tmp_path, POSITIONS_YAML)
    manager.apply_candidate_profile(CandidateProfile(resume_text="长" * 7000))
    block = manager.knowledge_block()
    assert "（参考资料过长，已截断）" in block
    assert len(block) < prompt_defaults.DEFAULT_KNOWLEDGE_MAX_CHARS + 200


# ---------------------------------------------------------------------------
# components use custom templates + persona context
# ---------------------------------------------------------------------------


async def test_evaluator_uses_custom_template_and_context():
    llm = FakeLlm("not json")
    evaluator = AnswerEvaluator(
        llm,
        prompt_template="评估：{question}|{answer}|{interviewer_style}",
        context={"interviewer_style": "犀利直接"},
    )
    await evaluator.evaluate("Q1", "A1")
    assert llm.prompt.startswith("评估：Q1|A1|犀利直接")
    assert "followUpQuestion 必须结合当前问题" in llm.prompt


async def test_follow_up_decider_uses_custom_template():
    llm = FakeLlm('{"needed": false}')
    decider = FollowUpDecider(
        llm, prompt_template="追问判断（{interviewer_name}）：{payload}", context={"interviewer_name": "测试面试官"}
    )
    question = QuestionSpec(
        section_id="s1",
        section_title="第一部分",
        question_id="q1",
        prompt="请介绍你自己。",
    )
    await decider.decide_async(
        question=question, answer_text="回答", evaluation=None, transcript=[], probe_index=0
    )
    assert llm.prompt.startswith("追问判断（测试面试官）：")
    assert '"请介绍你自己。"' in llm.prompt


async def test_report_generator_uses_custom_template():
    llm = FakeLlm(
        '{"summary":"候选人能够说明项目推进过程。","overallScore":70,'
        '"strengths":[],"weaknesses":[],"recommendations":[],"dimensions":{'
        '"role_alignment":{"score":7,"evidence":[],"concerns":[],'
        '"recommendations":[],"confidence":"medium"}}}'
    )
    generator = ReportGenerator(
        llm, prompt_template="报告：{rubric_dimensions}|{termination_reason}", context={}
    )
    report = await generator.generate_async(
        [], transcript=[], rubric_dimensions=["depth"], termination_reason="user_stopped"
    )
    assert llm.prompt.startswith('报告：["depth"]|user_stopped')
    assert report.summary == "候选人能够说明项目推进过程。"


# ---------------------------------------------------------------------------
# controller speech/workflow overrides
# ---------------------------------------------------------------------------


def _speech() -> SpeechConfig:
    return SpeechConfig(
        answer_acknowledgements=["收到一"],
        final_answer_acknowledgements=["收到最后"],
        follow_up_prefixes=["追问前缀。"],
        first_question_transition="第一题来了。",
        next_question_transitions=["下一题甲。", "下一题乙。"],
        skip_transition="跳过啦。",
        closing="面试结束语。",
        termination="提前终止语。",
    )


def test_controller_uses_custom_speech_and_limits():
    controller = InterviewController(
        agent=None,
        manager=None,
        planner=None,
        evaluator=None,
        report_generator=None,
        max_skipped_questions=5,
        max_consecutive_skipped_questions=4,
        speech_config=_speech(),
    )
    assert controller._answer_acknowledgements == ("收到一",)
    assert controller._final_answer_acknowledgements == ("收到最后",)
    assert controller._skip_transition_text == "跳过啦。"
    assert controller._closing_text == "面试结束语。"
    assert controller._termination_text == "提前终止语。"
    assert set(controller._next_question_transitions) == {"下一题甲。", "下一题乙。"}
    assert controller._max_skipped_questions == 5
    assert controller._max_consecutive_skipped_questions == 4

    question = QuestionSpec(
        section_id="s1", section_title="第一部分", question_id="q1", prompt="请介绍你自己。"
    )
    controller._asked_question_ids.add("q1")
    first = controller._spoken_prompt_text(
        question=question, prompt_type="main_question", prompt_text=None, probe_index=0
    )
    assert first == "第一题来了。请介绍你自己。"
    follow_up = controller._spoken_prompt_text(
        question=question, prompt_type="follow_up", prompt_text="展开讲讲？", probe_index=1
    )
    assert follow_up == "追问前缀。展开讲讲？"


def test_controller_defaults_unchanged_without_speech_config():
    controller = InterviewController(
        agent=None, manager=None, planner=None, evaluator=None, report_generator=None
    )
    assert controller._answer_acknowledgements == InterviewController._ANSWER_ACKNOWLEDGEMENTS
    assert controller._skip_transition_text == "没关系，这个问题我们先跳过。"
    assert controller._max_skipped_questions == 3
    assert controller._max_consecutive_skipped_questions == 2
