from interview.follow_up_decider import FollowUpDecider
from interview.follow_up_planner import FollowUpPlanner
from interview.models import Evaluation, QuestionSpec, TranscriptTurn


class FakeLlm:
    def __init__(self, text):
        self.text = text
        self.prompt = ""

    async def generate(self, prompt, max_tokens=512):
        self.prompt = prompt
        return self.text


def test_follow_up_decider_requests_probe_for_weak_answer():
    question = QuestionSpec(
        "backend",
        "后端",
        "q_backend_001",
        "请说明你如何处理可靠性问题。",
        competency="reliability_awareness",
        expected_signals=["降级", "重试", "监控"],
    )
    evaluation = Evaluation(
        score=2,
        dimensions={"reliability_awareness": 2},
        weaknesses=["没有说明监控和故障处理"],
    )

    decision = FollowUpDecider().decide(
        question=question,
        answer_text="我一般会多打日志。",
        evaluation=evaluation,
        transcript=[],
        probe_index=0,
    )

    assert decision.needed is True
    assert decision.follow_up_type == "deepen"
    assert "可靠性" in decision.reason or "reliability" in decision.reason
    assert decision.missing_signal


def test_follow_up_decider_skips_when_answer_is_strong():
    question = QuestionSpec(
        "project",
        "项目",
        "q_project_001",
        "请介绍项目。",
        expected_signals=["背景", "行动", "结果"],
    )
    evaluation = Evaluation(
        score=4,
        dimensions={"technical_depth": 4},
        strengths=["有背景、行动和量化结果"],
    )

    decision = FollowUpDecider().decide(
        question=question,
        answer_text="我说明了背景、行动和结果。",
        evaluation=evaluation,
        transcript=[
            TranscriptTurn(
                "turn_001",
                "iv_test",
                "candidate",
                "answer",
                "我说明了背景、行动和结果。",
            )
        ],
        probe_index=0,
    )

    assert decision.needed is False
    assert decision.follow_up_type == "skip"


def test_follow_up_planner_generates_question_from_decision():
    question = QuestionSpec(
        "backend",
        "后端",
        "q_backend_001",
        "请说明你如何处理可靠性问题。",
    )
    decision = FollowUpDecider().decide(
        question=question,
        answer_text="我一般会多打日志。",
        evaluation=Evaluation(
            score=2,
            dimensions={},
            weaknesses=["没有具体方案"],
        ),
        transcript=[],
        probe_index=0,
    )

    follow_up = FollowUpPlanner().plan(question=question, decision=decision)

    assert follow_up
    assert follow_up.endswith("？")


async def test_follow_up_decider_uses_llm_when_available():
    llm = FakeLlm(
        """
{
  "needed": true,
  "reason": "缺少故障恢复细节",
  "missingSignal": "恢复措施",
  "followUpType": "evidence",
  "suggestedQuestion": "你当时具体采取了哪些恢复措施？"
}
"""
    )
    question = QuestionSpec(
        "incident",
        "故障排查",
        "q_incident_001",
        "线上接口变慢如何处理？",
        competency="problem_solving",
    )

    decision = await FollowUpDecider(llm).decide_async(
        question=question,
        answer_text="我会看日志。",
        evaluation=Evaluation(score=3, dimensions={}),
        transcript=[],
        probe_index=0,
    )

    assert "完整对话记录" in llm.prompt
    assert decision.needed is True
    assert decision.follow_up_type == "evidence"
    assert decision.suggested_question == "你当时具体采取了哪些恢复措施？"
