"""Tests for interview.interview_planner — résumé/JD-driven interview plan."""

import asyncio
import json

import pytest

from interview.interview_planner import InterviewPlanner, InterviewPlanningError
from interview.models import QuestionSpec


class FakeLlm:
    def __init__(self, response):
        self._response = response
        self.calls = 0
        self.prompt = ""
        self.last_prompt = ""

    async def generate(self, prompt, max_tokens=None):
        self.calls += 1
        self.prompt = prompt
        self.last_prompt = prompt
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def generate_once(self, prompt, max_tokens=None):
        self.calls += 1
        self.prompt = prompt
        self.last_prompt = prompt
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class SlowLlm:
    async def generate_once(self, prompt, max_tokens=None):
        await asyncio.sleep(0.2)
        return "{}"


class SlowJsonLlm:
    _base_url = "https://api.deepseek.com"
    _model = "deepseek-v4-flash"

    async def generate_json_once(self, prompt, max_tokens=None, temperature=None):
        await asyncio.sleep(0.2)
        return "{}"


def _bank():
    return [
        QuestionSpec(
            section_id="s1", section_title="后端技术", question_id="q_backend",
            prompt="接口性能如何处理？", competency="reliability",
        ),
        QuestionSpec(
            section_id="s2", section_title="架构设计", question_id="q_arch",
            prompt="如何设计高并发服务？", competency="depth",
        ),
    ]


def _titles(plan):
    return [slot.section_title for slot in plan]


def test_llm_plan_structure_and_budgets():
    llm = FakeLlm(
        json.dumps(
            {
                "resumeQuestions": [
                    {"prompt": "讲讲你在订单系统的角色", "competency": "ownership",
                     "expectedSignals": ["拆分", "一致性"]},
                    {"prompt": "讲讲你做的缓存方案"},
                ],
                "businessQuestions": [
                    {"bankId": "q_arch"},
                    {"prompt": "怎么做幂等？", "competency": "reliability"},
                    {"prompt": "限流怎么做？"},
                ],
            }
        )
    )
    plan = asyncio.run(
        InterviewPlanner(llm).build_plan(
            candidate_brief='{"candidate_summary":"做过订单系统"}',
            has_resume=True,
            target_role="后端",
            bank=_bank(),
        )
    )
    assert _titles(plan) == ["自我介绍", "简历经历", "简历经历", "业务题", "业务题", "业务题"]
    by_id = {slot.question_id: slot for slot in plan}
    # 新规则：自我介绍不追问（不论是否有简历）
    assert by_id["self_intro"].max_followups == 0
    # 简历核心问题最多追问 1 次
    resume_slots = [s for s in plan if s.section_id == "resume_experience"]
    assert all(s.max_followups <= 1 for s in resume_slots)
    resume_intro_slots = [s for s in plan if s.section_id == "resume_project_intro"]
    assert all(s.max_followups == 1 for s in resume_intro_slots)
    business_slots = [s for s in plan if s.section_id == "business"]
    assert all(s.max_followups <= 1 for s in business_slots)
    # 当前 plan 默认：简历核心问题预算为 1（与新规则保持一致）
    assert plan[1].max_followups == 1
    assert plan[1].expected_signals == ["拆分", "一致性"]
    business = [s for s in plan if s.section_id == "business"]
    assert business[0].prompt == "如何设计高并发服务？"       # bankId → bank question
    assert all(s.max_followups == 1 for s in business)       # business → 1


def test_caps_counts_from_llm():
    llm = FakeLlm(
        json.dumps(
            {
                "resumeQuestions": [{"prompt": f"r{i}"} for i in range(5)],
                "businessQuestions": [{"prompt": f"b{i}"} for i in range(6)],
            }
        )
    )
    plan = asyncio.run(
        InterviewPlanner(llm).build_plan(candidate_brief="{}", has_resume=True, bank=[])
    )
    assert len([s for s in plan if s.section_id == "resume_experience"]) == 2
    assert len([s for s in plan if s.section_id == "business"]) == 3


