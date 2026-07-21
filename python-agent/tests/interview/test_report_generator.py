import pytest
import json
import re

from interview.models import Evaluation, Exchange, TranscriptTurn
from interview.report_generator import ReportGenerator


class FakeLlm:
    def __init__(self, text):
        self.text = text
        self.prompt = ""
        self.prompts = []
        self.max_tokens_seen = []

    async def generate(self, prompt, max_tokens=1024):
        self.prompt = prompt
        return self.text

    async def generate_once(self, prompt, max_tokens=1024):
        self.prompt = prompt
        self.max_tokens = max_tokens
        self.prompts.append(prompt)
        self.max_tokens_seen.append(max_tokens)
        return self.text


class PipelineLlm:
    def __init__(self):
        self.prompts = []

    async def generate_json_once(self, prompt, max_tokens=1024, temperature=0.2):
        self.prompts.append(prompt)
        if "本次输入是经过清洗的问答单元" in prompt:
            payload_text = prompt.split("完整对话：", 1)[1].split(
                "\n\n本次输入是经过清洗的问答单元", 1
            )[0]
            items = json.loads(payload_text)
            return json.dumps(
                {
                    "chunkSummary": "本组回答包含明确的项目动作、判断和结果证据。",
                    "strengths": ["能够提供具体行动"],
                    "risks": ["部分取舍仍可展开"],
                    "dimensionEvidence": {
                        "project_execution": {"evidence": ["回答包含行动和结果"]}
                    },
                    "qaAnalyses": [
                        {
                            "exchangeId": item["exchangeId"],
                            "segmentIndex": item["segmentIndex"],
                            "questionIndex": item["order"],
                            "question": item["question"],
                            "answer": item["answer"][:200],
                            "strengths": ["有具体行动"],
                            "risks": ["可补充取舍"],
                            "commentary": "【面试官点评】回答有事实依据。\n\n【参考思路】补充目标、取舍和结果。",
                        }
                        for item in items
                    ],
                },
                ensure_ascii=False,
            )
        exchange_ids = list(dict.fromkeys(__import__("re").findall(r'ex_\d+', prompt)))
        return json.dumps(
            {
                "summary": "候选人能够围绕真实经历说明行动和结果，整体信息完整。",
                "overallScore": 78,
                "evidenceRefs": exchange_ids[:3] or ["ex_001"],
                "strengths": ["能够提供真实案例"],
                "weaknesses": ["部分判断依据仍可展开"],
                "recommendations": ["补充方案取舍和复盘数据"],
                "highlights": {
                    "alerts": ["注意补充判断依据"],
                    "advice": ["优先整理量化结果"],
                },
                "dimensions": {
                    "project_execution": {
                        "score": 8,
                        "evidence": ["分块分析包含行动和结果"],
                        "concerns": ["取舍信息有限"],
                        "recommendations": ["补充复盘"],
                        "confidence": "high",
                    }
                },
                "dimensionCommentaries": [
                    {
                        "key": "project_execution",
                        "title": "项目展现力",
                        "score": 8,
                        "commentary": "项目行动与结果较清楚。",
                    }
                ],
                "learningPlan": {
                    "tags": ["项目复盘"],
                    "phases": [
                        {"title": "立即行动", "window": "1周", "items": ["补充取舍"]},
                        {"title": "短期提升", "window": "1个月", "items": ["练习复盘"]},
                        {"title": "中期规划", "window": "3个月", "items": ["沉淀案例"]},
                    ],
                },
            },
            ensure_ascii=False,
        )


class RepairingOverviewLlm(PipelineLlm):
    def __init__(self):
        super().__init__()
        self.overview_calls = 0

    async def generate_json_once(self, prompt, max_tokens=1024, temperature=0.2):
        raw = await super().generate_json_once(prompt, max_tokens, temperature)
        if "已经由 AI 完成的分块分析摘要" not in prompt:
            return raw
        self.overview_calls += 1
        if self.overview_calls == 1:
            data = json.loads(raw)
            data["learningPlan"]["phases"] = data["learningPlan"]["phases"][:1]
            return json.dumps(data, ensure_ascii=False)
        return raw

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

    # 新规则：综合分 0~100，evaluation.score=4 自动换算为 80
    assert report.overall_score == 80
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
        rubric_dimensions=["outcome_orientation", "communication_clarity"],
    )

    # 新规则：summary 改为更真实、更人性化的面试官点评
    assert "只回答了" in report.summary and "跳过了" in report.summary
    assert "outcome_orientation" in report.dimension_scores
    assert report.dimension_scores["outcome_orientation"].evidence
    assert report.dimension_scores["communication_clarity"].confidence in {
        "low",
        "medium",
        "high",
    }


