from __future__ import annotations

import json

from interview.models import DimensionAssessment, Evaluation
from interview.prompts import DEFAULT_EVALUATOR_PROMPT, render_template


class AnswerEvaluator:
    def __init__(
        self,
        llm_client,
        *,
        prompt_template: str | None = None,
        context: dict | None = None,
    ) -> None:
        self._llm = llm_client
        self._prompt_template = prompt_template or DEFAULT_EVALUATOR_PROMPT
        self._context = dict(context or {})

    async def evaluate(
        self,
        question_text: str,
        answer_text: str,
        *,
        transcript: list[dict] | None = None,
        rubric_dimensions: list[str] | None = None,
    ) -> Evaluation:
        prompt = render_template(
            self._prompt_template,
            {
                **self._context,
                "question": question_text,
                "answer": answer_text,
                "rubric_dimensions": json.dumps(
                    rubric_dimensions or [], ensure_ascii=False
                ),
                "transcript": json.dumps(transcript or [], ensure_ascii=False),
            },
        )
        try:
            raw = await self._llm.generate(prompt, max_tokens=512)
            data = json.loads(raw)
            dimension_assessments = {}
            for key, value in (data.get("dimensionAssessments") or {}).items():
                if not isinstance(value, dict):
                    continue
                dimension_assessments[str(key)] = DimensionAssessment(
                    score=int(value.get("score") or 0),
                    evidence=[str(item) for item in value.get("evidence") or []],
                    concerns=[str(item) for item in value.get("concerns") or []],
                    recommendations=[
                        str(item) for item in value.get("recommendations") or []
                    ],
                    confidence=str(value.get("confidence") or "low"),
                )
            return Evaluation(
                score=int(data.get("score") or 0),
                dimensions={
                    str(key): int(value)
                    for key, value in (data.get("dimensions") or {}).items()
                },
                strengths=[str(item) for item in data.get("strengths") or []],
                weaknesses=[str(item) for item in data.get("weaknesses") or []],
                follow_up_needed=bool(data.get("followUpNeeded")),
                follow_up_question=str(data.get("followUpQuestion") or ""),
                dimension_assessments=dimension_assessments,
            )
        except Exception:
            return self._fallback(answer_text)

    @staticmethod
    def _fallback(answer_text: str) -> Evaluation:
        if len(answer_text.strip()) < 20:
            return Evaluation(
                score=2,
                dimensions={"clarity": 2},
                weaknesses=["回答较短，缺少细节"],
                follow_up_needed=True,
                follow_up_question="可以再具体讲讲你的做法和结果吗？",
            )
        return Evaluation(
            score=3,
            dimensions={"clarity": 3},
            strengths=["能够回应问题"],
            follow_up_needed=False,
        )
