from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from interview import prompts as prompt_defaults
from interview.models import (
    CandidateConfig,
    CompanyKnowledgeConfig,
    CompanyKnowledgeEntry,
    InterviewConfig,
    InterviewerConfig,
    PlanConfig,
    PositionConfig,
    PromptsConfig,
    QuestionSpec,
    SpeechConfig,
    ThinkingCheck,
    WorkflowConfig,
)
from interview.position_matcher import match_position
from interview.prompts import render_template

if TYPE_CHECKING:
    from interview.profile import CandidateProfile
    from interview.profile_analyzer import CandidateBrief

class InterviewManager:
    def __init__(self, config_path: Path | str) -> None:
        self._config_path = Path(config_path)
        # Raw source is retained for session bookkeeping/position matching only.
        self._profile: "CandidateProfile | None" = None
        # Every prompt consumes this bounded brief, never the source JD/résumé.
        self._candidate_brief: "CandidateBrief | None" = None
        # The position this session matched to (set by apply_candidate_profile).
        self._matched_position: "PositionConfig | None" = None
        # Runtime-only placeholders such as the selected avatar's display name.
        self._runtime_context: dict[str, str] = {}
        self._company_interview_context = ""
        self._company_internal_context = ""
        self.config = self._load()

    def set_runtime_context(self, **kwargs: str) -> None:
        for key, value in kwargs.items():
            if value is None:
                continue
            self._runtime_context[str(key)] = str(value)

    def set_company_knowledge_context(
        self, interview_context: str, internal_context: str
    ) -> None:
        """Install the one-shot compressed result prepared for this session."""
        self._company_interview_context = str(interview_context or "")[
            : self.config.company_knowledge.max_interview_chars
        ]
        self._company_internal_context = str(internal_context or "")[
            : self.config.company_knowledge.max_internal_chars
        ]

    def get_question_specs(self) -> list[QuestionSpec]:
        return list(self.config.questions)

    def matched_question_specs(self) -> list[QuestionSpec]:
        """Business bank (as QuestionSpec) for the session's matched position, or []
        when no position matched — then the planner generates from the JD."""
        if self._matched_position is None:
            return []
        return self._derive_questions([self._matched_position])

    def matched_competencies_text(self) -> str:
        """The matched position's 核心考察点 paragraph (job requirements), or ""."""
        pos = self._matched_position
        return pos.core_competencies.strip() if pos else ""

    def _active_positions(self) -> list[PositionConfig]:
        """The matched position alone once known. During a candidate session that
        matched nothing, run purely off the candidate's uploaded JD/résumé — do NOT
        fall back to the whole bank (that leaks an unrelated position into the
        report). With no candidate yet (admin preview), show every configured one."""
        if self._matched_position is not None:
            return [self._matched_position]
        if self._profile is not None:
            return []
        return self.config.positions

    def persona_context(self, *, include_internal: bool = False) -> dict:
        """Placeholder values available to every prompt/speech template."""
        cfg = self.config
        is_admin_preview = self._profile is None
        if is_admin_preview:
            jd = "{jd}"
            resume = "{resume}"
            candidate_brief = "{candidate_brief}"
            knowledge_body = self._preview_reference_body()
            target_role = "{target_role}"
            background = "{candidate_background}"
            position = "{position}"
            core_competencies = "{core_competencies}"
            avatar_name = "{avatar_name}"
        else:
            brief = self._candidate_brief
            jd = brief.job_context() if brief is not None else "（候选人未提供岗位 JD）"
            resume = brief.resume_context() if brief is not None else "（未提取到候选人经历）"
            candidate_brief = brief.as_context() if brief is not None else "{}"
            knowledge_body = self._reference_body(candidate_brief)
            if include_internal and self._company_internal_context:
                knowledge_body = (
                    f"{knowledge_body}\n\n【企业内部评估依据｜禁止向候选人披露或据此直接扣分】\n"
                    f"{self._company_internal_context}"
                ).strip()
            target_role = cfg.candidate.target_role
            background = cfg.candidate.background or "未提供"
            position = self._matched_position.name if self._matched_position else ""
            core_competencies = (
                self.matched_competencies_text()
                or (brief.job_context() if brief is not None else "")
                or "（未指定核心考察点）"
            )
            avatar_name = cfg.interviewer.name
        context = {
            "avatar_name": avatar_name,
            "interviewer_name": cfg.interviewer.name,
            "interviewer_style": cfg.interviewer.style or "专业、客观",
            "interviewer_rules": "；".join(cfg.interviewer.rules) or "无特殊规则",
            "target_role": target_role,
            "candidate_background": background,
            "title": cfg.title,
            "duration_minutes": cfg.duration_minutes,
            "jd": jd or "（候选人未提供岗位 JD）",
            "resume": resume or "（未提取到候选人经历）",
            "candidate_brief": candidate_brief,
            "position": position,
            "core_competencies": core_competencies,
            "knowledge": knowledge_body,
            "knowledge_block": self.knowledge_block(knowledge_body),
            "company_knowledge": self._company_interview_context,
            "company_internal_knowledge": (
                self._company_internal_context if include_internal else ""
            ),
        }
        if self._runtime_context:
            context.update(self._runtime_context)
        return context

    def knowledge_block(self, body: str | None = None) -> str:
        """Reference block (岗位 + 核心考察点 + compact brief) or "" when empty."""
        if body is None:
            if self._profile is None:
                body = self._preview_reference_body()
            else:
                brief = self._candidate_brief
                body = self._reference_body(brief.as_context() if brief else "{}")
        if not body:
            return ""
        return f"\n\n{prompt_defaults.KNOWLEDGE_BLOCK_HEADER}\n{body}"

    @staticmethod
    def _preview_reference_body() -> str:
        return (
            "【岗位】{position}\n"
            "岗位要求/考察点：{core_competencies}\n\n"
            "【本场候选人 JD】\n{jd}\n\n"
            "【本场候选人简历】\n{resume}\n\n"
            "【本场候选人精简画像】\n{candidate_brief}"
        )

    def _reference_body(self, candidate_brief: str) -> str:
        """Build the reference block from the matched position plus compact brief."""
        parts: list[str] = []
        for pos in self._active_positions():
            lines = [f"【岗位】{pos.name}"]
            if pos.core_competencies:
                lines.append(f"岗位要求/考察点：{pos.core_competencies}")
            parts.append("\n".join(lines))
        if candidate_brief and candidate_brief != "{}":
            parts.append(f"【本场候选人精简画像】\n{candidate_brief}")
        if self._company_interview_context:
            parts.append(
                "【公司背景｜可用于业务题、追问与评估】\n"
                + self._company_interview_context
            )
        if self._profile is not None:
            source_chars = len(self._profile.jd_text) + len(self._profile.resume_text)
            if source_chars > prompt_defaults.DEFAULT_KNOWLEDGE_MAX_CHARS:
                parts.append(prompt_defaults.KNOWLEDGE_TRUNCATION_NOTE)
        body = "\n\n".join(parts)
        cap = prompt_defaults.DEFAULT_KNOWLEDGE_MAX_CHARS
        if len(body) > cap:
            body = body[:cap].rstrip() + f"\n{prompt_defaults.KNOWLEDGE_TRUNCATION_NOTE}"
        return body

    def apply_candidate_profile(
        self,
        profile: "CandidateProfile",
        brief: "CandidateBrief | None" = None,
    ) -> None:
        """Attach source metadata and a prompt-safe compact brief to this session."""
        from interview.profile_analyzer import fallback_candidate_brief

        self._profile = profile
        self._candidate_brief = brief or fallback_candidate_brief(profile)
        if profile.target_role:
            self.config.candidate.target_role = profile.target_role
        # The candidate's real background comes from the compact brief; drop the
        # static config background so a leftover ("有 3 年后端经验…") can't leak into the
        # prompts/report and mislabel this candidate.
        self.config.candidate.background = ""
        # Match from actual submitted text. An empty JD stays empty.
        self._matched_position = match_position(
            self.config.positions,
            jd_text=profile.jd_text,
            target_role=profile.target_role,
        )
        self._compile_company_knowledge(profile)

    def _compile_company_knowledge(self, profile: "CandidateProfile") -> None:
        """Select and bound company knowledge once for this prepared session.

        The raw YAML entries never enter repeated prompts. Public/interview material
        is available to planning, questions and evaluation; internal material is
        exposed only when a caller explicitly requests an internal context.
        """
        config = self.config.company_knowledge
        query = f"{profile.target_role}\n{profile.jd_text}".lower()
        terms = {
            token
            for token in __import__("re").findall(r"[\w\u4e00-\u9fff]{2,}", query)
            if len(token) >= 2
        }

        def rank(entry: CompanyKnowledgeEntry) -> tuple[int, str]:
            haystack = f"{entry.title} {entry.category} {entry.content}".lower()
            score = sum(1 for token in terms if token in haystack)
            category_bonus = 1 if entry.category in {"business_scenarios", "tech_stack"} else 0
            return (score + category_bonus, entry.title)

        enabled = [entry for entry in config.entries if entry.enabled and entry.content.strip()]
        enabled.sort(key=rank, reverse=True)

        def render(visibility: str, limit: int) -> str:
            chunks: list[str] = []
            used = 0
            for entry in enabled:
                if entry.visibility != visibility:
                    continue
                chunk = f"【{entry.title}｜{entry.category}】\n{entry.content.strip()}"
                remaining = limit - used
                if remaining <= 0:
                    break
                chunk = chunk[:remaining]
                chunks.append(chunk)
                used += len(chunk) + 2
            return "\n\n".join(chunks)

        self._company_interview_context = render(
            "interview", config.max_interview_chars
        )
        self._company_internal_context = render(
            "internal", config.max_internal_chars
        )

    def build_system_prompt(self) -> str:
        return render_template(self.config.prompts.system, self.persona_context())

    def build_opening_text(self) -> str:
        return render_template(
            self.config.speech.opening_template, self.persona_context()
        )

    def _load(self) -> InterviewConfig:
        raw = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
        interview = raw.get("interview") or {}
        interviewer_raw = raw.get("interviewer") or {}
        candidate_raw = raw.get("candidate") or {}
        positions = self._load_positions(raw.get("positions") or [])
        return InterviewConfig(
            title=str(interview.get("title") or "Interview"),
            lang=str(interview.get("lang") or "zh"),
            duration_minutes=int(interview.get("duration_minutes") or 20),
            difficulty=str(interview.get("difficulty") or "mid"),
            max_probe_per_question=int(interview.get("max_probe_per_question") or 2),
            interviewer=InterviewerConfig(
                name=str(interviewer_raw.get("name") or "面试官"),
                style=str(interviewer_raw.get("style") or ""),
                rules=[str(rule) for rule in interviewer_raw.get("rules") or []],
            ),
            candidate=CandidateConfig(
                target_role=str(candidate_raw.get("target_role") or "候选人"),
                background=str(candidate_raw.get("background") or ""),
            ),
            positions=positions,
            # 核心考察点 is a free-text paragraph now, not structured dimensions;
            # scoring guidance flows to the evaluator/report via {core_competencies}.
            rubric_dimensions=[],
            questions=self._derive_questions(positions),
            prompts=self._load_prompts(raw.get("prompts") or {}),
            speech=self._load_speech(raw.get("speech") or {}),
            workflow=self._load_workflow(raw.get("workflow") or {}),
            plan=self._load_plan(raw.get("plan") or {}),
            company_knowledge=self._load_company_knowledge(
                raw.get("company_knowledge") or {}
            ),
        )

    @staticmethod
    def _load_company_knowledge(raw: dict) -> CompanyKnowledgeConfig:
        entries: list[CompanyKnowledgeEntry] = []
        for item in raw.get("entries") or []:
            if not isinstance(item, dict):
                continue
            entries.append(
                CompanyKnowledgeEntry(
                    id=str(item.get("id") or ""),
                    title=str(item.get("title") or "").strip(),
                    category=str(item.get("category") or "other").strip(),
                    content=str(item.get("content") or "").strip(),
                    visibility=str(item.get("visibility") or "interview").strip(),
                    enabled=bool(item.get("enabled", True)),
                )
            )
        return CompanyKnowledgeConfig(
            entries=entries,
            max_interview_chars=int(raw.get("max_interview_chars") or 4000),
            max_internal_chars=int(raw.get("max_internal_chars") or 3000),
        )

    @staticmethod
    def _load_positions(raw: list) -> list[PositionConfig]:
        """Parse positions (岗位 → 业务题[题目 + 可选考察点] + 岗位考察点)."""
        positions: list[PositionConfig] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            questions: list[str] = []
            question_competencies: dict[str, str] = {}
            for q in item.get("business_questions") or []:
                # Strings remain supported; structured rows preserve the per-question
                # assessment point without making the avatar speak it aloud.
                text = str((q.get("prompt") if isinstance(q, dict) else q) or "").strip()
                if text:
                    questions.append(text)
                    if isinstance(q, dict):
                        competency = str(q.get("competency") or "").strip()
                        if competency:
                            question_competencies[text] = competency
            positions.append(
                PositionConfig(
                    name=str(item.get("name") or "岗位").strip(),
                    match_keywords=[
                        str(k).strip()
                        for k in item.get("match_keywords") or []
                        if str(k).strip()
                    ],
                    business_questions=questions,
                    business_question_competencies=question_competencies,
                    core_competencies=str(item.get("core_competencies") or "").strip(),
                )
            )
        return positions

    @staticmethod
    def _derive_questions(positions: list[PositionConfig]) -> list[QuestionSpec]:
        """Preheat/fallback question bank flattened from every position's business
        questions (just the prompts). The real per-session plan is built at start."""
        specs: list[QuestionSpec] = []
        index = 0
        for pos in positions:
            for prompt in pos.business_questions:
                index += 1
                specs.append(
                    QuestionSpec(
                        section_id="business",
                        section_title=pos.name,
                        question_id=f"biz_{index:03d}",
                        prompt=prompt,
                        competency=pos.business_question_competencies.get(prompt, ""),
                    )
                )
        return specs

    @staticmethod
    def _load_prompts(raw: dict) -> PromptsConfig:
        """Prompt templates; any field omitted in YAML keeps the built-in default."""
        return PromptsConfig(
            system=str(raw.get("system") or prompt_defaults.DEFAULT_SYSTEM_PROMPT),
            evaluator=str(
                raw.get("evaluator") or prompt_defaults.DEFAULT_EVALUATOR_PROMPT
            ),
            follow_up_decider=str(
                raw.get("follow_up_decider")
                or prompt_defaults.DEFAULT_FOLLOW_UP_DECIDER_PROMPT
            ),
            report=str(raw.get("report") or prompt_defaults.DEFAULT_REPORT_PROMPT),
            planner=str(raw.get("planner") or prompt_defaults.DEFAULT_PLANNER_PROMPT),
            closing_comment=str(
                raw.get("closing_comment")
                or prompt_defaults.DEFAULT_CLOSING_COMMENT_PROMPT
            ),
        )

    @staticmethod
    def _load_speech(raw: dict) -> SpeechConfig:
        def _phrases(key: str, defaults: list[str]) -> list[str]:
            values = [str(item) for item in raw.get(key) or [] if str(item).strip()]
            return values or list(defaults)

        checks_raw = raw.get("thinking_checks")
        if checks_raw:
            checks = [
                ThinkingCheck(
                    after_seconds=float(item.get("after_seconds") or 0),
                    text=str(item.get("text") or ""),
                )
                for item in checks_raw
                if isinstance(item, dict) and str(item.get("text") or "").strip()
            ]
        else:
            checks = [
                ThinkingCheck(after_seconds=seconds, text=text)
                for seconds, text in prompt_defaults.DEFAULT_THINKING_CHECKS
            ]
        return SpeechConfig(
            self_intro_prompt=str(
                raw.get("self_intro_prompt") or prompt_defaults.DEFAULT_SELF_INTRO_PROMPT
            ),
            prep_template=str(
                raw.get("prep_template") or prompt_defaults.DEFAULT_PREP_TEMPLATE
            ),
            opening_template=str(
                raw.get("opening_template") or prompt_defaults.DEFAULT_OPENING_TEMPLATE
            ),
            answer_acknowledgements=_phrases(
                "answer_acknowledgements",
                prompt_defaults.DEFAULT_ANSWER_ACKNOWLEDGEMENTS,
            ),
            final_answer_acknowledgements=_phrases(
                "final_answer_acknowledgements",
                prompt_defaults.DEFAULT_FINAL_ANSWER_ACKNOWLEDGEMENTS,
            ),
            follow_up_prefixes=_phrases(
                "follow_up_prefixes", prompt_defaults.DEFAULT_FOLLOW_UP_PREFIXES
            ),
            first_question_transition=str(
                raw.get("first_question_transition")
                or prompt_defaults.DEFAULT_FIRST_QUESTION_TRANSITION
            ),
            next_question_transitions=(
                [
                    str(item)
                    for item in raw.get("next_question_transitions") or []
                    if str(item).strip()
                ]
                or (
                    [str(raw.get("next_question_transition"))]
                    if str(raw.get("next_question_transition") or "").strip()
                    else list(prompt_defaults.DEFAULT_NEXT_QUESTION_TRANSITIONS)
                )
            ),
            next_question_transition=str(
                raw.get("next_question_transition")
                or prompt_defaults.DEFAULT_NEXT_QUESTION_TRANSITION
            ),
            skip_transition=str(
                raw.get("skip_transition") or prompt_defaults.DEFAULT_SKIP_TRANSITION
            ),
            closing=str(raw.get("closing") or prompt_defaults.DEFAULT_CLOSING),
            termination=str(
                raw.get("termination") or prompt_defaults.DEFAULT_TERMINATION
            ),
            thinking_checks=checks,
        )

    @staticmethod
    def _load_workflow(raw: dict) -> WorkflowConfig:
        defaults = WorkflowConfig()

        def _num(key: str, fallback: float) -> float:
            value = raw.get(key)
            return float(value) if value is not None else fallback

        return WorkflowConfig(
            hard_timeout_seconds=_num(
                "hard_timeout_seconds", defaults.hard_timeout_seconds
            ),
            opening_to_question_delay_seconds=_num(
                "opening_to_question_delay_seconds",
                defaults.opening_to_question_delay_seconds,
            ),
            prompt_playback_timeout_seconds=_num(
                "prompt_playback_timeout_seconds",
                defaults.prompt_playback_timeout_seconds,
            ),
            candidate_speech_grace_seconds=_num(
                "candidate_speech_grace_seconds",
                defaults.candidate_speech_grace_seconds,
            ),
            evaluation_join_timeout_seconds=_num(
                "evaluation_join_timeout_seconds",
                defaults.evaluation_join_timeout_seconds,
            ),
            foreground_evaluation_timeout_seconds=_num(
                "foreground_evaluation_timeout_seconds",
                defaults.foreground_evaluation_timeout_seconds,
            ),
            max_skipped_questions=int(
                _num("max_skipped_questions", defaults.max_skipped_questions)
            ),
            max_consecutive_skipped_questions=int(
                _num(
                    "max_consecutive_skipped_questions",
                    defaults.max_consecutive_skipped_questions,
                )
            ),
        )

    @staticmethod
    def _load_plan(raw: dict) -> PlanConfig:
        defaults = PlanConfig()

        def _count(key: str, fallback: int) -> int:
            value = raw.get(key)
            if value is None:
                return fallback
            try:
                return max(0, int(value))  # counts/budgets are non-negative
            except (TypeError, ValueError):
                return fallback

        return PlanConfig(
            resume_experiences=_count(
                "resume_experiences", defaults.resume_experiences
            ),
            business_questions=_count(
                "business_questions", defaults.business_questions
            ),
            resume_followups=_count("resume_followups", defaults.resume_followups),
            business_followups=_count(
                "business_followups", defaults.business_followups
            ),
            self_intro_followups=_count(
                "self_intro_followups", defaults.self_intro_followups
            ),
            self_intro_followups_no_resume=_count(
                "self_intro_followups_no_resume",
                defaults.self_intro_followups_no_resume,
            ),
        )
