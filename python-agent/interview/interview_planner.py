"""Session-start interview planner.

Turns the candidate's résumé + the job JD (+ any existing question bank) into the
actual ordered question list for one interview:

    [ 自我介绍 ] + [ N 段简历经历深挖 ] + [ M 道业务题 ]

Résumé-experience questions are generated from the résumé, picking the experiences
most relevant to the JD. Business questions are drawn from the bank ranked by JD
relevance, or generated from the JD when a position has no bank.

The plan is a plain ``list[QuestionSpec]`` — the existing state machine / QuestionPlanner
consume it unchanged; per-stage follow-up budgets ride on each slot's ``max_followups``.

Robust by design: if the LLM call fails, the résumé is empty, or the bank is empty,
a deterministic rule-based fallback still produces a sensible plan.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from time import perf_counter

from interview import prompts as prompt_defaults
from interview.models import QuestionSpec
from interview.prompts import render_template

logger = logging.getLogger(__name__)
MAX_QUESTION_CHARS = 160
MIN_PLAN_REQUEST_TIMEOUT_SECONDS = 15.0
MAX_PLAN_REQUEST_TIMEOUT_SECONDS = 35.0


class InterviewPlanningError(RuntimeError):
    """Raised when a strict (enterprise) interview cannot get a valid AI plan."""


def _extract_json(raw: str) -> str:
    """Pull the outer JSON object out of the reply, tolerating ``` fences or preamble."""
    start = raw.find("{")
    end = raw.rfind("}")
    return raw[start : end + 1] if start != -1 and end > start else raw


def _normalize_question_text(text: str) -> str:
    out = " ".join((text or "").split()).strip()
    if not out:
        return out
    if len(out) > MAX_QUESTION_CHARS:
        return ""
    first_marks = [i for i in (out.find("？"), out.find("?")) if i >= 0]
    if not first_marks:
        return out.rstrip("。！!；;") + "？"
    first = min(first_marks)
    return (out[:first].rstrip("？?") + "？").strip()


def _safe_question(text: str, fallback: str) -> str:
    return _normalize_question_text(text) or _normalize_question_text(fallback)


def _source_key(text: str) -> str:
    return re.sub(r"[\W_]+", "", str(text or "")).lower()


def _plain_identity(value: object) -> str:
    text = re.sub(r"[`*_#]+", "", str(value or ""))
    text = re.sub(r"[|｜\t]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -—–·•,，;；/｜|")


def _spoken_identity(value: object, *, role: str = "", period: str = "") -> str:
    """Defensively collapse a résumé table row into a speakable identity."""
    text = _plain_identity(value)
    if period:
        text = text.replace(_plain_identity(period), " ")
    text = re.sub(
        r"(?:19|20)\d{2}[./年\-]\d{1,2}\s*(?:至|到|[-—–~～])\s*"
        r"(?:(?:19|20)\d{2}[./年\-]\d{1,2}|至今|现在)",
        " ",
        text,
    )
    if role:
        text = re.sub(re.escape(role), " ", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?<!\S)[A-Za-z\u4e00-\u9fff]{1,16}(?:事业部|部门|中心|团队|小组|组)(?!\S)",
        " ",
        text,
    )
    return re.sub(r"\s+", " ", text).strip(" -—–·•,，;；/｜|")[:48]


