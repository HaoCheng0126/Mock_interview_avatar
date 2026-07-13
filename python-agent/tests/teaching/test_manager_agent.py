"""Tests for pacing_engine.py classroom pacing decisions.

Tests both the new PacingEngine.evaluate() interface and the legacy
ManagerAgent.decide() backward-compat path.
"""

import pytest

from teaching.pacing_engine import PacingEngine, PacingAction


# -- PacingEngine.evaluate() tests --------------------------------------------


@pytest.mark.asyncio
async def test_silent_mid_chapter_after_classmate_spoke_asks_teacher_question():
    """When a classmate has spoken mid-chapter and student is silent,
    the engine should suggest asking a question to check understanding."""
    engine = PacingEngine(classmate_engine=None)
    engine.update_state(
        knowledge_index=1,
        knowledge_total=4,
        student_questions_in_chapter=0,
    )
    engine.mark_classmate_spoke()  # simulate classmate having spoken

    actions = await engine.evaluate()

    assert any(a.kind == "ASK_QUESTION" for a in actions)


@pytest.mark.asyncio
async def test_last_point_no_classmate_returns_continue_when_no_engine():
    """Without a ClassmateEngine, chapter-end should just continue."""
    engine = PacingEngine(classmate_engine=None)
    engine.update_state(
        knowledge_index=2,
        knowledge_total=3,
    )

    actions = await engine.evaluate()

    # No classmate engine → no CLASSMATE_SPEAK possible
    assert all(a.kind != "CLASSMATE_SPEAK" for a in actions)
    assert any(a.kind == "CONTINUE" for a in actions)


@pytest.mark.asyncio
async def test_multiple_student_questions_triggers_re_explain():
    engine = PacingEngine(classmate_engine=None)
    engine.update_state(
        knowledge_index=1,
        knowledge_total=4,
        student_questions_in_chapter=2,
    )

    actions = await engine.evaluate()

    assert any(a.kind == "RE_EXPLAIN" for a in actions)


@pytest.mark.asyncio
async def test_long_chapter_triggers_skip():
    engine = PacingEngine(classmate_engine=None)
    engine.update_state(
        knowledge_index=2,
        knowledge_total=5,
        elapsed_seconds=300,
    )

    actions = await engine.evaluate()

    assert any(a.kind == "SKIP" for a in actions)


@pytest.mark.asyncio
async def test_skip_not_triggered_at_start():
    """SKIP should not fire on the very first knowledge point,
    even if the chapter is long (elapsed includes previous chapters)."""
    engine = PacingEngine(classmate_engine=None)
    engine.update_state(
        knowledge_index=0,
        knowledge_total=5,
        elapsed_seconds=300,
    )

    actions = await engine.evaluate()

    assert all(a.kind != "SKIP" for a in actions)


@pytest.mark.asyncio
async def test_normal_progress_returns_continue():
    engine = PacingEngine(classmate_engine=None)
    engine.update_state(
        knowledge_index=1,
        knowledge_total=5,
        elapsed_seconds=10,
    )

    actions = await engine.evaluate()

    assert actions == [PacingAction("CONTINUE", "normal progress")]


@pytest.mark.asyncio
async def test_reset_chapter_clears_classmate_flag():
    engine = PacingEngine(classmate_engine=None)
    engine.mark_classmate_spoke()
    engine.update_state(knowledge_index=1, knowledge_total=3)

    # After marking, ASK_QUESTION should be possible
    actions = await engine.evaluate()
    assert any(a.kind == "ASK_QUESTION" for a in actions)

    # After reset, the flag is cleared
    engine.reset_chapter()
    actions = await engine.evaluate()
    assert all(a.kind != "ASK_QUESTION" for a in actions)


# -- Backward-compat ManagerAgent.decide() tests ------------------------------


@pytest.mark.asyncio
async def test_legacy_decide_silent_mid_chapter_asks_teacher_question():
    """Legacy ManagerAgent.decide() should still work via backward compat."""
    engine = PacingEngine(classmate_engine=None)
    engine.update_state(
        knowledge_index=1,
        knowledge_total=4,
        student_questions_in_chapter=0,
    )
    engine.mark_classmate_spoke()

    decision = await engine.decide()

    assert decision.kind == "ASK_QUESTION"


@pytest.mark.asyncio
async def test_legacy_decide_last_point_without_classmate():
    engine = PacingEngine(classmate_engine=None)
    engine.update_state(
        knowledge_index=2,
        knowledge_total=3,
    )

    decision = await engine.decide()

    assert decision.kind == "CONTINUE"