async def test_report_generator_uses_llm_for_transcript_level_report():
    llm = FakeLlm(
        """
{
  "cover": {
    "title": "产品经理模拟面试报告",
    "interviewType": "综合面试",
    "durationText": "45分钟",
    "generatedAt": "2026-07-16 19:18:23"
  },
  "summary": "候选人能说明性能优化过程。",
  "overallScore": 4,
  "evidenceRefs": ["turn_001"],
  "strengths": ["有量化结果"],
  "weaknesses": ["可靠性展开不足"],
  "recommendations": ["继续追问故障处理"],
  "highlights": {
    "alerts": ["可靠性展开不足"],
    "advice": ["继续追问故障处理"]
  },
  "dimensions": {
    "outcome_orientation": {
      "score": 4,
      "evidence": ["P95 从 800ms 降到 200ms"],
      "concerns": [],
      "recommendations": ["补充架构取舍"],
      "confidence": "high"
    }
  },
  "dimensionCommentaries": [
    {
      "key": "outcome_orientation",
      "title": "结果导向",
      "score": 4,
      "commentary": "能够说明性能优化过程。"
    }
  ],
  "learningPlan": {
    "tags": ["性能优化"],
    "phases": [
      { "title": "立即行动", "window": "1周", "items": ["继续补充架构取舍"] }
    ]
  },
  "qaAnalyses": [
    {
      "questionIndex": 1,
      "question": "请介绍项目。",
      "answer": "我负责订单系统，把接口 P95 从 800ms 降到 200ms。",
      "strengths": ["有量化结果"],
      "risks": ["可靠性展开不足"],
      "commentary": "回答不错，但可靠性展开不足。",
      "approach": ["补充架构取舍"],
      "referenceAnswer": "可以补充更多可靠性治理细节。"
    }
  ]
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

    report = await ReportGenerator(
        llm, context={"target_role": "产品经理"}
    ).generate_async(
        [],
        transcript=transcript,
        rubric_dimensions=["outcome_orientation"],
        actual_duration_seconds=503,
    )

    assert len(llm.prompts) == 2
    assert all("完整对话" in prompt for prompt in llm.prompts)
    assert 2400 <= max(llm.max_tokens_seen) <= 3000
    assert report.summary == "候选人能说明性能优化过程。"
    assert report.generation_source == "llm"
    # 新规则：综合分 0~100，LLM 输出 4 自动换算为 80
    assert report.overall_score == 80
    assert report.cover.title == "产品经理模拟面试报告"
    assert report.cover.interview_type == "综合面试"
    assert report.cover.duration_text == "8 分 23 秒"
    assert report.cover.generated_at != "2026-07-16 19:18:23"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", report.cover.generated_at)
    assert report.highlights.alerts == ["可靠性展开不足"]
    assert report.dimension_commentaries[0].commentary == "能够说明性能优化过程。"
    assert report.learning_plan.tags == ["性能优化"]
    assert report.qa_analyses[0].question == "请介绍项目。"
    assert report.dimension_scores["outcome_orientation"].evidence == [
        "P95 从 800ms 降到 200ms"
    ]


async def test_invalid_llm_report_is_marked_as_fallback():
    report = await ReportGenerator(FakeLlm('{"summary":"太短"}')).generate_async(
        [],
        transcript=[
            TranscriptTurn(
                "turn_001", "iv_test", "candidate", "answer", "我负责过订单系统。"
            )
        ],
    )
    assert report.generation_source == "fallback"
    assert report.summary


async def test_required_ai_overview_raises_instead_of_showing_local_fallback(monkeypatch):
    import interview.report_generator as report_module
    from interview.report_generator import ReportGenerationError

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(report_module, "REPORT_OVERVIEW_ATTEMPTS", 2)
    monkeypatch.setattr(report_module.asyncio, "sleep", no_wait)
    generator = ReportGenerator(
        FakeLlm('{"summary":"太短"}'), require_ai_overview=True
    )

    with pytest.raises(ReportGenerationError):
        await generator.generate_async(
            [],
            transcript=[
                TranscriptTurn(
                    "turn_001", "iv_test", "candidate", "answer", "我负责增长项目。"
                )
            ],
        )


async def test_llm_cover_duration_overrides_yaml_default():
    """LLM 给的「45分钟」是 yaml 默认值（与 actual 不符），应当被实际面试时长覆盖。"""
    from interview.report_generator import (
        _duration_text_matches_actual,
        _format_duration_seconds,
        _looks_like_realistic_duration,
    )

    # 兜底函数能正确把秒数格式化为「X 分 Y 秒」
    assert _format_duration_seconds(503) == "8 分 23 秒"
    assert _format_duration_seconds(45) == "45 秒"
    assert _format_duration_seconds(0) == ""
    # 当 actual_duration_seconds 已提供时，LLM 的「45分钟」必须匹配实际秒数才视为可信
    assert not _duration_text_matches_actual("45分钟", 503)
    assert _duration_text_matches_actual("8 分 23 秒", 503)
    assert _duration_text_matches_actual("8 分 25 秒", 503)  # ±3s 容忍
    # 没传 actual 时，宽松判断（包含中文时间单位即视为可信）
    assert _looks_like_realistic_duration("8 分 23 秒")
    assert not _looks_like_realistic_duration("")


def test_default_dimension_scoring_fallback_uses_exchanges():
    """LLM 把所有维度都给相同高分（典型「默认 8」行为）时，应当用 exchange.evaluation
    的真实维度分重新计算。"""
    from interview.models import DimensionAssessment
    from interview.report_generator import ReportGenerator

    exchanges = [
        Exchange(
            exchange_id="ex_001",
            question_id="q1",
            section_id="project",
            type="main_question",
            prompt_id="p1",
            prompt_text="项目问题",
            prompt_type="main_question",
            answer_text="我做了订单系统",
            evaluation=Evaluation(
                score=3,
                dimensions={
                    "communication_clarity": 8,
                    "problem_solving": 4,
                    "outcome_orientation": 6,
                },
                strengths=[],
                weaknesses=[],
            ),
        ),
        Exchange(
            exchange_id="ex_002",
            question_id="q2",
            section_id="business",
            type="main_question",
            prompt_id="p2",
            prompt_text="业务问题",
            prompt_type="main_question",
            answer_text="我做了支付",
            evaluation=Evaluation(
                score=3,
                dimensions={
                    "communication_clarity": 6,
                    "problem_solving": 4,
                    "outcome_orientation": 4,
                },
                strengths=[],
                weaknesses=[],
            ),
        ),
    ]
    # LLM 把所有维度都给 8（典型默认高分）
    llm_defaults = {
        "communication_clarity": DimensionAssessment(
            score=8,
            evidence=["LLM 假设的 evidence"],
            concerns=[],
            recommendations=[],
            confidence="medium",
        ),
        "problem_solving": DimensionAssessment(
            score=8,
            evidence=["LLM 假设的 evidence"],
            concerns=[],
            recommendations=[],
            confidence="medium",
        ),
        "outcome_orientation": DimensionAssessment(
            score=8,
            evidence=["LLM 假设的 evidence"],
            concerns=[],
            recommendations=[],
            confidence="medium",
        ),
    }
    fixed = ReportGenerator._fallback_dimension_scores(exchanges, llm_defaults)
    # 真实 evaluation 维度分 = (8+6)/2=7, (4+4)/2=4, (6+4)/2=5
    assert fixed["communication_clarity"].score == 7
    assert fixed["problem_solving"].score == 4
    assert fixed["outcome_orientation"].score == 5
    # 保留 LLM 原本的 evidence
    assert fixed["communication_clarity"].evidence == ["LLM 假设的 evidence"]


def test_default_dimension_scoring_detection():
    """检测 LLM 是否在「全默认打分」：所有维度完全相同高分 / 5 维度分差 ≤ 1 且均值 ≥ 7。"""
    from interview.models import DimensionAssessment
    from interview.report_generator import ReportGenerator

    five_same_high = {
        k: DimensionAssessment(score=8, evidence=[], concerns=[], recommendations=[], confidence="medium")
        for k in [
            "communication_clarity",
            "problem_solving",
            "outcome_orientation",
            "project_execution",
            "role_alignment",
        ]
    }
    assert ReportGenerator._looks_like_default_dimension_scoring(five_same_high)

    # 5 维度都是 7（差 0，均值 7）—— 触发
    five_same_7 = {
        k: DimensionAssessment(score=7, evidence=[], concerns=[], recommendations=[], confidence="medium")
        for k in [
            "communication_clarity",
            "problem_solving",
            "outcome_orientation",
            "project_execution",
            "role_alignment",
        ]
    }
    assert ReportGenerator._looks_like_default_dimension_scoring(five_same_7)

    # 5 维度 5/6 差 1，均值 5.6 < 7 —— 不触发
    five_5_6 = {
        k: DimensionAssessment(score=5, evidence=[], concerns=[], recommendations=[], confidence="medium")
        for k in [
            "communication_clarity",
            "problem_solving",
            "outcome_orientation",
            "project_execution",
            "role_alignment",
        ]
    }
    five_5_6["role_alignment"] = DimensionAssessment(
        score=6, evidence=[], concerns=[], recommendations=[], confidence="medium"
    )
    assert not ReportGenerator._looks_like_default_dimension_scoring(five_5_6)

    # 真实评估 9/3/7/5/8 差 6 —— 不触发
    varied = {
        "communication_clarity": DimensionAssessment(
            score=9, evidence=[], concerns=[], recommendations=[], confidence="medium"
        ),
        "problem_solving": DimensionAssessment(
            score=3, evidence=[], concerns=[], recommendations=[], confidence="medium"
        ),
        "outcome_orientation": DimensionAssessment(
            score=7, evidence=[], concerns=[], recommendations=[], confidence="medium"
        ),
        "project_execution": DimensionAssessment(
            score=5, evidence=[], concerns=[], recommendations=[], confidence="medium"
        ),
        "role_alignment": DimensionAssessment(
            score=8, evidence=[], concerns=[], recommendations=[], confidence="medium"
        ),
    }
    assert not ReportGenerator._looks_like_default_dimension_scoring(varied)


def test_actual_duration_seconds_passed_through_cover():
    """当 LLM 没返回 cover.durationText 时，cover.durationText 应来自 actual_duration_seconds。"""
    # 没有 LLM 也能直接走本地兜底
    report = ReportGenerator().generate(
        [],
        transcript=[],
        rubric_dimensions=[],
        actual_duration_seconds=503,
    )
    assert report.cover.duration_text == "8 分 23 秒"
    assert report.cover.interview_type == "综合面试"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", report.cover.generated_at)

    report2 = ReportGenerator().generate(
        [], transcript=[], rubric_dimensions=[], actual_duration_seconds=45
    )
    assert report2.cover.duration_text == "45 秒"

    # 没传 actual_duration_seconds → 留空
    report3 = ReportGenerator().generate([], transcript=[], rubric_dimensions=[])
    assert report3.cover.duration_text == ""


@pytest.mark.asyncio
async def test_required_ai_overview_builds_cover_when_llm_omits_it():
    llm = PipelineLlm()
    exchange = Exchange(
        exchange_id="ex_001",
        question_id="q1",
        section_id="business",
        type="main_question",
        prompt_id="p1",
        prompt_text="请介绍项目？",
        prompt_type="main_question",
        answer_text="我负责项目推进并完成了复盘。",
    )

    report = await ReportGenerator(
        llm,
        require_ai_overview=True,
        context={"target_role": "后端工程师"},
    ).generate_async(
        [exchange],
        transcript=[],
        actual_duration_seconds=125,
    )

    assert report.generation_source == "llm"
    assert report.cover.title == "后端工程师模拟面试报告"
    assert report.cover.interview_type == "综合面试"
    assert report.cover.duration_text == "2 分 5 秒"
    assert report.cover.score == report.overall_score == 78
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", report.cover.generated_at)


@pytest.mark.asyncio
async def test_long_transcript_is_chunked_before_overview_and_fully_covered():
    llm = PipelineLlm()
    exchanges = [
        Exchange(
            exchange_id=f"ex_{index:03d}",
            question_id=f"q_{index:03d}",
            section_id="business",
            type="main_question",
            prompt_id=f"p_{index:03d}",
            prompt_text=f"请说明第 {index} 个项目？",
            prompt_type="main_question",
            answer_text=("RAW_LONG_SENTINEL_具体回答" * 220),
        )
        for index in range(1, 21)
    ]

    report = await ReportGenerator(llm, require_ai_overview=True).generate_async(
        exchanges,
        transcript=[],
        rubric_dimensions=["project_execution"],
    )

    qa_prompts = [p for p in llm.prompts if "经过清洗的问答单元" in p]
    overview_prompts = [p for p in llm.prompts if "已经由 AI 完成的分块分析摘要" in p]
    assert len(qa_prompts) > 1
    assert len(overview_prompts) == 1
    assert all(len(prompt) < 40000 for prompt in llm.prompts)
    assert "RAW_LONG_SENTINEL" not in overview_prompts[0]
    assert '"source":"ai_chunk_analyses"' in overview_prompts[0]
    assert len(report.qa_analyses) == len(exchanges)
    assert report.generation_source == "llm"


@pytest.mark.asyncio
async def test_successful_qa_chunks_are_reused_when_report_is_retried():
    llm = PipelineLlm()
    generator = ReportGenerator(llm, require_ai_overview=True)
    exchange = Exchange(
        exchange_id="ex_001",
        question_id="q1",
        section_id="business",
        type="main_question",
        prompt_id="p1",
        prompt_text="请介绍项目？",
        prompt_type="main_question",
        answer_text="我负责项目推进并完成了复盘。",
    )

    await generator.generate_async([exchange], transcript=[])
    first_qa_calls = sum("经过清洗的问答单元" in p for p in llm.prompts)
    await generator.generate_async([exchange], transcript=[])
    second_qa_calls = sum("经过清洗的问答单元" in p for p in llm.prompts)

    assert first_qa_calls == 1
    assert second_qa_calls == first_qa_calls


@pytest.mark.asyncio
async def test_incomplete_overview_is_repaired_inside_retry_loop():
    llm = RepairingOverviewLlm()
    generator = ReportGenerator(llm, require_ai_overview=True)
    exchange = Exchange(
        exchange_id="ex_001",
        question_id="q1",
        section_id="business",
        type="main_question",
        prompt_id="p1",
        prompt_text="请介绍项目？",
        prompt_type="main_question",
        answer_text="我负责项目推进并完成了复盘。",
    )

    report = await generator.generate_async([exchange], transcript=[])

    overview_prompts = [
        prompt
        for prompt in llm.prompts
        if "已经由 AI 完成的分块分析摘要" in prompt
    ]
    assert llm.overview_calls == 2
    assert "AI 综合结论缺少学习计划" in overview_prompts[1]
    assert "上一次待修复 JSON" in overview_prompts[1]
    assert len(report.learning_plan.phases) == 3


@pytest.mark.asyncio
async def test_validation_error_progress_keeps_five_of_six_steps(monkeypatch):
    llm = PipelineLlm()
    generator = ReportGenerator(llm, require_ai_overview=True)
    updates = []
    exchange = Exchange(
        exchange_id="ex_001",
        question_id="q1",
        section_id="business",
        type="main_question",
        prompt_id="p1",
        prompt_text="请介绍项目？",
        prompt_type="main_question",
        answer_text="我负责项目推进并完成了复盘。",
    )

    def fail_final_validation(_report, _qa_items):
        raise RuntimeError("QA coverage mismatch")

    monkeypatch.setattr(generator, "_validate_complete_ai_report", fail_final_validation)

    with pytest.raises(Exception, match="QA coverage mismatch"):
        await generator.generate_async(
            [exchange], transcript=[], on_progress=updates.append
        )

    validating = next(item for item in updates if item["stage"] == "validating")
    failed = updates[-1]
    assert validating["percent"] == 94
    assert failed["state"] == "error"
    assert failed["stage"] == "validating"
    assert failed["completed_steps"] == validating["completed_steps"]
    assert failed["total_steps"] == validating["total_steps"]
    assert failed["percent"] == 94


@pytest.mark.asyncio
async def test_required_ai_report_never_publishes_local_qa_fallback(monkeypatch):
    import interview.report_generator as report_module
    from interview.report_generator import ReportGenerationError

    async def no_wait(_seconds):
        return None

    class BrokenQaLlm:
        async def generate_json_once(self, prompt, max_tokens=1024, temperature=0.2):
            return '{"qaAnalyses":[]}'

    monkeypatch.setattr(report_module, "REPORT_QA_ATTEMPTS", 2)
    monkeypatch.setattr(report_module.asyncio, "sleep", no_wait)
    exchange = Exchange(
        exchange_id="ex_001",
        question_id="q1",
        section_id="business",
        type="main_question",
        prompt_id="p1",
        prompt_text="请介绍项目？",
        prompt_type="main_question",
        answer_text="我负责项目推进。",
    )

    with pytest.raises(ReportGenerationError):
        await ReportGenerator(
            BrokenQaLlm(), require_ai_overview=True
        ).generate_async([exchange], transcript=[])
