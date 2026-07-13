from interview.answer_evaluator import AnswerEvaluator


class FakeLlm:
    def __init__(self, text):
        self.text = text

    async def generate(self, prompt, max_tokens=512):
        self.prompt = prompt
        return self.text


async def test_evaluator_parses_json_response():
    evaluator = AnswerEvaluator(
        FakeLlm(
            """
{
  "score": 4,
  "dimensions": {"depth": 4},
  "strengths": ["结构清楚"],
  "weaknesses": ["缺少数据"],
  "followUpNeeded": true,
  "followUpQuestion": "具体指标是什么？"
}
"""
        )
    )

    result = await evaluator.evaluate("项目问题", "我做了订单系统")

    assert result.score == 4
    assert result.dimensions["depth"] == 4
    assert result.follow_up_needed is True
    assert result.follow_up_question == "具体指标是什么？"


async def test_evaluator_uses_transcript_and_dimension_assessments():
    llm = FakeLlm(
        """
{
  "score": 4,
  "dimensions": {"technical_depth": 4},
  "dimensionAssessments": {
    "technical_depth": {
      "score": 4,
      "evidence": ["说明了缓存和限流"],
      "concerns": ["没有提到降级"],
      "recommendations": ["继续追问故障处理"],
      "confidence": "medium"
    }
  },
  "followUpNeeded": false
}
"""
    )
    evaluator = AnswerEvaluator(llm)

    result = await evaluator.evaluate(
        "可靠性问题",
        "我用了缓存和限流",
        transcript=[{"role": "candidate", "text": "我用了缓存和限流"}],
        rubric_dimensions=["technical_depth", "reliability_awareness"],
    )

    assert "完整对话记录" in llm.prompt
    assert "technical_depth" in llm.prompt
    assert result.dimension_assessments["technical_depth"].score == 4
    assert result.dimension_assessments["technical_depth"].evidence == ["说明了缓存和限流"]


async def test_evaluator_falls_back_for_invalid_json():
    evaluator = AnswerEvaluator(FakeLlm("not json"))

    result = await evaluator.evaluate("项目问题", "简短回答")

    assert result.score == 2
    assert result.follow_up_needed is True
    assert "再具体" in result.follow_up_question
