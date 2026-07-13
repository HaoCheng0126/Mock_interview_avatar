from __future__ import annotations

from interview.models import FollowUpDecision, QuestionSpec


class FollowUpPlanner:
    def plan(self, *, question: QuestionSpec, decision: FollowUpDecision) -> str | None:
        if not decision.needed:
            return None
        if decision.suggested_question:
            return decision.suggested_question
        if decision.missing_signal:
            return f"你刚才的回答里还没有展开{decision.missing_signal}，可以具体讲讲吗？"
        if decision.follow_up_type == "clarify":
            return "我想确认一下，你可以用一个具体例子再说明吗？"
        if question.competency:
            return f"围绕{question.section_title}，你可以再补充一个能体现{question.competency}的细节吗？"
        return "可以再具体讲讲你的做法、取舍和结果吗？"
