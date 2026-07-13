from interview.models import Evaluation, QuestionSpec
from interview.question_planner import QuestionPlanner


def test_next_question_returns_first_unasked_required_question():
    planner = QuestionPlanner(
        [
            QuestionSpec("project", "项目", "q1", "项目问题", True),
            QuestionSpec("backend", "后端", "q2", "后端问题", True),
        ]
    )

    assert planner.next_question(set()).question_id == "q1"
    assert planner.next_question({"q1"}).question_id == "q2"
    assert planner.next_question({"q1", "q2"}) is None


def test_follow_up_uses_evaluator_question_when_needed():
    evaluation = Evaluation(
        score=3,
        dimensions={},
        follow_up_needed=True,
        follow_up_question="你如何保证幂等？",
    )

    assert QuestionPlanner([]).follow_up_from(evaluation) == "你如何保证幂等？"


def test_question_spec_carries_competency_and_signal_metadata():
    question = QuestionSpec(
        "project",
        "项目",
        "q1",
        "项目问题",
        True,
        competency="project_ownership",
        difficulty="mid",
        expected_signals=["说明个人贡献"],
        red_flags=["只讲团队成果"],
        max_followups=1,
    )

    assert question.competency == "project_ownership"
    assert question.expected_signals == ["说明个人贡献"]
    assert question.red_flags == ["只讲团队成果"]
    assert question.max_followups == 1
