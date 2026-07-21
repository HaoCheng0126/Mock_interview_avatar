"""One-shot résumé/JD analysis with selective experience evidence.

Repeated LLM calls receive a small structured profile.  The question planner gets
the same profile plus bounded source excerpts for individual internships/projects,
so it can ask about concrete details without carrying the full résumé every time.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from dataclasses import dataclass, field

from interview.profile import CandidateProfile

logger = logging.getLogger(__name__)

MAX_BRIEF_CHARS = 2500
MAX_PLANNER_CONTEXT_CHARS = 8000
ANALYSIS_TIMEOUT_SECONDS = 28.0


@dataclass(frozen=True)
class CandidateBrief:
    target_role: str = ""
    has_jd: bool = False
    job_summary: str = ""
    job_requirements: list[str] = field(default_factory=list)
    candidate_name: str = ""
    education: str = ""
    schools: list[str] = field(default_factory=list)
    candidate_summary: str = ""
    skills: list[str] = field(default_factory=list)
    internships: list[dict] = field(default_factory=list)
    projects: list[dict] = field(default_factory=list)
    role_matches: list[str] = field(default_factory=list)
    verification_points: list[str] = field(default_factory=list)
    question_focus: list[str] = field(default_factory=list)

    def as_dict(self, *, include_sources: bool = False) -> dict:
        def _entries(values: list[dict]) -> list[dict]:
            entries = copy.deepcopy(values)
            if not include_sources:
                for item in entries:
                    item.pop("source_excerpt", None)
            return entries

        return {
            "target_role": self.target_role,
            "has_jd": self.has_jd,
            "job_summary": self.job_summary,
            "job_requirements": list(self.job_requirements),
            "candidate_name": self.candidate_name,
            "education": self.education,
            "schools": list(self.schools),
            "candidate_summary": self.candidate_summary,
            "skills": list(self.skills),
            "internships": _entries(self.internships),
            "projects": _entries(self.projects),
            "role_matches": list(self.role_matches),
            "verification_points": list(self.verification_points),
            "question_focus": list(self.question_focus),
        }

    def as_context(self) -> str:
        """Small context used by evaluator/follow-up/report; no source excerpts."""
        return _bounded_json(self.as_dict(include_sources=False), MAX_BRIEF_CHARS)

    def planner_context(self) -> str:
        """Planner-only context with bounded source excerpts per concrete experience."""
        return _bounded_json(
            self.as_dict(include_sources=True), MAX_PLANNER_CONTEXT_CHARS
        )

    def job_context(self) -> str:
        if not self.has_jd:
            return "（候选人未提供岗位 JD）"
        parts = [self.job_summary] if self.job_summary else []
        if self.job_requirements:
            parts.append("要求：" + "；".join(self.job_requirements))
        return "\n".join(parts) or "（已提供 JD，但未提取到明确要求）"

    def resume_context(self) -> str:
        """Compatibility view for legacy {resume}; deliberately excludes excerpts."""
        parts: list[str] = []
        identity = " / ".join(
            item
            for item in (
                self.candidate_name,
                self.education,
                "、".join(self.schools),
            )
            if item
        )
        if identity:
            parts.append(identity)
        if self.candidate_summary:
            parts.append(self.candidate_summary)
        if self.skills:
            parts.append("技能：" + "、".join(self.skills))
        for label, values in (("实习", self.internships), ("项目", self.projects)):
            for item in values:
                title = str(item.get("name") or item.get("organization") or label)
                role = str(item.get("role") or "")
                summary = str(item.get("summary") or "")
                parts.append(
                    f"{label}：" + " / ".join(x for x in (title, role, summary) if x)
                )
        return "\n".join(parts) or "（未提取到候选人经历）"


def _compact_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _bounded_json(source: dict, limit: int) -> str:
    """Return valid JSON under the cap while preserving the stable schema."""
    data = copy.deepcopy(source)
    text = _compact_json(data)
    shrink_lists = (
        "verification_points",
        "role_matches",
        "question_focus",
        "skills",
        "job_requirements",
        "schools",
    )
    while len(text) > limit:
        changed = False
        for key in shrink_lists:
            values = data.get(key) or []
            if len(values) > 1:
                values.pop()
                changed = True
                break
        if not changed:
            for key in ("internships", "projects"):
                for item in data.get(key) or []:
                    excerpt = str(item.get("source_excerpt") or "")
                    if len(excerpt) > 120:
                        item["source_excerpt"] = excerpt[:-80]
                        changed = True
                        break
                if changed:
                    break
        if not changed:
            for key in ("projects", "internships"):
                values = data.get(key) or []
                if len(values) > 1:
                    values.pop()
                    changed = True
                    break
        if not changed:
            for key in ("candidate_summary", "job_summary"):
                value = str(data.get(key) or "")
                if len(value) > 100:
                    data[key] = value[:-80]
                    changed = True
                    break
        if not changed:
            break
        text = _compact_json(data)
    if len(text) <= limit:
        return text
    # Preserve named experiences even under extreme input density. Remove verbose
    # evidence first; the planner still needs company/project name, role and work.
    for key in ("internships", "projects"):
        compact_entries = []
        for item in (data.get(key) or [])[:2]:
            compact_entries.append(
                {
                    "name": _clip(item.get("name"), 80),
                    "organization": _clip(item.get("organization"), 80),
                    "role": _clip(item.get("role"), 60),
                    "period": _clip(item.get("period"), 50),
                    "summary": _clip(item.get("summary"), 120),
                    "highlights": [],
                    "source_excerpt": "",
                }
            )
        data[key] = compact_entries
    data["candidate_summary"] = _clip(data.get("candidate_summary"), 180)
    data["job_summary"] = _clip(data.get("job_summary"), 180)
    data["verification_points"] = []
    data["role_matches"] = []
    data["question_focus"] = []
    return _compact_json(data)


def _clip(value: object, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _plain_resume_field(value: object) -> str:
    """Remove PDF/Markdown table decoration while preserving readable names."""
    text = str(value or "")
    text = re.sub(r"[`*_#]+", "", text)
    text = re.sub(r"[|｜\t]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -—–·•,，;；/｜|")


def _clean_experience_role(value: object) -> str:
    text = _plain_resume_field(value)
    if not text:
        return ""
    role_pattern = re.compile(
        r"(?:(?:高级|资深|策略|增长|商业|用户|平台|数据|产品|项目|交互|视觉|"
        r"UI|UX|研发|前端|后端|测试|算法|设计|运营|市场){0,3})"
        r"(?:产品经理|项目经理|设计师|工程师|分析师|负责人|实习生|研究员|"
        r"顾问|总监|主管|助理|运营|策划|创始人|合伙人|HRBP|PM)",
        re.IGNORECASE,
    )
    matches = list(role_pattern.finditer(text))
    return _clip(matches[-1].group(0), 40) if matches else ""


def _clean_experience_identity(
    value: object,
    *,
    role: str = "",
    period: str = "",
) -> str:
    """Turn a copied résumé table row into a concise company/project name."""
    text = _plain_resume_field(value)
    if not text:
        return ""
    if period:
        text = text.replace(_plain_resume_field(period), " ")
    text = re.sub(
        r"(?:19|20)\d{2}[./年\-]\d{1,2}\s*(?:至|到|[-—–~～])\s*"
        r"(?:(?:19|20)\d{2}[./年\-]\d{1,2}|至今|现在)",
        " ",
        text,
    )
    if role:
        text = re.sub(re.escape(role), " ", text, flags=re.IGNORECASE)
    # Department/team names help the LLM understand the résumé but sound robotic
    # when spoken as part of the company identity.
    text = re.sub(
        r"(?<!\S)[A-Za-z\u4e00-\u9fff]{1,16}(?:事业部|部门|中心|团队|小组|组)(?!\S)",
        " ",
        text,
    )
    text = re.sub(r"\s+", " ", text).strip(" -—–·•,，;；/｜|")
    return text[:64]


def _string_list(value: object, *, count: int, item_chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _clip(item, item_chars)
        if text and text not in result:
            result.append(text)
        if len(result) >= count:
            break
    return result


def _source_excerpt(value: object, resume_text: str) -> str:
    """Keep only excerpts that actually occur in the submitted résumé."""
    excerpt = _clip(value, 500)
    if not excerpt:
        return ""
    normalized_resume = _clip(resume_text, len(resume_text) + 1)
    return excerpt if excerpt in normalized_resume else ""


def _experience_entries(value: object, resume_text: str, *, count: int = 5) -> list[dict]:
    if not isinstance(value, list):
        return []
    result: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        period = _plain_resume_field(item.get("period"))[:50]
        role = _clean_experience_role(item.get("role"))
        entry = {
            "name": _clean_experience_identity(
                item.get("name"), role=role, period=period
            ),
            "organization": _clean_experience_identity(
                item.get("organization"), role=role, period=period
            ),
            "role": role,
            "period": period,
            "summary": _clip(item.get("summary"), 180),
            "highlights": _string_list(item.get("highlights"), count=4, item_chars=100),
            "source_excerpt": _source_excerpt(item.get("source_excerpt"), resume_text),
        }
        normalized = {
            key: re.sub(r"\W+", "", str(entry.get(key) or "")).lower()
            for key in ("name", "organization", "role", "summary")
        }
        if normalized["role"] and normalized["role"] == normalized["name"]:
            entry["role"] = ""
            normalized["role"] = ""
        if normalized["summary"] and normalized["summary"] in {
            normalized["name"],
            normalized["organization"],
            normalized["role"],
        }:
            entry["summary"] = ""
        if any(entry.values()):
            result.append(entry)
        if len(result) >= count:
            break
    return result


def _parse_brief(raw: str, profile: CandidateProfile) -> CandidateBrief:
    text = str(raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("profile analysis must return a JSON object")
    return CandidateBrief(
        target_role=_clip(profile.target_role, 100),
        has_jd=bool(profile.jd_text.strip()),
        job_summary=(
            _clip(data.get("job_summary"), 360) if profile.jd_text.strip() else ""
        ),
        job_requirements=(
            _string_list(data.get("job_requirements"), count=8, item_chars=140)
            if profile.jd_text.strip()
            else []
        ),
        candidate_name=_clip(data.get("candidate_name"), 60),
        education=_clip(data.get("education"), 80),
        schools=_string_list(data.get("schools"), count=4, item_chars=100),
        candidate_summary=_clip(data.get("candidate_summary"), 420),
        skills=_string_list(data.get("skills"), count=18, item_chars=50),
        internships=_experience_entries(data.get("internships"), profile.resume_text),
        projects=_experience_entries(data.get("projects"), profile.resume_text),
        role_matches=_string_list(data.get("role_matches"), count=8, item_chars=120),
        verification_points=_string_list(
            data.get("verification_points"), count=8, item_chars=120
        ),
        question_focus=_string_list(data.get("question_focus"), count=8, item_chars=120),
    )


def brief_from_context(text: str) -> CandidateBrief:
    """Deserialize a public compact context (source excerpts are intentionally absent)."""
    data = json.loads(text)
    return CandidateBrief(
        target_role=str(data.get("target_role") or ""),
        has_jd=bool(data.get("has_jd")),
        job_summary=str(data.get("job_summary") or ""),
        job_requirements=list(data.get("job_requirements") or []),
        candidate_name=str(data.get("candidate_name") or ""),
        education=str(data.get("education") or ""),
        schools=list(data.get("schools") or []),
        candidate_summary=str(data.get("candidate_summary") or ""),
        skills=list(data.get("skills") or []),
        internships=list(data.get("internships") or []),
        projects=list(data.get("projects") or []),
        role_matches=list(data.get("role_matches") or []),
        verification_points=list(data.get("verification_points") or []),
        question_focus=list(data.get("question_focus") or []),
    )


def _significant_lines(text: str, *, count: int, item_chars: int) -> list[str]:
    chunks = re.split(r"[\r\n]+|(?<=[。；;])", text or "")
    result: list[str] = []
    for chunk in chunks:
        line = re.sub(r"^[\s\-*•·\d.、）)]+", "", chunk).strip()
        line = _clip(line, item_chars)
        if len(line) >= 4 and line not in result:
            result.append(line)
        if len(result) >= count:
            break
    return result


def _first_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text or "", re.IGNORECASE)
    return _clip(match.group(1), 100) if match else ""


def fallback_candidate_brief(profile: CandidateProfile) -> CandidateBrief:
    """Deterministic fallback: structure source lines without inventing facts."""
    resume_lines = _significant_lines(profile.resume_text, count=18, item_chars=300)
    jd_lines = (
        _significant_lines(profile.jd_text, count=8, item_chars=140)
        if profile.jd_text.strip()
        else []
    )
    if profile.jd_text.strip() and not jd_lines:
        jd_lines = [_clip(profile.jd_text, 140)]
    known_skills = (
        "Python", "Java", "Go", "C++", "JavaScript", "TypeScript", "React", "Vue",
        "SQL", "MySQL", "Redis", "Kafka", "Docker", "Kubernetes", "AWS", "数据分析",
        "需求分析", "用户研究", "项目管理", "产品设计", "跨团队协作",
    )
    skills = [skill for skill in known_skills if skill.lower() in profile.resume_text.lower()]
    name = _first_match(r"(?:姓名|Name)\s*[:：]\s*([^\n|]{2,30})", profile.resume_text)
    education = _first_match(
        r"((?:博士|硕士|本科|学士|大专)(?:学历|学位)?)", profile.resume_text
    )
    schools = [
        line
        for line in resume_lines
        if any(word in line for word in ("大学", "学院", "University", "College"))
    ][:4]

    raw_lines = [line.strip() for line in re.split(r"[\r\n]+", profile.resume_text) if line.strip()]
    section_aliases = {
        "internship": ("实习经历", "工作经历", "职业经历", "实习经验", "工作经验"),
        "project": ("项目经历", "项目经验", "代表项目", "项目实践"),
        "excluded": (
            "主要荣誉", "荣誉奖项", "获奖经历", "奖项荣誉", "专业技能",
            "技能清单", "教育经历", "教育背景", "自我评价", "个人总结",
        ),
    }

    def _heading_section(line: str) -> str | None:
        normalized = re.sub(r"[\s：:|/·•\-]", "", line)
        for section, aliases in section_aliases.items():
            if any(normalized == re.sub(r"\W", "", alias) for alias in aliases):
                return section
        return None

    sectioned_lines: list[tuple[int, str, str]] = []
    active_section = ""
    has_explicit_sections = False
    for line_index, line in enumerate(raw_lines):
        heading = _heading_section(line)
        if heading is not None:
            active_section = heading
            has_explicit_sections = True
            continue
        sectioned_lines.append((line_index, active_section, line))

    def _extract_entity(pattern: str, text: str) -> str:
        match = re.search(pattern, text, re.IGNORECASE)
        return _clip(match.group(1), 80) if match else ""

    def _fallback_entries(lines: list[tuple[int, str]], label: str) -> list[dict]:
        entries: list[dict] = []
        role_pattern = (
            r"((?:产品|项目|交互|体验|视觉|UI|UX|运营|市场|数据|研发|前端|后端|测试)?"
            r"(?:经理|实习生|设计师|工程师|分析师|负责人|助理))"
        )
        org_pattern = (
            r"([\w\u4e00-\u9fff（）()·&.\-]{2,48}(?:公司|科技|集团|工作室|研究院|银行|大学|学院))"
        )
        period_pattern = r"((?:19|20)\d{2}[./年\-]\d{1,2}[^\n]{0,20}(?:至今|现在|(?:19|20)\d{2}))"
        for index, (source_index, line) in enumerate(lines[:5], start=1):
            next_index = next(
                (value for value in all_candidate_indices if value > source_index),
                min(len(raw_lines), source_index + 4),
            )
            nearby = raw_lines[source_index:next_index] or [line]
            evidence = "；".join(nearby)
            organization = _extract_entity(org_pattern, evidence)
            role = _extract_entity(role_pattern, evidence)
            period = _extract_entity(period_pattern, evidence)
            if label == "实习" and not organization:
                identity = re.sub(role_pattern, "", line, flags=re.IGNORECASE)
                identity = re.sub(r"(?:19|20)\d{2}[^\s]{0,24}", "", identity)
                identity = re.sub(r"[|｜/·•\-]+", " ", identity).strip()
                if 2 <= len(identity) <= 48:
                    organization = _clip(identity, 80)
            name = ""
            if label == "项目":
                name = re.sub(r"^(?:项目名称|项目)\s*[:：]?\s*", "", line).strip()
                name = _clip(name, 80)
            responsibility_lines = [
                item
                for item in nearby
                if any(word in item for word in ("负责", "主导", "参与", "推进", "设计", "产出", "优化"))
            ]
            summary_text = "；".join(responsibility_lines) or line
            entries.append(
                {
                    "name": name or f"{label} {index}",
                    "organization": organization,
                    "role": role,
                    "period": period,
                    "summary": _clip(summary_text, 220),
                    "highlights": [],
                    "source_excerpt": _clip(evidence, 500),
                }
            )
        return entries

    if has_explicit_sections:
        internship_lines = [
            (index, line)
            for index, section, line in sectioned_lines
            if section == "internship"
            and (
                "实习" in line
                or re.search(r"(?:公司|科技|集团|工作室|研究院|银行)", line)
                or re.search(
                    r"(?:产品|项目|交互|体验|视觉|UI|UX|运营|市场|数据|研发|前端|后端|测试)"
                    r"(?:经理|实习生|设计师|工程师|分析师|负责人|助理)",
                    line,
                    re.IGNORECASE,
                )
            )
        ]
        project_lines = [
            (index, line)
            for index, section, line in sectioned_lines
            if section == "project"
            and not any(alias in line for alias in section_aliases["excluded"])
            and (
                "项目" in line
                or not any(word in line for word in ("负责", "主导", "参与", "推进"))
            )
        ]
    else:
        # Without headings, stay conservative: only explicit internship/project
        # identity lines can create entries. Responsibility verbs alone are never
        # enough (otherwise "主要荣誉" and skill bullets become fake projects).
        internship_lines = [
            (index, line)
            for index, _, line in sectioned_lines
            if "实习" in line and "主要荣誉" not in line
        ]
        project_lines = [
            (index, line)
            for index, _, line in sectioned_lines
            if "项目" in line
            and not any(alias in line for alias in section_aliases["excluded"])
        ]
    all_candidate_indices = sorted(
        {index for index, _ in internship_lines + project_lines}
        | {
            index
            for index, line in enumerate(raw_lines)
            if _heading_section(line) is not None
        }
    )
    summary = "；".join(resume_lines[:3])[:420]
    internships = _fallback_entries(internship_lines, "实习")
    projects = _fallback_entries(project_lines, "项目")
    focus = [
        str(item.get("summary") or "")
        for item in (internships + projects)[:4]
        if item.get("summary")
    ]
    return CandidateBrief(
        target_role=_clip(profile.target_role, 100),
        has_jd=bool(profile.jd_text.strip()),
        job_summary="；".join(jd_lines[:3])[:360],
        job_requirements=jd_lines,
        candidate_name=name,
        education=education,
        schools=schools,
        candidate_summary=summary,
        skills=skills[:18],
        internships=internships,
        projects=projects,
        role_matches=[],
        verification_points=[],
        question_focus=focus,
    )


async def analyze_candidate_profile(
    profile: CandidateProfile,
    llm_client=None,
    *,
    timeout_seconds: float = ANALYSIS_TIMEOUT_SECONDS,
) -> CandidateBrief:
    if llm_client is None:
        return fallback_candidate_brief(profile)
    prompt = (
        "请一次性分析下面的岗位信息和候选人简历，只输出 JSON。\n"
        "岗位信息只需轻量精炼，保留真实职责、要求和关键词，不要扩写。\n"
        "候选人必须结构化为姓名、学历、学校、技能、实习经历和项目经历。\n"
        "每段实习必须分别保留 organization=公司名称、role=岗位名称、summary=具体负责事项；"
        "每个项目必须分别保留 name=项目名称、role=候选人在项目中的角色、summary=具体负责事项。\n"
        "公司名、岗位名和项目名不得只写在 summary 中；原文明确出现时对应字段不得为空。\n"
        "每段实习/项目的 source_excerpt 必须逐字复制简历中对应的连续原文，"
        "用于之后针对该经历出题；不得改写或虚构。没有的信息留空。\n"
        "没有 JD 时 job_summary 为空且 job_requirements 必须是空数组。\n"
        f"目标岗位：{profile.target_role or '（未提供）'}\n"
        f"岗位 JD：{profile.jd_text or '（未提供）'}\n"
        f"候选人简历：{profile.resume_text or '（未提供）'}\n\n"
        "JSON 字段固定为：\n"
        '{"job_summary":"","job_requirements":[],"candidate_name":"",'
        '"education":"","schools":[],"candidate_summary":"","skills":[],'
        '"internships":[{"name":"","organization":"","role":"","period":"",'
        '"summary":"","highlights":[],"source_excerpt":""}],'
        '"projects":[{"name":"","organization":"","role":"","period":"",'
        '"summary":"","highlights":[],"source_excerpt":""}],'
        '"role_matches":[],"verification_points":[],"question_focus":[]}'
    )
    try:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                generate_json = getattr(llm_client, "generate_json_once", None)
                if callable(generate_json):
                    raw = await asyncio.wait_for(
                        generate_json(prompt, max_tokens=1800, temperature=0.1),
                        timeout=max(0.05, float(timeout_seconds) / 3),
                    )
                else:
                    raw = await asyncio.wait_for(
                        llm_client.generate_once(prompt, max_tokens=1800),
                        timeout=max(0.05, float(timeout_seconds) / 3),
                    )
                return _parse_brief(raw, profile)
            except Exception as exc:  # noqa: BLE001 - retry structured extraction
                last_error = exc
                logger.warning(
                    "candidate profile analysis attempt %s/3 failed: %s",
                    attempt + 1,
                    exc,
                )
                if attempt < 2:
                    await asyncio.sleep(0.4 * (attempt + 1))
        raise RuntimeError(f"candidate profile analysis failed after 3 attempts: {last_error}")
    except Exception as exc:  # noqa: BLE001 - analysis failure must not block start
        logger.warning("candidate profile analysis fallback: %s", exc)
        return fallback_candidate_brief(profile)