def test_planner_prompt_uses_brief_and_complete_source_material():
    llm = FakeLlm(
        json.dumps(
            {
                "resumeQuestions": [
                    {
                        "experienceRef": "示例科技有限公司 / 产品实习生",
                        "prompt": "你如何推进新用户激活流程优化？",
                    }
                ],
                "businessQuestions": [{"prompt": "你会如何定义产品目标？"}],
            },
            ensure_ascii=False,
        )
    )
    brief = '{"candidate_summary":"精简后的候选人画像"}'
    asyncio.run(
        InterviewPlanner(llm).build_plan(
            candidate_brief=brief,
            has_resume=True,
            target_role="产品经理",
            bank=_bank(),
            jd_text="JD_RAW_SENTINEL：负责产品规划",
            resume_text=(
                "RESUME_RAW_SENTINEL：示例科技有限公司 产品实习生，"
                "负责新用户激活流程优化"
            ),
        )
    )
    assert "精简后的候选人画像" in llm.last_prompt
    assert "RESUME_RAW_SENTINEL" in llm.last_prompt
    assert "JD_RAW_SENTINEL" in llm.last_prompt


def test_resume_question_is_forced_to_name_the_bound_experience():
    brief = json.dumps(
        {
            "internships": [
                {
                    "organization": "示例科技有限公司",
                    "role": "产品实习生",
                    "summary": "负责新用户激活流程优化",
                }
            ]
        },
        ensure_ascii=False,
    )
    llm = FakeLlm(
        json.dumps(
            {
                "resumeQuestions": [
                    {
                        "experienceId": "internship_1",
                        "prompt": "请讲讲你当时是如何推进工作的？",
                    }
                ],
                "businessQuestions": [{"prompt": "你会如何定义产品目标？"}],
            },
            ensure_ascii=False,
        )
    )

    plan = asyncio.run(
        InterviewPlanner(llm, resume_experiences=1).build_plan(
            candidate_brief=brief,
            has_resume=True,
            target_role="产品经理",
            resume_text="示例科技有限公司 产品实习生 负责新用户激活流程优化",
        )
    )

    resume_question = next(
        item for item in plan if item.section_id == "resume_experience"
    )
    assert "示例科技有限公司" in resume_question.prompt
    assert "担任产品实习生时" in resume_question.prompt
    assert len(resume_question.prompt) <= 100
    assert resume_question.source_reference


def test_resume_question_uses_short_project_name_when_company_is_missing():
    brief = json.dumps(
        {
            "projects": [
                {
                    "name": "校园增长项目",
                    "role": "项目负责人",
                    "summary": "负责用户研究和增长实验",
                }
            ]
        },
        ensure_ascii=False,
    )
    llm = FakeLlm(
        json.dumps(
            {
                "resumeQuestions": [
                    {
                        "experienceId": "project_1",
                        "prompt": "你如何确定增长实验的优先级？",
                    }
                ],
                "businessQuestions": [{"prompt": "你会如何定义产品目标？"}],
            },
            ensure_ascii=False,
        )
    )

    plan = asyncio.run(
        InterviewPlanner(llm, resume_experiences=1).build_plan(
            candidate_brief=brief,
            has_resume=True,
            target_role="产品经理",
            resume_text="校园增长项目 项目负责人 负责用户研究和增长实验",
        )
    )

    question = next(item for item in plan if item.section_id == "resume_experience")
    assert "校园增长项目" in question.prompt
    assert "担任项目负责人时" in question.prompt


def test_resume_table_row_is_spoken_as_company_and_role_not_raw_markdown():
    raw_row = (
        "**任意门Soul** **App**    **用户策略组**    "
        "**策略产品经理**    **2025.10-2026.05**"
    )
    brief = json.dumps(
        {
            "internships": [
                {
                    "organization": raw_row,
                    "role": "策略产品经理",
                    "period": "2025.10-2026.05",
                    "summary": "负责用户策略优化",
                }
            ]
        },
        ensure_ascii=False,
    )
    llm = FakeLlm(
        json.dumps(
            {
                "resumeQuestions": [
                    {
                        "experienceId": "internship_1",
                        "prompt": f"针对{raw_row}，你当时如何识别用户策略问题？",
                    }
                ],
                "businessQuestions": [{"prompt": "你如何制定产品策略？"}],
            },
            ensure_ascii=False,
        )
    )

    plan = asyncio.run(
        InterviewPlanner(llm, resume_experiences=1).build_plan(
            candidate_brief=brief,
            has_resume=True,
            target_role="产品经理",
            resume_text=raw_row + " 负责用户策略优化",
        )
    )

    question = next(item for item in plan if item.section_id == "resume_experience")
    assert question.prompt.startswith("你在任意门Soul App担任策略产品经理时，")
    assert "用户策略组" not in question.prompt
    assert "2025.10" not in question.prompt
    assert "**" not in question.prompt


