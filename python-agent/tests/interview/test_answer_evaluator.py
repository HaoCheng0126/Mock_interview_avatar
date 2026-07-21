from interview.answer_evaluator import AnswerEvaluator


class FakeLlm:
    def __init__(self, text):
        self.text = text
        self.prompt = ""

    async def generate(self, prompt, max_tokens=512):
        self.prompt = prompt
        return self.text

    async def generate_once(self, prompt, max_tokens=512):
        self.prompt = prompt
        return self.text


class JsonLlm(FakeLlm):
    def __init__(self, text):
        super().__init__(text)
        self.json_calls = 0

    async def generate_json_once(self, prompt, max_tokens=512, temperature=0.2):
        self.prompt = prompt
        self.json_calls += 1
        return self.text


class SequenceJsonLlm:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    async def generate_json_once(self, prompt, max_tokens=512, temperature=0.2):
        self.prompts.append(prompt)
        return self.responses.pop(0)


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


async def test_evaluator_prefers_structured_json_and_specific_follow_up_contract():
    llm = JsonLlm(
        '{"score":3,"dimensions":{},"strengths":[],"weaknesses":[],'
        '"followUpNeeded":true,"followUpQuestion":"字节跳动项目中具体使用了什么指标？"}'
    )
    evaluator = AnswerEvaluator(llm)

    result = await evaluator.evaluate(
        "请介绍字节跳动增长项目？", "我负责增长策略和实验设计。"
    )

    assert llm.json_calls == 1
    assert "禁止输出‘再具体讲讲’" in llm.prompt
    assert result.follow_up_question == "字节跳动项目中具体使用了什么指标？"


async def test_low_information_answer_repairs_missing_follow_up_with_ai():
    llm = SequenceJsonLlm(
        [
            '{"score":1,"dimensions":{},"strengths":[],"weaknesses":[],'
            '"followUpNeeded":false,"followUpQuestion":""}',
            '{"score":1,"dimensions":{},"strengths":[],"weaknesses":[],'
            '"followUpNeeded":true,'
            '"followUpQuestion":"你刚才只回答了‘一起分析’，这些分析结果具体如何影响Agent的决策？"}',
        ]
    )
    evaluator = AnswerEvaluator(llm)

    result = await evaluator.evaluate(
        "智能购物助手如何与其他Agent协作？",
        "他就直接一起去分析了。",
    )

    assert len(llm.prompts) == 2
    assert "低信息回答" in llm.prompts[1]
    assert result.follow_up_needed is True
    assert "一起分析" in result.follow_up_question


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
    assert result.dimension_assessments["technical_depth"].score == 4
    assert result.dimension_assessments["technical_depth"].evidence == ["说明了缓存和限流"]


async def test_evaluator_falls_back_for_invalid_json():
    evaluator = AnswerEvaluator(FakeLlm("not json"))

    result = await evaluator.evaluate("项目问题", "简短回答")

    assert result.score == 2
    assert result.follow_up_needed is True
    assert "再具体" in result.follow_up_question
