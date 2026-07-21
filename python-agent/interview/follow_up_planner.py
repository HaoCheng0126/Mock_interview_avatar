from __future__ import annotations

from interview.models import FollowUpDecision, QuestionSpec


class FollowUpPlanner:
    def plan(self, *, question: QuestionSpec, decision: FollowUpDecision) -> str | None:
        if not decision.needed:
            return None
        if decision.suggested_question:
            return decision.suggested_question
        if question.competency:
            return f"围绕{question.section_title}，你可以再补充一个能体现{question.competency}的细节吗？"
        return "可以再具体讲讲你的做法、取舍和结果吗？"