def _experience_catalog(candidate_brief: str) -> list[dict[str, str]]:
    """Build stable, prompt-safe references from the structured profile."""
    try:
        data = json.loads(candidate_brief or "{}")
    except Exception:
        data = {}
    catalog: list[dict[str, str]] = []
    for kind, key, section_title in (
        ("internship", "internships", "实习经历"),
        ("project", "projects", "项目经历"),
    ):
        for index, item in enumerate(data.get(key) or [], start=1):
            if not isinstance(item, dict):
                continue
            role = _plain_identity(item.get("role"))[:40]
            period = _plain_identity(item.get("period"))[:50]
            name = _spoken_identity(item.get("name"), role=role, period=period)
            organization = _spoken_identity(
                item.get("organization"), role=role, period=period
            )
            summary = " ".join(str(item.get("summary") or "").split())[:180]
            identity_parts: list[str] = []
            for value in (organization, name, role):
                if (
                    value
                    and value not in identity_parts
                    and not re.fullmatch(r"(?:实习|项目)\s*\d+", value)
                ):
                    identity_parts.append(value)
            normalized = {_source_key(value) for value in identity_parts if value}
            if not identity_parts or len(normalized) != len(identity_parts):
                continue
            if summary and _source_key(summary) in normalized:
                continue
            spoken_reference = organization if kind == "internship" else name
            # A role such as “产品实习生” is not an experience identity. If the
            # company/project name cannot be recovered, let the LLM repair from
            # the full résumé instead of speaking “实习1” or a job title.
            if not spoken_reference or re.fullmatch(
                r"(?:第?[一二三四五六七八九十\d]+段?)?(?:实习|项目|经历)\s*\d*",
                spoken_reference,
            ):
                continue
            catalog.append(
                {
                    "id": f"{kind}_{index}",
                    # Keep the full identity in planning context, but only speak
                    # the company (or, when absent, the project name). Repeating
                    # company + role + long project titles makes questions sound
                    # robotic and needlessly verbose.
                    "reference": spoken_reference[:48],
                    "role": role,
                    "full_reference": " / ".join(identity_parts)[:120],
                    "summary": summary,
                    "section_title": section_title,
                }
            )
    return catalog


def _reference_from_resume(value: object, resume_text: str) -> str:
    reference = " ".join(str(value or "").split()).strip("《》「」[]【】")[:120]
    if not reference or not resume_text.strip():
        return ""
    key = _source_key(reference)
    if re.fullmatch(
        r"(?:第?[一二三四五六七八九十\d]+段?)?(?:实习|项目|经历)\d*",
        key,
    ):
        return ""
    if re.fullmatch(
        r"(?:产品|项目|交互|体验|视觉|ui|ux|运营|市场|数据|研发|前端|后端|测试)?"
        r"(?:经理|实习生|设计师|工程师|分析师|负责人|助理)",
        key,
        re.IGNORECASE,
    ):
        return ""
    if not key or key in {
        "一段经历",
        "相关经历",
        "某段经历",
        "一个项目",
        "某个项目",
        "简历经历",
        "项目经历",
        "实习经历",
        "产品经理",
        "设计师",
        "工程师",
        "实习1",
        "实习2",
        "项目1",
        "项目2",
    }:
        return ""
    resume_key = _source_key(resume_text)
    concise = next(
        (
            item.strip()
            for item in re.split(r"[/｜|·,，;；]+", reference)
            if item.strip()
        ),
        reference,
    )[:48]
    if key in resume_key:
        return concise
    terms = [
        _source_key(item)
        for item in re.split(r"[/｜|·,，;；\-]+", reference)
        if len(_source_key(item)) >= 2
    ]
    matched = [term for term in terms if term in resume_key]
    return concise if len(matched) >= min(2, len(terms)) and matched else ""


