from __future__ import annotations

import json
import asyncio

from interview.models import Evaluation, FollowUpDecision, QuestionSpec, TranscriptTurn
from interview.prompts import DEFAULT_FOLLOW_UP_DECIDER_PROMPT, render_template


def _normalize_question_text(text: str) -> str:
    out = (text or "").strip()
    if not out:
        return out
    first_marks = [i for i in (out.find("？"), out.find("?")) if i >= 0]
    if not first_marks:
        return out
    first = min(first_marks)
    tail = out[first + 1 :]
    if "？" in tail or "?" in tail:
        return out[: first + 1].strip()
    if out[first] == "?":
        return (out[:first] + "？").strip()
    return out


class FollowUpDecider:
    def __init__(
        self,
        llm_client=None,
        *,
        prompt_template: str | None = None,
        context: dict | None = None,
    ) -> None:
        self._llm = llm_client
        self._prompt_template = prompt_template or DEFAULT_FOLLOW_UP_DECIDER_PROMPT
        self._context = dict(context or {})

    async def decide_async(
        self,
        *,
        question: QuestionSpec,
        answer_text: str,
        evaluation: Evaluation | None,
        transcript: list[TranscriptTurn],
        probe_index: int,
    ) -> FollowUpDecision:
        if self._llm is None:
            return self.decide(
                question=question,
                answer_text=answer_text,
                evaluation=evaluation,
                transcript=transcript,
                probe_index=probe_index,
            )
        try:
            prompt = self._build_prompt(
                question=question,
                answer_text=answer_text,
                evaluation=evaluation,
                transcript=transcript,
                probe_index=probe_index,
            )
            raw = await asyncio.wait_for(
                self._llm.generate_once(prompt, max_tokens=256), timeout=2.5
            )
            data = json.loads(raw)
            return FollowUpDecision(
                needed=bool(data.get("needed")),
                suggested_question=_normalize_question_text(
                    str(data.get("suggestedQuestion") or "")
                ),
            )
        except Exception:
            return self.decide(
                question=question,
                answer_text=answer_text,
                evaluation=evaluation,
                transcript=transcript,
                probe_index=probe_index,
            )

    def decide(
        self,
        *,
        question: QuestionSpec,
        answer_text: str,
        evaluation: Evaluation | None,
        transcript: list[TranscriptTurn],
        probe_index: int,
    ) -> FollowUpDecision:
        max_followups = question.max_followups
        if max_followups is not None and probe_index >= max_followups:
            return FollowUpDecision(needed=False)

        # Self-intro and project-intro are intentionally non-probing stages: use them
        # to open the conversation and collect context, not to dig deeper immediately.
        if question.section_id in {"self_intro", "resume_project_intro"}:
            return FollowUpDecision(needed=False)

        answer = answer_text.strip()
        missing_signal = self._first_missing_signal(question, answer)
        weak_score = bool(evaluation and evaluation.score > 0 and evaluation.score <= 2)
        strong_score = bool(evaluation and evaluation.score >= 4)
        has_weakness = bool(evaluation and evaluation.weaknesses)
        evaluator_requested = bool(evaluation and evaluation.follow_up_needed)

        if strong_score and not has_weakness and not evaluator_requested:
            return FollowUpDecision(needed=False)

        if weak_score or has_weakness or evaluator_requested or missing_signal:
            suggested = evaluation.follow_up_question.strip() if evaluation else ""
            if not suggested and missing_signal:
                suggested = f"你刚才提到得比较简略，能具体讲讲{missing_signal}吗？"
            return FollowUpDecision(needed=True, suggested_question=suggested)

        return FollowUpDecision(needed=False)

    def _build_prompt(
        self,
        *,
        question: QuestionSpec,
        answer_text: str,
        evaluation: Evaluation | None,
        transcript: list[TranscriptTurn],
        probe_index: int,
    ) -> str:
        trimmed = list(transcript or [])[-6:]
        turns = [
            {
                "role": turn.role,
                "type": turn.type,
                "text": turn.text,
                "questionId": turn.question_id,
                "exchangeId": turn.exchange_id,
            }
            for turn in trimmed
        ]
        payload = {
            "question": {
                "questionId": question.question_id,
                "prompt": question.prompt,
                "competency": question.competency,
                "expectedSignals": question.expected_signals,
                "redFlags": question.red_flags,
            },
            "answer": answer_text,
            "evaluation": (
                {
                    "score": evaluation.score,
                    "dimensions": evaluation.dimensions,
                    "strengths": evaluation.strengths,
                    "weaknesses": evaluation.weaknesses,
                }
                if evaluation is not None
                else None
            ),
            "probeIndex": probe_index,
            "transcript": turns,
        }
        return render_template(
            self._prompt_template,
            {
                **self._context,
                "payload": json.dumps(payload, ensure_ascii=False),
            },
        )

    @staticmethod
    def _first_missing_signal(question: QuestionSpec, answer: str) -> str:
        if not answer:
            return question.expected_signals[0] if question.expected_signals else ""
        for signal in question.expected_signals:
            token = (signal or "").strip()
            if not token:
                continue
            # For natural language expected signals, exact-substring matching is too strict.
            # Only treat short concrete tokens as a hard missing signal.
            if len(token) <= 8 and token not in answer:
                return signal
        return ""
