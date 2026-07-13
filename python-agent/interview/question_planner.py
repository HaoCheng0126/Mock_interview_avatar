from __future__ import annotations

from interview.models import Evaluation, QuestionSpec


class QuestionPlanner:
    def __init__(self, questions: list[QuestionSpec]) -> None:
        self._questions = list(questions)

    def next_question(self, asked_question_ids: set[str]) -> QuestionSpec | None:
        for question in self._questions:
            if question.question_id not in asked_question_ids:
                return question
        return None

    def follow_up_from(self, evaluation: Evaluation) -> str | None:
        if evaluation.follow_up_needed and evaluation.follow_up_question.strip():
            return evaluation.follow_up_question.strip()
        return None
