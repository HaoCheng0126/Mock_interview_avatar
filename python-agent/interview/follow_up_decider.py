from __future__ import annotations

import json

from interview.models import Evaluation, FollowUpDecision, QuestionSpec, TranscriptTurn
from interview.prompts import DEFAULT_FOLLOW_UP_DECIDER_PROMPT, render_template


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
            raw = await self._llm.generate(prompt, max_tokens=512)
            data = json.loads(raw)
            return FollowUpDecision(
                needed=bool(data.get("needed")),
                reason=str(data.get("reason") or ""),
                missing_signal=str(data.get("missingSignal") or ""),
                follow_up_type=str(data.get("followUpType") or "skip"),
                suggested_question=str(data.get("suggestedQuestion") or ""),
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

        answer = answer_text.strip()
        missing_signal = self._first_missing_signal(question, answer)
        weak_score = bool(evaluation and evaluation.score > 0 and evaluation.score <= 2)
        strong_score = bool(evaluation and evaluation.score >= 4)
        has_weakness = bool(evaluation and evaluation.weaknesses)
        evaluator_requested = bool(evaluation and evaluation.follow_up_needed)

        if strong_score and not has_weakness and not evaluator_requested:
            return FollowUpDecision(needed=False)

        if weak_score or has_weakness or evaluator_requested or missing_signal:
            reason = self._reason(question, evaluation, missing_signal, transcript)
            return FollowUpDecision(
                needed=True,
                reason=reason,
                missing_signal=missing_signal,
                follow_up_type="deepen" if answer else "clarify",
                suggested_question=(
                    evaluation.follow_up_question.strip() if evaluation else ""
                ),
            )

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
        turns = [
            {
                "role": turn.role,
                "type": turn.type,
                "text": turn.text,
                "questionId": turn.question_id,
                "exchangeId": turn.exchange_id,
            }
            for turn in transcript
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
            if signal and signal not in answer:
                return signal
        return ""

    @staticmethod
    def _reason(
        question: QuestionSpec,
        evaluation: Evaluation | None,
        missing_signal: str,
        transcript: list[TranscriptTurn],
    ) -> str:
        if missing_signal:
            if question.competency:
                return f"需要继续验证 {question.competency}，回答还缺少关于 {missing_signal} 的信息。"
            return f"回答还缺少关于 {missing_signal} 的信息。"
        if evaluation and evaluation.weaknesses:
            return evaluation.weaknesses[0]
        if question.competency:
            return f"需要继续验证 {question.competency}。"
        if transcript:
            return "需要结合前面对话继续核实回答细节。"
        return "需要继续核实回答细节。"
