"""One-shot company-knowledge relevance selection and compression."""

from __future__ import annotations

import asyncio
import json
import re

from interview.interview_manager import InterviewManager
from interview.profile import CandidateProfile


KNOWLEDGE_TIMEOUT_SECONDS = 8.0


def _extract_json(text: str) -> dict:
    value = str(text or "").strip()
    value = re.sub(r"^```(?:json)?\s*", "", value)
    value = re.sub(r"\s*```$", "", value)
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("company knowledge response is not JSON")
    data = json.loads(value[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("company knowledge response must be an object")
    return data


async def prepare_company_knowledge(
    config_path,
    profile: CandidateProfile,
    llm_client=None,
) -> tuple[str, str]:
    """Return (interview, internal) compressed text.

    Raw entries appear only in this preparation call. All later components receive
    these two bounded strings. Any timeout/parse failure falls back to deterministic
    keyword selection in InterviewManager.
    """
    manager = InterviewManager(config_path)
    manager.apply_candidate_profile(profile)
    fallback_public = manager.persona_context().get("company_knowledge", "")
    fallback_internal = manager.persona_context(
        include_internal=True
    ).get("company_internal_knowledge", "")
    entries = [
        {
            "title": entry.title,
            "category": entry.category,
            "content": entry.content,
            "visibility": entry.visibility,
        }
        for entry in manager.config.company_knowledge.entries
        if entry.enabled and entry.content.strip()
    ]
    if llm_client is None or not entries:
        return fallback_public, fallback_internal

    prompt = (
        "请按目标岗位/JD筛选并忠实压缩公司知识，只输出 JSON。不得补充原文不存在的事实。\n"
        "interview 只可使用 visibility=interview 的条目，供业务出题/追问/评估；"
        "internal 只可使用 visibility=internal 的条目，仅供内部评估，禁止写入 interview。\n"
        "每条保留标题、关键事实和与岗位相关的业务/技术约束，删除冗余措辞。\n"
        f"interview 不超过 {manager.config.company_knowledge.max_interview_chars} 字，"
        f"internal 不超过 {manager.config.company_knowledge.max_internal_chars} 字。\n"
        '输出：{"interview":"...","internal":"..."}\n'
        f"目标岗位：{profile.target_role}\n岗位 JD：{profile.jd_text or '未提供'}\n"
        f"公司知识：{json.dumps(entries, ensure_ascii=False)}"
    )
    try:
        raw = await asyncio.wait_for(
            llm_client.generate_once(prompt, max_tokens=1400),
            timeout=KNOWLEDGE_TIMEOUT_SECONDS,
        )
        data = _extract_json(raw)
        public = str(data.get("interview") or "").strip()[
            : manager.config.company_knowledge.max_interview_chars
        ]
        internal = str(data.get("internal") or "").strip()[
            : manager.config.company_knowledge.max_internal_chars
        ]
        return public or fallback_public, internal or fallback_internal
    except Exception:
        return fallback_public, fallback_internal
