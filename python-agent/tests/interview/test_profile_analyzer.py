import asyncio
import json

from interview.profile import CandidateProfile
from interview.profile_analyzer import (
    MAX_BRIEF_CHARS,
    MAX_PLANNER_CONTEXT_CHARS,
    analyze_candidate_profile,
    fallback_candidate_brief,
)


class FakeLlm:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    async def generate_once(self, prompt, max_tokens=None):
        self.prompts.append(prompt)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class SlowLlm:
    async def generate_once(self, prompt, max_tokens=None):
        await asyncio.sleep(0.2)
        return "{}"


def test_analysis_receives_raw_once_and_returns_bounded_brief():
    internship_source = "在星云科技实习，负责推荐系统特征工程，CTR 提升 8%。"
    project_source = "订单系统项目中负责缓存架构，接口延迟降低 30%。"
    raw = {
        "job_summary": "负责高并发后端系统",
        "job_requirements": ["负责高并发系统"],
        "candidate_name": "张三",
        "education": "硕士",
        "schools": ["示例大学"],
        "candidate_summary": "五年 Python 后端经验",
        "skills": ["Python", "Redis"],
        "internships": [
            {
                "name": "推荐系统实习",
                "organization": "星云科技",
                "role": "后端实习生",
                "period": "2024",
                "summary": "负责特征工程",
                "highlights": ["CTR 提升 8%"],
                "source_excerpt": internship_source,
            }
        ],
        "projects": [
            {
                "name": "订单系统",
                "summary": "负责缓存架构",
                "highlights": ["延迟下降 30%"],
                "source_excerpt": project_source,
            }
        ],
        "role_matches": ["后端经验匹配"],
        "verification_points": ["确认个人贡献"],
        "question_focus": ["系统设计"],
    }
    llm = FakeLlm(json.dumps(raw, ensure_ascii=False))
    profile = CandidateProfile(
        target_role="后端工程师",
        jd_text="JD_RAW_SENTINEL",
        resume_text=(
            "RESUME_RAW_SENTINEL\n姓名：张三\n学历：硕士\n学校：示例大学\n"
            f"{internship_source}\n{project_source}"
        ),
    )
    brief = asyncio.run(analyze_candidate_profile(profile, llm))
    assert len(llm.prompts) == 1
    assert "JD_RAW_SENTINEL" in llm.prompts[0]
    assert "RESUME_RAW_SENTINEL" in llm.prompts[0]
    assert len(brief.as_context()) <= MAX_BRIEF_CHARS
    assert len(brief.planner_context()) <= MAX_PLANNER_CONTEXT_CHARS
    assert brief.candidate_name == "张三"
    assert brief.education == "硕士"
    assert brief.schools == ["示例大学"]
    assert "JD_RAW_SENTINEL" not in brief.as_context()
    assert "RESUME_RAW_SENTINEL" not in brief.as_context()
    assert internship_source not in brief.as_context()
    assert project_source not in brief.as_context()
    assert internship_source in brief.planner_context()
    assert project_source in brief.planner_context()


def test_empty_jd_stays_empty_even_if_model_invents_requirements():
    llm = FakeLlm(
        json.dumps(
            {
                "job_summary": "模型自行猜测的岗位",
                "job_requirements": ["模型自行猜测的要求"],
                "candidate_summary": "有项目经验",
            },
            ensure_ascii=False,
        )
    )
    profile = CandidateProfile(target_role="产品经理", resume_text="负责项目推进")
    brief = asyncio.run(analyze_candidate_profile(profile, llm))
    assert brief.has_jd is False
    assert brief.job_summary == ""
    assert brief.job_requirements == []


def test_timeout_and_failure_use_non_fabricating_fallback():
    profile = CandidateProfile(
        target_role="产品经理",
        jd_text="负责需求分析",
        resume_text="主导增长项目，负责数据分析和跨团队协作。",
    )
    timed_out = asyncio.run(
        analyze_candidate_profile(profile, SlowLlm(), timeout_seconds=0.01)
    )
    failed = asyncio.run(analyze_candidate_profile(profile, FakeLlm(ValueError("bad"))))
    for brief in (timed_out, failed):
        assert brief.has_jd is True
        assert any("需求分析" in item for item in brief.job_requirements)
        assert "跨团队协作" in brief.resume_context()


def test_local_fallback_does_not_create_missing_jd():
    brief = fallback_candidate_brief(
        CandidateProfile(
            target_role="设计师",
            resume_text="姓名：李雷\n本科\n示例大学\n负责移动端设计项目",
        )
    )
    assert brief.has_jd is False
    assert brief.job_requirements == []
    assert brief.candidate_name == "李雷"
    assert brief.education == "本科"
    assert "示例大学" in brief.schools
    assert brief.projects
    assert "负责移动端设计项目" in brief.planner_context()
    assert "source_excerpt" not in brief.as_context()
    assert "岗位职责" not in brief.as_context()


def test_local_fallback_preserves_company_role_and_project_responsibility():
    brief = fallback_candidate_brief(
        CandidateProfile(
            target_role="产品经理",
            resume_text=(
                "示例科技有限公司 产品实习生 2024.03-2024.08\n"
                "负责新用户激活流程优化和数据复盘\n"
                "校园增长项目\n"
                "主导用户调研、方案设计和上线复盘"
            ),
        )
    )
    assert brief.internships
    internship = brief.internships[0]
    assert internship["organization"] == "示例科技有限公司"
    assert internship["role"] == "产品实习生"
    assert "激活流程" in internship["summary"]
    assert brief.projects
    assert any("增长项目" in item["name"] for item in brief.projects)
    assert "示例科技有限公司" in brief.planner_context()


def test_llm_profile_cleans_markdown_resume_table_identity():
    raw_row = (
        "**任意门Soul** **App**    **用户策略组**    "
        "**策略产品经理**    **2025.10-2026.05**"
    )
    llm = FakeLlm(
        json.dumps(
            {
                "candidate_summary": "策略产品经验",
                "internships": [
                    {
                        "organization": raw_row,
                        "role": "策略产品经理",
                        "period": "2025.10-2026.05",
                        "summary": "负责用户策略优化",
                        "source_excerpt": raw_row,
                    }
                ],
            },
            ensure_ascii=False,
        )
    )
    profile = CandidateProfile(target_role="产品经理", resume_text=raw_row)

    brief = asyncio.run(analyze_candidate_profile(profile, llm))

    assert brief.internships[0]["organization"] == "任意门Soul App"
    assert brief.internships[0]["role"] == "策略产品经理"
    assert "用户策略组" not in brief.internships[0]["organization"]
    assert "2025.10" not in brief.internships[0]["organization"]
    assert "**" not in brief.internships[0]["organization"]


def test_local_fallback_never_turns_honors_or_skills_into_projects():
    brief = fallback_candidate_brief(
        CandidateProfile(
            target_role="产品经理",
            resume_text=(
                "主要荣誉\n负责校级创新奖申报并获得一等奖\n"
                "专业技能\n参与数据分析与原型设计\n"
                "项目经历\n校园增长项目\n主导用户调研和上线复盘"
            ),
        )
    )
    assert [item["name"] for item in brief.projects] == ["校园增长项目"]
    assert all("荣誉" not in item["name"] for item in brief.projects)
    assert all("技能" not in item["name"] for item in brief.projects)