def test_uploaded_resume_without_any_bound_experience_blocks_weak_plan():
    llm = FakeLlm(
        json.dumps(
            {
                "resumeQuestions": [{"prompt": "请选择一段经历介绍一下？"}],
                "businessQuestions": [{"prompt": "你如何理解这个岗位？"}],
            },
            ensure_ascii=False,
        )
    )

    with pytest.raises(InterviewPlanningError, match="具体公司、岗位或项目"):
        asyncio.run(
            InterviewPlanner(llm, resume_experiences=1).build_plan(
                candidate_brief="{}",
                has_resume=True,
                target_role="产品经理",
                resume_text="只有职责描述，没有可识别的公司名称或项目名称",
            )
        )


@pytest.mark.parametrize(
    "reference",
    ["实习1", "项目1", "第一段实习", "产品实习生"],
)
def test_numbered_or_role_only_resume_reference_is_rejected(reference):
    llm = FakeLlm(
        json.dumps(
            {
                "resumeQuestions": [
                    {"experienceRef": reference, "prompt": "请介绍一下这段经历？"}
                ],
                "businessQuestions": [{"prompt": "你如何理解这个岗位？"}],
            },
            ensure_ascii=False,
        )
    )

    with pytest.raises(InterviewPlanningError):
        asyncio.run(
            InterviewPlanner(llm, resume_experiences=1).build_plan(
                candidate_brief="{}",
                has_resume=True,
                target_role="产品经理",
                resume_text=f"{reference} 负责需求分析和项目推进",
            )
        )


def test_no_resume_skips_resume_and_boosts_intro():
    # no LLM → rule fallback; no résumé → no résumé slots, self-intro probed harder
    plan = asyncio.run(
        InterviewPlanner(None).build_plan(candidate_brief="{}", has_resume=False, bank=_bank())
    )
    assert not any(s.section_id == "resume_experience" for s in plan)
    assert plan[0].question_id == "self_intro"
    # 新规则：自我介绍不追问
    assert plan[0].max_followups == 0
    assert [s.section_id for s in plan[1:]] == ["business", "business"]  # bank has 2


def test_no_bank_no_llm_generates_generic_business():
    plan = asyncio.run(
        InterviewPlanner(None).build_plan(candidate_brief="{}", has_resume=True, bank=[])
    )
    business = [s for s in plan if s.section_id == "business"]
    assert len(business) == 3                                # generic fallback
    assert all(s.prompt for s in business)
    assert any(s.section_id == "resume_experience" for s in plan)  # has résumé → 1 slot


def test_product_manager_without_jd_never_receives_backend_fallback_questions():
    plan = asyncio.run(
        InterviewPlanner(None).build_plan(
            candidate_brief="{}",
            has_resume=False,
            target_role="产品经理",
            bank=[],
        )
    )
    prompts = [item.prompt for item in plan if item.section_id == "business"]
    assert prompts
    assert all("产品经理" in prompt for prompt in prompts)
    assert not any(
        term in "".join(prompts)
        for term in ("高并发", "高可用", "缓存", "幂等", "线上故障")
    )


def test_fallback_resume_question_names_company_or_project_with_role():
    brief = json.dumps(
        {
            "internships": [
                {
                    "organization": "示例科技有限公司",
                    "role": "产品实习生",
                    "summary": "负责新用户激活流程优化",
                }
            ],
            "projects": [
                {
                    "name": "校园增长项目",
                    "role": "项目负责人",
                    "summary": "设计增长实验并复盘数据",
                }
            ],
        },
        ensure_ascii=False,
    )
    plan = asyncio.run(
        InterviewPlanner(None).build_plan(
            candidate_brief=brief,
            has_resume=True,
            target_role="产品经理",
            bank=[],
        )
    )
    resume_text = "\n".join(
        item.prompt for item in plan if item.section_id == "resume_experience"
    )
    for term in (
        "示例科技有限公司",
        "新用户激活流程优化",
        "校园增长项目",
        "设计增长实验并复盘数据",
    ):
        assert term in resume_text
    assert "示例科技有限公司担任产品实习生时" in resume_text
    assert "校园增长项目担任项目负责人时" in resume_text