def _question_with_reference(prompt: str, reference: str, role: str = "") -> str:
    normalized = _normalize_question_text(re.sub(r"[`*_#]+", "", prompt or ""))
    if not normalized:
        normalized = "请具体介绍你当时的职责、关键动作和结果？"
    natural_role = _plain_identity(role)[:40]
    prefix = (
        f"你在{reference[:48]}担任{natural_role}时，"
        if natural_role
        else f"你在{reference[:48]}时，"
    )
    # Never trust the model's spoken identity. It may paste a complete PDF table
    # row (company + department + role + dates + Markdown). Remove that context
    # and add our own concise, deterministic spoken prefix.
    body = normalized
    body = re.sub(
        r"(?:19|20)\d{2}[./年\-]\d{1,2}\s*(?:至|到|[-—–~～])\s*"
        r"(?:(?:19|20)\d{2}[./年\-]\d{1,2}|至今|现在)",
        " ",
        body,
    )
    body = re.sub(
        r"(?<!\S)[A-Za-z\u4e00-\u9fff]{1,16}(?:事业部|部门|中心|团队|小组|组)(?!\S)",
        " ",
        body,
    )
    if reference:
        body = re.sub(re.escape(reference), " ", body, flags=re.IGNORECASE)
    if natural_role:
        body = re.sub(re.escape(natural_role), " ", body, flags=re.IGNORECASE)
    body = re.sub(r"[「」『』“”\"']", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    body = re.sub(
        r"^(?:请)?(?:结合|围绕|针对|关于)?\s*(?:这段|该段|上述)?"
        r"(?:经历|实习|项目|工作)?\s*[，,:：；;]*\s*",
        "",
        body,
    )
    body = re.sub(
        r"^你在\s*(?:担任\s*)?(?:时|期间|过程中|项目中|工作中)\s*[，,]?\s*",
        "",
        body,
    )
    body = body.strip(" ，,：:；;？?") or "请具体介绍你当时的职责、关键动作和结果"
    available = max(20, MAX_QUESTION_CHARS - len(prefix) - 1)
    return _normalize_question_text(prefix + body[:available] + "？")


class InterviewPlanner:
    def __init__(
        self,
        llm_client=None,
        *,
        prompt_template: str | None = None,
        context: dict | None = None,
        self_intro_prompt: str | None = None,
        resume_experiences: int = prompt_defaults.DEFAULT_RESUME_EXPERIENCES,
        business_questions: int = prompt_defaults.DEFAULT_BUSINESS_QUESTIONS,
        resume_followups: int = prompt_defaults.DEFAULT_RESUME_FOLLOWUPS,
        business_followups: int = prompt_defaults.DEFAULT_BUSINESS_FOLLOWUPS,
        self_intro_followups: int = prompt_defaults.DEFAULT_SELF_INTRO_FOLLOWUPS,
        self_intro_followups_no_resume: int = (
            prompt_defaults.DEFAULT_SELF_INTRO_FOLLOWUPS_NO_RESUME
        ),
        llm_timeout_seconds: float = 90.0,
        allow_fallback: bool = True,
    ) -> None:
        self._llm = llm_client
        self._prompt_template = prompt_template or prompt_defaults.DEFAULT_PLANNER_PROMPT
        self._context = dict(context or {})
        self._self_intro_prompt = (
            str(self_intro_prompt).strip()
            if self_intro_prompt is not None and str(self_intro_prompt).strip()
            else prompt_defaults.DEFAULT_SELF_INTRO_PROMPT
        )
        self._resume_experiences = max(0, resume_experiences)
        self._business_questions = max(0, business_questions)
        self._resume_followups = resume_followups
        self._business_followups = business_followups
        self._self_intro_followups = self_intro_followups
        self._self_intro_followups_no_resume = self_intro_followups_no_resume
        self._llm_timeout_seconds = max(0.1, float(llm_timeout_seconds))
        self._allow_fallback = bool(allow_fallback)

    def _llm_label(self) -> str:
        """Return a log/user-safe endpoint label without exposing credentials."""
        model = str(getattr(self._llm, "_model", "") or "").strip()
        base_url = str(getattr(self._llm, "_base_url", "") or "").lower()
        if "volces.com" in base_url or "volcengine" in base_url or "ark." in base_url:
            provider = "火山方舟"
        elif "deepseek" in base_url:
            provider = "DeepSeek"
        else:
            provider = self._llm.__class__.__name__ if self._llm is not None else "LLM"
        return f"{provider}/{model}" if model else provider

    @staticmethod
    def _error_text(exc: BaseException) -> str:
        return str(exc).strip() or exc.__class__.__name__

    @staticmethod
    def _plan_request_timeout_seconds(prompt: str) -> float:
        """Give long résumé/JD prompts enough time without waiting indefinitely."""
        timeout = 12.0 + len(prompt or "") / 4000.0
        return min(
            MAX_PLAN_REQUEST_TIMEOUT_SECONDS,
            max(MIN_PLAN_REQUEST_TIMEOUT_SECONDS, timeout),
        )

    async def build_plan(
        self,
        *,
        candidate_brief: str = "",
        has_resume: bool = False,
        target_role: str = "",
        bank: list[QuestionSpec] | None = None,
        core_competencies: str = "",
        jd_text: str = "",
        resume_text: str = "",
    ) -> list[QuestionSpec]:
        bank = bank or []
        plan: list[QuestionSpec] = [self._self_intro_slot(has_resume)]
        require_source_grounded_plan = bool(
            resume_text.strip() or jd_text.strip()
        )

        generated: list[QuestionSpec] | None = None
        if self._llm is not None:
            try:
                generated = await asyncio.wait_for(
                    self._llm_slots(
                        candidate_brief,
                        target_role,
                        bank,
                        core_competencies,
                        jd_text,
                        resume_text,
                    ),
                    timeout=self._llm_timeout_seconds,
                )
            except TimeoutError:
                logger.warning(
                    "interview planner LLM timed out after %.1fs (%s)",
                    self._llm_timeout_seconds,
                    self._llm_label(),
                )
                if require_source_grounded_plan or not self._allow_fallback:
                    raise InterviewPlanningError(
                        "AI 面试题目规划超时，请重试；"
                        f"{self._llm_label()} 在 {self._llm_timeout_seconds:.0f} 秒内未完成，"
                        "本场不会使用空泛兜底题"
                    )
            except InterviewPlanningError:
                if require_source_grounded_plan or not self._allow_fallback:
                    raise
            except Exception as exc:  # noqa: BLE001 — any LLM/parse failure → fallback
                logger.warning("interview planner LLM failed, using fallback: %s", exc)
                if require_source_grounded_plan or not self._allow_fallback:
                    raise InterviewPlanningError(
                        f"AI 面试题目规划失败，请重试；本场不会使用空泛兜底题：{exc}"
                    ) from exc
        elif require_source_grounded_plan or not self._allow_fallback:
            raise InterviewPlanningError("AI 面试题目规划模型未配置")

        plan += (
            generated
            if generated is not None
            else self._fallback_slots(
                has_resume,
                bank,
                candidate_brief=candidate_brief,
                target_role=target_role,
                resume_text=resume_text,
            )
        )
        plan = self._finalize_plan(
            plan,
            bank=bank,
            target_role=target_role,
            require_named_resume=bool(resume_text.strip()),
        )
        logger.info(
            "interview plan: source=%s slots=%d questions=%s",
            "ai" if generated is not None else "local_fallback",
            len(plan),
            " | ".join(
                f"{s.section_title}[{s.source_reference or '-'}]:{s.prompt}"
                for s in plan
            ),
        )
        return plan

    @staticmethod
    def _question_key(text: str) -> str:
        return re.sub(r"[\W_]+", "", str(text or "")).lower()

    def _finalize_plan(
        self,
        plan: list[QuestionSpec],
        *,
        bank: list[QuestionSpec],
        target_role: str,
        require_named_resume: bool = False,
    ) -> list[QuestionSpec]:
        """Publish only a diverse plan with enough role-relevant coverage."""
        unique: list[QuestionSpec] = []
        seen: set[str] = set()
        for slot in plan:
            normalized = _normalize_question_text(slot.prompt)
            key = self._question_key(normalized)
            if not normalized or not key or key in seen:
                continue
            slot.prompt = normalized
            seen.add(key)
            unique.append(slot)

        non_intro = [item for item in unique if item.section_id != "self_intro"]
        needs_fill = len(non_intro) < 2 or not any(
            item.section_id == "business" for item in non_intro
        )
        if self._allow_fallback and needs_fill:
            fill_candidates = self._fallback_business(
                bank, target_role=target_role
            )
            # A short bank must not prevent the three distinct role-safe questions
            # from being considered as quality-preserving fill candidates.
            for index, prompt in enumerate(
                self._role_safe_business_questions(target_role), start=1
            ):
                fill_candidates.append(
                    QuestionSpec(
                        section_id="business",
                        section_title="业务题",
                        question_id=f"business_safe_{index}",
                        prompt=prompt,
                        max_followups=self._business_followups,
                    )
                )
            for slot in fill_candidates:
                key = self._question_key(slot.prompt)
                if not key or key in seen:
                    continue
                unique.append(slot)
                seen.add(key)
                non_intro = [item for item in unique if item.section_id != "self_intro"]
                if len(non_intro) >= 2 and any(
                    item.section_id == "business" for item in non_intro
                ):
                    break

        non_intro = [item for item in unique if item.section_id != "self_intro"]
        if require_named_resume and self._resume_experiences > 0:
            resume_slots = [
                item
                for item in unique
                if item.section_id in {"resume_project_intro", "resume_experience"}
            ]
            if not resume_slots or any(not item.source_reference for item in resume_slots):
                raise InterviewPlanningError(
                    "未能从简历中可靠定位具体公司、岗位或项目，请重新生成题目"
                )
        if len(non_intro) < 2 or not any(
            item.section_id == "business" for item in non_intro
        ):
            raise InterviewPlanningError(
                "题目规划未达到最低质量要求，请重试"
            )
        return unique

    # ---- slots ----------------------------------------------------------- #

    def _self_intro_slot(self, has_resume: bool) -> QuestionSpec:
        return QuestionSpec(
            section_id="self_intro",
            section_title="自我介绍",
            question_id="self_intro",
            prompt=_safe_question(
                self._self_intro_prompt,
                "请你做一个简短的自我介绍，重点讲最近负责的方向和最有代表性的经历？",
            ),
            competency="",
            # No résumé → probe the self-intro harder, that's where experience surfaces.
            max_followups=(
                self._self_intro_followups_no_resume
                if not has_resume
                else self._self_intro_followups
            ),
        )

    async def _llm_slots(
        self,
        candidate_brief: str,
        target_role: str,
        bank: list[QuestionSpec],
        core_competencies: str = "",
        jd_text: str = "",
        resume_text: str = "",
    ) -> list[QuestionSpec]:
        bank_brief = [
            {
                "id": q.question_id,
                "title": q.section_title,
                "prompt": q.prompt,
                "competency": q.competency,
            }
            for q in bank
        ]
        catalog = _experience_catalog(candidate_brief)
        source_material = (
            "【岗位 JD 原文】\n"
            + (jd_text or "（未提供）")
            + "\n\n【候选人简历原文】\n"
            + (resume_text or "（未提供）")
            + "\n\n【可引用经历目录】\n"
            + json.dumps(catalog, ensure_ascii=False)
        )
        prompt = render_template(
            self._prompt_template,
            {
                **self._context,
                "candidate_brief": candidate_brief or "{}",
                "target_role": target_role or "候选人",
                "core_competencies": core_competencies or "（未指定核心考察点）",
                "resume_experiences": self._resume_experiences,
                "business_questions": self._business_questions,
                "business_questionlist": json.dumps(bank_brief, ensure_ascii=False),
                "source_material": source_material,
            },
        )
        if "{source_material}" not in self._prompt_template:
            prompt += "\n\n" + source_material
        raw = await self._request_plan_json(prompt)
        data = json.loads(_extract_json(raw))

        if resume_text.strip() and self._resume_experiences > 0:
            invalid_refs = self._invalid_resume_references(
                data.get("resumeQuestions") or [], catalog, resume_text
            )
            if invalid_refs:
                repair_prompt = (
                    prompt
                    + "\n\n上一次 JSON 的简历问题没有明确绑定真实经历。"
                    + "请保留正确业务题，只修复 resumeQuestions。每项必须填写有效 experienceId，"
                    + "或在 experienceRef 中逐字引用简历里的公司、岗位或项目名称；"
                    + "每道问题必须直接点名该经历。\n"
                    + "校验错误："
                    + "；".join(invalid_refs[:6])
                    + "\n上一次 JSON：\n"
                    + raw[:12000]
                )
                raw = await self._request_plan_json(repair_prompt)
                data = json.loads(_extract_json(raw))

        slots: list[QuestionSpec] = []
        for i, item in enumerate(
            (data.get("resumeQuestions") or [])[: self._resume_experiences]
        ):
            reference, source_role = self._resolve_experience_context(
                item, catalog, resume_text
            )
            if not reference and resume_text.strip():
                continue
            slots += self._resume_slots(
                i,
                item,
                source_reference=reference,
                source_role=source_role,
            )
        if resume_text.strip() and self._resume_experiences > 0 and not slots:
            if not self._allow_fallback:
                raise InterviewPlanningError(
                    "企业面试题目未能绑定候选人的具体简历经历"
                )
            slots += self._fallback_resume_slots(
                candidate_brief, target_role, resume_text
            )
        bank_by_id = {q.question_id: q for q in bank}
        for i, item in enumerate(
            (data.get("businessQuestions") or [])[: self._business_questions]
        ):
            if not isinstance(item, dict):
                if not self._allow_fallback:
                    raise InterviewPlanningError("企业面试题目规划返回了无效业务题")
                item = {}
            ref = str(item.get("bankId") or "")
            generated_prompt = _normalize_question_text(
                str(item.get("prompt") or item.get("question") or "")
            )
            if (
                not self._allow_fallback
                and not (ref and ref in bank_by_id)
                and not generated_prompt
            ):
                raise InterviewPlanningError("企业面试题目规划返回了过长或无效问题")
            slots.append(self._business_slot(i, item, bank_by_id))

        # The LLM produced no usable business questions — never leave the plan empty.
        if not any(s.section_id == "business" for s in slots):
            if not self._allow_fallback:
                raise InterviewPlanningError("企业面试题目规划结果缺少有效业务题")
            if not bank and (resume_text.strip() or jd_text.strip()):
                raise InterviewPlanningError(
                    "AI 面试题目规划结果缺少岗位业务题"
                )
            slots += self._fallback_business(bank, target_role=target_role)
        return slots

    @classmethod
    def _resolve_experience_reference(
        cls,
        item: object,
        catalog: list[dict[str, str]],
        resume_text: str,
    ) -> str:
        return cls._resolve_experience_context(item, catalog, resume_text)[0]

    @staticmethod
    def _resolve_experience_context(
        item: object,
        catalog: list[dict[str, str]],
        resume_text: str,
    ) -> tuple[str, str]:
        if not isinstance(item, dict):
            return "", ""
        by_id = {entry["id"]: entry for entry in catalog}
        experience_id = str(item.get("experienceId") or "").strip()
        if experience_id in by_id:
            entry = by_id[experience_id]
            return entry["reference"], str(entry.get("role") or "")
        return _reference_from_resume(item.get("experienceRef"), resume_text), ""

    @classmethod
    def _invalid_resume_references(
        cls,
        items: object,
        catalog: list[dict[str, str]],
        resume_text: str,
    ) -> list[str]:
        if not isinstance(items, list) or not items:
            return ["缺少 resumeQuestions"]
        errors: list[str] = []
        for index, item in enumerate(items, start=1):
            if not cls._resolve_experience_reference(item, catalog, resume_text):
                errors.append(f"第 {index} 段经历缺少有效 experienceId/experienceRef")
        return errors

    async def _request_plan_json(self, prompt: str) -> str:
        last_error: Exception | None = None
        request_timeout = self._plan_request_timeout_seconds(prompt)
        llm_label = self._llm_label()
        for attempt in range(3):
            started = perf_counter()
            try:
                generate_json = getattr(self._llm, "generate_json_once", None)
                if callable(generate_json):
                    result = await asyncio.wait_for(
                        generate_json(prompt, max_tokens=2400, temperature=0.15),
                        timeout=request_timeout,
                    )
                else:
                    result = await asyncio.wait_for(
                        self._llm.generate_once(prompt, max_tokens=2400),
                        timeout=request_timeout,
                    )
                logger.info(
                    "interview planner request succeeded (attempt %s/3, %s, prompt_chars=%s, elapsed=%.2fs)",
                    attempt + 1,
                    llm_label,
                    len(prompt),
                    perf_counter() - started,
                )
                return result
            except TimeoutError as exc:
                last_error = TimeoutError(
                    f"{llm_label} 单次请求超过 {request_timeout:.0f} 秒"
                )
                logger.warning(
                    "interview planner attempt %s/3 timed out (%s, prompt_chars=%s, elapsed=%.2fs)",
                    attempt + 1,
                    llm_label,
                    len(prompt),
                    perf_counter() - started,
                )
            except Exception as exc:  # noqa: BLE001 - retry structured planning
                last_error = exc
                logger.warning(
                    "interview planner attempt %s/3 failed (%s, prompt_chars=%s, elapsed=%.2fs): %s",
                    attempt + 1,
                    llm_label,
                    len(prompt),
                    perf_counter() - started,
                    self._error_text(exc),
                )
            if attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))
        detail = self._error_text(last_error or RuntimeError("未知错误"))
        raise RuntimeError(
            f"{llm_label} 题目规划连续 3 次失败：{detail}"
        ) from last_error

    def _resume_slots(
        self,
        index: int,
        item: object,
        *,
        source_reference: str = "",
        source_role: str = "",
    ) -> list[QuestionSpec]:
        if not isinstance(item, dict):
            return [
                self._resume_slot(
                    index,
                    {},
                    source_reference=source_reference,
                    source_role=source_role,
                )
            ]
        project_intro = item.get("projectIntro")
        core_questions = item.get("coreQuestions")
        if isinstance(project_intro, dict) and isinstance(core_questions, list):
            intro_prompt = _question_with_reference(_safe_question(
                str(project_intro.get("prompt") or ""),
                "请你选择一段与目标岗位最相关的项目，介绍背景、目标、个人职责和结果？",
            ), source_reference, source_role)
            intro = QuestionSpec(
                section_id="resume_project_intro",
                section_title="项目介绍",
                question_id=f"resume_intro_{index + 1}",
                prompt=intro_prompt,
                competency=str(project_intro.get("competency") or ""),
                expected_signals=[
                    str(s) for s in project_intro.get("expectedSignals") or []
                ],
                max_followups=self._resume_followups,
                source_reference=source_reference,
            )
            slots: list[QuestionSpec] = [intro]
            for j, q in enumerate(core_questions[:2]):
                if not isinstance(q, dict):
                    continue
                prompt = _question_with_reference(
                    str(q.get("prompt") or ""), source_reference, source_role
                )
                if not prompt:
                    continue
                slots.append(
                    QuestionSpec(
                        section_id="resume_experience",
                        section_title="简历经历",
                        question_id=f"resume_{index + 1}_{j + 1}",
                        prompt=prompt,
                        competency=str(q.get("competency") or ""),
                        expected_signals=[str(s) for s in q.get("expectedSignals") or []],
                        max_followups=self._resume_followups,
                        source_reference=source_reference,
                    )
                )
            if len(slots) > 1:
                return slots
        return [
            self._resume_slot(
                index,
                item,
                source_reference=source_reference,
                source_role=source_role,
            )
        ]

    def _resume_slot(
        self,
        index: int,
        item: dict,
        *,
        source_reference: str = "",
        source_role: str = "",
    ) -> QuestionSpec:
        prompt = _question_with_reference(_safe_question(
            str(item.get("prompt") or item.get("question") or ""),
            "请从简历中选择一段与目标岗位最相关的经历，具体说明你的职责、关键动作和结果？",
        ), source_reference, source_role) if source_reference else _safe_question(
            str(item.get("prompt") or item.get("question") or ""),
            "请从简历中选择一段与目标岗位最相关的经历，具体说明你的职责、关键动作和结果？",
        )
        return QuestionSpec(
            section_id="resume_experience",
            section_title="简历经历",
            question_id=f"resume_{index + 1}",
            prompt=prompt,
            competency=str(item.get("competency") or ""),
            expected_signals=[str(s) for s in item.get("expectedSignals") or []],
            max_followups=self._resume_followups,
            source_reference=source_reference,
        )

    def _business_slot(
        self, index: int, item: dict, bank_by_id: dict[str, QuestionSpec]
    ) -> QuestionSpec:
        ref = str(item.get("bankId") or "")
        if ref and ref in bank_by_id:
            source = bank_by_id[ref]
            fallback = self._role_safe_business_questions(
                str(self._context.get("target_role") or "目标岗位")
            )[index % 3]
            return QuestionSpec(
                section_id="business",
                section_title="业务题",
                question_id=f"business_{index + 1}",
                prompt=_safe_question(source.prompt, fallback),
                competency=source.competency,
                expected_signals=list(source.expected_signals),
                red_flags=list(source.red_flags),
                max_followups=self._business_followups,
            )
        fallback = self._role_safe_business_questions(
            str(self._context.get("target_role") or "目标岗位")
        )[index % 3]
        prompt = _safe_question(
            str(item.get("prompt") or item.get("question") or ""), fallback
        )
        return QuestionSpec(
            section_id="business",
            section_title="业务题",
            question_id=f"business_{index + 1}",
            prompt=prompt,
            competency=str(item.get("competency") or ""),
            expected_signals=[str(s) for s in item.get("expectedSignals") or []],
            max_followups=self._business_followups,
        )

    # ---- fallbacks ------------------------------------------------------- #

    def _fallback_slots(
        self,
        has_resume: bool,
        bank: list[QuestionSpec],
        *,
        candidate_brief: str = "",
        target_role: str = "",
        resume_text: str = "",
    ) -> list[QuestionSpec]:
        slots: list[QuestionSpec] = []
        if has_resume:
            slots.extend(
                self._fallback_resume_slots(
                    candidate_brief, target_role, resume_text
                )
            )
        slots += self._fallback_business(bank, target_role=target_role)
        return slots

    def _fallback_business(
        self, bank: list[QuestionSpec], *, target_role: str = ""
    ) -> list[QuestionSpec]:
        if bank:
            return [
                QuestionSpec(
                    section_id="business",
                    section_title="业务题",
                    question_id=f"business_{i + 1}",
                    prompt=_safe_question(
                        q.prompt,
                        self._role_safe_business_questions(target_role)[i % 3],
                    ),
                    competency=q.competency,
                    expected_signals=list(q.expected_signals),
                    red_flags=list(q.red_flags),
                    max_followups=self._business_followups,
                )
                for i, q in enumerate(bank[: self._business_questions])
            ]
        questions = self._role_safe_business_questions(target_role)
        return [
            QuestionSpec(
                section_id="business",
                section_title="业务题",
                question_id=f"business_{i + 1}",
                prompt=text,
                max_followups=self._business_followups,
            )
            for i, text in enumerate(questions[: self._business_questions])
        ]

    def _fallback_resume_slots(
        self,
        candidate_brief: str,
        target_role: str,
        resume_text: str = "",
    ) -> list[QuestionSpec]:
        experiences = _experience_catalog(candidate_brief)
        slots: list[QuestionSpec] = []
        for index, item in enumerate(
            experiences[: self._resume_experiences], start=1
        ):
            identity = item["reference"]
            role = str(item.get("role") or "")
            focus = str(item.get("summary") or "").strip()[:48]
            prompt = _question_with_reference(
                (
                    f"围绕“{focus}”，你当时最关键的个人贡献是什么？"
                    if focus
                    else "你负责的工作里，哪一项最能体现你的个人贡献？"
                ),
                identity,
                role,
            )
            slots.append(
                QuestionSpec(
                    section_id="resume_experience",
                    section_title=item.get("section_title") or "简历经历",
                    question_id=f"resume_{index}",
                    prompt=prompt,
                    competency=f"{target_role or '目标岗位'}相关经历的真实性、个人贡献与结果",
                    max_followups=self._resume_followups,
                    source_reference=identity,
                )
            )
        if slots:
            return slots
        if resume_text.strip() or not experiences:
            return [
                QuestionSpec(
                    section_id="resume_experience",
                    section_title="简历经历",
                    question_id="resume_1",
                    prompt=(
                        f"请从简历中选择一段最能体现你与{target_role or '目标岗位'}匹配度的经历，"
                        "具体讲讲你的职责、关键动作和结果？"
                    ),
                    max_followups=self._resume_followups,
                )
            ]
        return []

    @staticmethod
    def _role_safe_business_questions(target_role: str) -> list[str]:
        role = (target_role or "目标岗位").strip()
        return [
            f"结合你的理解，你认为{role}最核心的目标和职责是什么？",
            f"请讲一个最能体现你胜任{role}的真实案例？",
            f"如果你以{role}的身份接手一个目标不够清晰、资源有限的任务，你会如何推进？",
        ]
