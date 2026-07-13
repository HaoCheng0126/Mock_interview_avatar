from interview.models import Evaluation, Exchange, TranscriptTurn
from interview.report_generator import ReportGenerator


class FakeLlm:
    def __init__(self, text):
        self.text = text
        self.prompt = ""

    async def generate(self, prompt, max_tokens=1024):
        self.prompt = prompt
        return self.text


def test_report_summarizes_exchanges():
    exchange = Exchange(
        exchange_id="ex_001",
        question_id="q1",
        section_id="project",
        type="main_question",
        prompt_id="prompt_001",
        prompt_text="项目问题",
        prompt_type="main_question",
        answer_text="我做了订单系统",
        evaluation=Evaluation(
            score=4,
            dimensions={"depth": 4},
            strengths=["结构清楚"],
            weaknesses=["缺少数据"],
        ),
    )

    report = ReportGenerator().generate([exchange])

    assert report.overall_score == 4
    assert "结构清楚" in report.strengths
    assert "缺少数据" in report.weaknesses
    assert report.exchanges == [exchange]


def test_report_uses_transcript_and_rubric_dimensions():
    transcript = [
        TranscriptTurn(
            turn_id="turn_001",
            interview_id="iv_test",
            role="interviewer",
            type="main_question",
            text="请介绍项目。",
            question_id="q1",
            exchange_id="ex_001",
        ),
        TranscriptTurn(
            turn_id="turn_002",
            interview_id="iv_test",
            role="candidate",
            type="answer",
            text="我负责订单系统，把接口 P95 从 800ms 降到 200ms。",
            question_id="q1",
            exchange_id="ex_001",
        ),
        TranscriptTurn(
            turn_id="turn_003",
            interview_id="iv_test",
            role="system",
            type="question_skipped",
            text="hard_timeout_no_answer",
            question_id="q2",
            exchange_id="ex_002",
        ),
    ]

    report = ReportGenerator().generate(
        [],
        transcript=transcript,
        rubric_dimensions=["technical_depth", "communication_clarity"],
    )

    assert report.summary == "本次面试记录 3 条对话，其中候选人有效回答 1 条。"
    assert "technical_depth" in report.dimension_scores
    assert report.dimension_scores["technical_depth"].evidence
    assert report.dimension_scores["communication_clarity"].confidence in {
        "low",
        "medium",
        "high",
    }


async def test_report_generator_uses_llm_for_transcript_level_report():
    llm = FakeLlm(
        """
{
  "summary": "候选人能说明性能优化过程。",
  "overallScore": 4,
  "strengths": ["有量化结果"],
  "weaknesses": ["可靠性展开不足"],
  "recommendations": ["继续追问故障处理"],
  "dimensions": {
    "technical_depth": {
      "score": 4,
      "evidence": ["P95 从 800ms 降到 200ms"],
      "concerns": [],
      "recommendations": ["补充架构取舍"],
      "confidence": "high"
    }
  }
}
"""
    )
    transcript = [
        TranscriptTurn(
            "turn_001",
            "iv_test",
            "candidate",
            "answer",
            "我负责订单系统，把接口 P95 从 800ms 降到 200ms。",
        )
    ]

    report = await ReportGenerator(llm).generate_async(
        [],
        transcript=transcript,
        rubric_dimensions=["technical_depth"],
    )

    assert "完整面试对话记录" in llm.prompt
    assert report.summary == "候选人能说明性能优化过程。"
    assert report.overall_score == 4
    assert report.dimension_scores["technical_depth"].evidence == [
        "P95 从 800ms 降到 200ms"
    ]