def test_two_unreliable_resume_entries_produce_only_one_generic_question():
    brief = json.dumps(
        {
            "internships": [
                {
                    "organization": "主要荣誉",
                    "role": "主要荣誉",
                    "summary": "主要荣誉",
                }
            ],
            "projects": [
                {
                    "name": "熟练使用 Figma、Photoshop",
                    "role": "熟练使用 Figma、Photoshop",
                    "summary": "熟练使用 Figma、Photoshop",
                }
            ],
        },
        ensure_ascii=False,
    )

    plan = asyncio.run(
        InterviewPlanner(None).build_plan(
            candidate_brief=brief,
            has_resume=True,
            target_role="UI/UX 设计师",
            bank=[],
        )
    )

    resume_prompts = [
        item.prompt for item in plan if item.section_id == "resume_experience"
    ]
    normalized = ["".join(prompt.split()).rstrip("？?") for prompt in (item.prompt for item in plan)]
    assert len(resume_prompts) == 1
    assert "请选择" in resume_prompts[0] or "选择一段" in resume_prompts[0]
    assert len(normalized) == len(set(normalized))


def test_llm_failure_falls_back():
    llm = FakeLlm(ValueError("boom"))
    plan = asyncio.run(
        InterviewPlanner(llm).build_plan(
            candidate_brief="{}", has_resume=True, bank=_bank()
        )
    )
    assert _titles(plan)[0] == "自我介绍"
    assert any(s.section_id == "resume_experience" for s in plan)
    assert any(s.section_id == "business" for s in plan)


def test_llm_failure_with_uploaded_sources_never_starts_generic_interview():
    llm = FakeLlm(ValueError("boom"))

    with pytest.raises(InterviewPlanningError, match="不会使用空泛兜底题"):
        asyncio.run(
            InterviewPlanner(llm).build_plan(
                candidate_brief="{}",
                has_resume=True,
                target_role="产品经理",
                jd_text="负责产品规划与用户增长",
                resume_text="示例科技有限公司 产品实习生 负责增长项目",
                bank=[],
            )
        )


def test_llm_timeout_falls_back_promptly():
    plan = asyncio.run(
        InterviewPlanner(SlowLlm(), llm_timeout_seconds=0.01).build_plan(
            candidate_brief="{}", has_resume=True, bank=_bank()
        )
    )
    assert _titles(plan)[0] == "自我介绍"
    assert any(s.section_id == "resume_experience" for s in plan)
    assert any(s.section_id == "business" for s in plan)


def test_plan_request_timeout_is_longer_for_large_resume_prompts():
    small = InterviewPlanner._plan_request_timeout_seconds("x" * 1000)
    large = InterviewPlanner._plan_request_timeout_seconds("x" * 60000)

    assert small == 15.0
    assert large > small
    assert large <= 35.0


def test_plan_request_timeout_error_is_explicit_and_names_model(monkeypatch):
    planner = InterviewPlanner(SlowJsonLlm())
    monkeypatch.setattr(planner, "_plan_request_timeout_seconds", lambda prompt: 0.01)

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(planner._request_plan_json("long planner prompt"))

    message = str(exc_info.value)
    assert "deepseek-v4-flash" in message
    assert "连续 3 次失败" in message
    assert "超过" in message
    assert not message.endswith(":")


def test_llm_business_only_still_completes_plan():
    # LLM returns no business questions → planner backfills from the bank
    llm = FakeLlm(json.dumps({"resumeQuestions": [{"prompt": "讲讲经历"}], "businessQuestions": []}))
    plan = asyncio.run(
        InterviewPlanner(llm).build_plan(
            candidate_brief="{}", has_resume=True, bank=_bank()
        )
    )
    assert any(s.section_id == "business" for s in plan)


def test_generated_questions_are_single_bounded_questions():
    llm = FakeLlm(
        json.dumps(
            {
                "resumeQuestions": [
                    {"prompt": "先复述下面整段简历：" + "负责复杂工作" * 80 + "？然后评价？"}
                ],
                "businessQuestions": [
                    {"prompt": "你会如何推进？还会如何复盘？"}
                ],
            },
            ensure_ascii=False,
        )
    )
    plan = asyncio.run(
        InterviewPlanner(llm).build_plan(
            candidate_brief="{}", has_resume=True, target_role="产品经理", bank=[]
        )
    )
    for question in plan:
        assert len(question.prompt) <= 160
        assert question.prompt.count("？") == 1
        assert "负责复杂工作" * 10 not in question.prompt


def test_enterprise_strict_planning_blocks_after_internal_retries():
    planner = InterviewPlanner(
        FakeLlm(ValueError("provider unavailable")), allow_fallback=False
    )
    with pytest.raises(InterviewPlanningError):
        asyncio.run(
            planner.build_plan(
                candidate_brief="{}",
                has_resume=True,
                target_role="产品经理",
                bank=_bank(),
            )
        )
