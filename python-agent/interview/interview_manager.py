from __future__ import annotations

from pathlib import Path

import yaml

from interview import prompts as prompt_defaults
from interview.models import (
    CandidateConfig,
    InterviewConfig,
    InterviewerConfig,
    KnowledgeConfig,
    KnowledgeEntry,
    PromptsConfig,
    QuestionSpec,
    SpeechConfig,
    ThinkingCheck,
    WorkflowConfig,
)
from interview.prompts import render_template


class InterviewManager:
    def __init__(self, config_path: Path | str) -> None:
        self._config_path = Path(config_path)
        self.config = self._load()

    def get_question_specs(self) -> list[QuestionSpec]:
        return list(self.config.questions)

    def persona_context(self) -> dict:
        """Placeholder values available to every prompt/speech template."""
        cfg = self.config
        knowledge_body = self._knowledge_body()
        return {
            "interviewer_name": cfg.interviewer.name,
            "interviewer_style": cfg.interviewer.style or "专业、客观",
            "interviewer_rules": "；".join(cfg.interviewer.rules) or "无特殊规则",
            "target_role": cfg.candidate.target_role,
            "candidate_background": cfg.candidate.background or "未提供",
            "title": cfg.title,
            "duration_minutes": cfg.duration_minutes,
            "knowledge": knowledge_body,
            "knowledge_block": self.knowledge_block(knowledge_body),
        }

    def knowledge_block(self, body: str | None = None) -> str:
        """Full knowledge block (header + entries) or "" when nothing enabled."""
        if body is None:
            body = self._knowledge_body()
        if not body:
            return ""
        return f"\n\n{prompt_defaults.KNOWLEDGE_BLOCK_HEADER}\n{body}"

    def _knowledge_body(self) -> str:
        knowledge = self.config.knowledge
        parts = [
            f"【{entry.title}】\n{entry.content}"
            for entry in knowledge.entries
            if entry.enabled and entry.content.strip()
        ]
        body = "\n\n".join(parts)
        if len(body) > knowledge.max_chars:
            body = body[: knowledge.max_chars].rstrip()
            body += f"\n{prompt_defaults.KNOWLEDGE_TRUNCATION_NOTE}"
        return body

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
        question_sets = raw.get("question_sets") or []
        if not question_sets:
            raise ValueError("question_sets must contain at least one question")

        questions = []
        for index, item in enumerate(question_sets, start=1):
            section_id = str(item.get("id") or f"section_{index}")
            questions.append(
                QuestionSpec(
                    section_id=section_id,
                    section_title=str(item.get("title") or section_id),
                    question_id=str(
                        item.get("question_id") or f"q_{section_id}_{index:03d}"
                    ),
                    prompt=str(item.get("prompt") or item.get("title") or section_id),
                    required=bool(item.get("required", True)),
                    competency=str(item.get("competency") or section_id),
                    difficulty=str(item.get("difficulty") or interview.get("difficulty") or "mid"),
                    expected_signals=[
                        str(signal) for signal in item.get("expected_signals") or []
                    ],
                    red_flags=[str(flag) for flag in item.get("red_flags") or []],
                    max_followups=(
                        int(item["max_followups"])
                        if item.get("max_followups") is not None
                        else None
                    ),
                )
            )

        rubric = raw.get("rubric") or {}
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
            rubric_dimensions=[str(item) for item in rubric.get("dimensions") or []],
            questions=questions,
            prompts=self._load_prompts(raw.get("prompts") or {}),
            speech=self._load_speech(raw.get("speech") or {}),
            workflow=self._load_workflow(raw.get("workflow") or {}),
            knowledge=self._load_knowledge(raw.get("knowledge") or {}),
        )

    @staticmethod
    def _load_knowledge(raw: dict) -> KnowledgeConfig:
        entries = [
            KnowledgeEntry(
                title=str(item.get("title") or f"资料 {index}"),
                content=str(item.get("content") or ""),
                enabled=bool(item.get("enabled", True)),
            )
            for index, item in enumerate(raw.get("entries") or [], start=1)
            if isinstance(item, dict)
        ]
        try:
            max_chars = int(raw.get("max_chars") or prompt_defaults.DEFAULT_KNOWLEDGE_MAX_CHARS)
        except (TypeError, ValueError):
            max_chars = prompt_defaults.DEFAULT_KNOWLEDGE_MAX_CHARS
        return KnowledgeConfig(entries=entries, max_chars=max(200, max_chars))

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
