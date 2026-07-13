from __future__ import annotations

import json

from interview.models import DimensionAssessment, Exchange, InterviewReport, TranscriptTurn
from interview.prompts import DEFAULT_REPORT_PROMPT, render_template


class ReportGenerator:
    def __init__(
        self,
        llm_client=None,
        *,
        prompt_template: str | None = None,
        context: dict | None = None,
    ) -> None:
        self._llm = llm_client
        self._prompt_template = prompt_template or DEFAULT_REPORT_PROMPT
        self._context = dict(context or {})

    async def generate_async(
        self,
        exchanges: list[Exchange],
        *,
        transcript: list[TranscriptTurn] | None = None,
        rubric_dimensions: list[str] | None = None,
        termination_reason: str | None = None,
    ) -> InterviewReport:
        if self._llm is None:
            return self.generate(
                exchanges,
                transcript=transcript,
                rubric_dimensions=rubric_dimensions,
                termination_reason=termination_reason,
            )
        try:
            prompt = self._build_llm_prompt(
                transcript or [],
                rubric_dimensions or [],
                termination_reason,
            )
            raw = await self._llm.generate(prompt, max_tokens=1024)
            return self._parse_llm_report(raw, exchanges)
        except Exception:
            return self.generate(
                exchanges,
                transcript=transcript,
                rubric_dimensions=rubric_dimensions,
                termination_reason=termination_reason,
            )

    def generate(
        self,
        exchanges: list[Exchange],
        *,
        transcript: list[TranscriptTurn] | None = None,
        rubric_dimensions: list[str] | None = None,
        termination_reason: str | None = None,
    ) -> InterviewReport:
        transcript = list(transcript or [])
        scored = [item.evaluation for item in exchanges if item.evaluation is not None]
        overall = round(sum(item.score for item in scored) / len(scored)) if scored else 0
        strengths: list[str] = []
        weaknesses: list[str] = []
        for evaluation in scored:
            strengths.extend(evaluation.strengths)
            weaknesses.extend(evaluation.weaknesses)
        candidate_answers = [
            turn for turn in transcript if turn.role == "candidate" and turn.type == "answer"
        ]
        skipped = [turn for turn in transcript if turn.type == "question_skipped"]
        dimension_scores = self._dimension_scores(
            rubric_dimensions or [],
            scored,
            candidate_answers,
            skipped,
        )
        if not strengths and candidate_answers:
            strengths.append("候选人提供了可用于评估的回答。")
        if skipped:
            weaknesses.append(f"有 {len(skipped)} 个问题未收到有效回答。")
        recommendations = ["继续补充具体案例、量化结果和技术取舍。"]
        if termination_reason:
            recommendations.append(f"本次面试提前结束原因：{termination_reason}。")
        summary = (
            f"本次面试记录 {len(transcript)} 条对话，其中候选人有效回答 "
            f"{len(candidate_answers)} 条。"
            if transcript
            else f"本次面试共完成 {len(exchanges)} 轮问答。"
        )
        return InterviewReport(
            summary=summary,
            overall_score=overall,
            strengths=strengths,
            weaknesses=weaknesses,
            recommendations=recommendations,
            exchanges=list(exchanges),
            dimension_scores=dimension_scores,
        )

    def _build_llm_prompt(
        self,
        transcript: list[TranscriptTurn],
        rubric_dimensions: list[str],
        termination_reason: str | None,
    ) -> str:
        turns = [
            {
                "role": turn.role,
                "type": turn.type,
                "text": turn.text,
                "questionId": turn.question_id,
                "exchangeId": turn.exchange_id,
                "metadata": turn.metadata,
            }
            for turn in transcript
        ]
        return render_template(
            self._prompt_template,
            {
                **self._context,
                "rubric_dimensions": json.dumps(rubric_dimensions, ensure_ascii=False),
                "termination_reason": termination_reason or "",
                "transcript": json.dumps(turns, ensure_ascii=False),
            },
        )

    @staticmethod
    def _parse_llm_report(raw: str, exchanges: list[Exchange]) -> InterviewReport:
        data = json.loads(raw)
        dimensions = {}
        for name, value in (data.get("dimensions") or {}).items():
            if not isinstance(value, dict):
                continue
            dimensions[str(name)] = DimensionAssessment(
                score=int(value.get("score") or 0),
                evidence=[str(item) for item in value.get("evidence") or []],
                concerns=[str(item) for item in value.get("concerns") or []],
                recommendations=[
                    str(item) for item in value.get("recommendations") or []
                ],
                confidence=str(value.get("confidence") or "low"),
            )
        return InterviewReport(
            summary=str(data.get("summary") or ""),
            overall_score=int(data.get("overallScore") or data.get("overall_score") or 0),
            strengths=[str(item) for item in data.get("strengths") or []],
            weaknesses=[str(item) for item in data.get("weaknesses") or []],
            recommendations=[str(item) for item in data.get("recommendations") or []],
            exchanges=list(exchanges),
            dimension_scores=dimensions,
        )

    @staticmethod
    def _dimension_scores(
        rubric_dimensions: list[str],
        scored,
        candidate_answers: list[TranscriptTurn],
        skipped: list[TranscriptTurn],
    ) -> dict[str, DimensionAssessment]:
        result: dict[str, DimensionAssessment] = {}
        for dimension in rubric_dimensions:
            collected = [
                evaluation.dimension_assessments[dimension]
                for evaluation in scored
                if dimension in evaluation.dimension_assessments
            ]
            numeric = [
                evaluation.dimensions[dimension]
                for evaluation in scored
                if dimension in evaluation.dimensions
            ]
            if collected:
                evidence = [
                    item
                    for assessment in collected
                    for item in assessment.evidence
                ]
                concerns = [
                    item
                    for assessment in collected
                    for item in assessment.concerns
                ]
                recommendations = [
                    item
                    for assessment in collected
                    for item in assessment.recommendations
                ]
                score = round(sum(item.score for item in collected) / len(collected))
                confidence = "high" if len(candidate_answers) >= 2 else "medium"
            else:
                evidence = [candidate_answers[0].text] if candidate_answers else []
                concerns = ["回答样本较少，维度证据不足。"] if not evidence else []
                recommendations = ["继续围绕该维度追问具体案例。"]
                score = round(sum(numeric) / len(numeric)) if numeric else 0
                confidence = "medium" if evidence else "low"
            if skipped:
                concerns.append(f"存在 {len(skipped)} 个跳过问题，影响该维度置信度。")
            result[dimension] = DimensionAssessment(
                score=score,
                evidence=evidence,
                concerns=concerns,
                recommendations=recommendations,
                confidence=confidence,
            )
        return result
