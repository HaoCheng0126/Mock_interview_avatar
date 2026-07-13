"""Pacing Engine — unified classroom rhythm control.

Consolidates what was previously scattered across ManagerAgent (rule-based
decisions) and TeachingController._broadcast_chapter (random classmate
interjections + decision execution) into a single decision point.

Called once per knowledge point; returns ordered actions for the controller
to execute.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from teaching.classmate_engine import ClassmateEngine

logger = logging.getLogger(__name__)


@dataclass
class PacingState:
    """Snapshot of classroom state fed to the pacing engine."""

    chapter_id: str = ""
    knowledge_index: int = 0
    knowledge_total: int = 0
    student_questions_in_chapter: int = 0
    student_quiz_correct: bool | None = None
    elapsed_seconds: float = 0.0


@dataclass
class PacingAction:
    """A recommended pacing action for the controller to execute."""

    kind: str  # CONTINUE | RE_EXPLAIN | CLASSMATE_SPEAK | ASK_QUESTION | SKIP
    reason: str = ""
    classmate_name: str | None = None  # Pre-selected classmate for CLASSMATE_SPEAK

    @property
    def action(self) -> str:
        """Backward-compat alias for kind (matches old ManagerDecision.action)."""
        return self.kind


class PacingEngine:
    """Unified classroom pacing decision-maker.

    Replaces the old ManagerAgent (rule-based decisions only) by also absorbing
    the random classmate interjection logic that was hardcoded in the controller.
    Three formerly-independent classmate triggers are now one decision path:

    * ClassmateEngine.should_interject() 30% random (was in controller)
    * ManagerAgent 30% random CLASSMATE_SPEAK (was in manager — dead code,
      classmate_recently_spoke was never updated)
    * Chapter-end force CLASSMATE_SPEAK (was in manager)

    All three now flow through _should_classmate_speak().
    """

    def __init__(self, classmate_engine: ClassmateEngine | None = None) -> None:
        self._classmates = classmate_engine
        self._state = PacingState()
        self._classmate_spoke_this_chapter = False

    # -- State management ---------------------------------------------------

    def update_state(self, **kwargs) -> None:
        """Update pacing state fields from the controller."""
        for key, value in kwargs.items():
            if hasattr(self._state, key):
                setattr(self._state, key, value)

    def mark_classmate_spoke(self) -> None:
        """Called by controller after a classmate finishes speaking."""
        self._classmate_spoke_this_chapter = True

    def reset_chapter(self) -> None:
        """Reset per-chapter tracking when starting a new chapter."""
        self._classmate_spoke_this_chapter = False

    # -- Decision -----------------------------------------------------------

    async def evaluate(self) -> list[PacingAction]:
        """Evaluate current state and return ordered pacing actions.

        Called once per knowledge point. The controller executes each action
        in order, skipping remaining actions if the lecture is interrupted
        (e.g. hand raised).
        """
        actions: list[PacingAction] = []
        state = self._state

        # Rule: student asked 2+ questions → re-explain
        if state.student_questions_in_chapter >= 2:
            actions.append(PacingAction("RE_EXPLAIN", "student asked multiple questions"))
            return actions  # Re-explain takes priority

        # Rule: chapter running long (>4 min) and not at start → skip
        if state.elapsed_seconds > 240 and state.knowledge_index > 0:
            actions.append(PacingAction("SKIP", "chapter running long"))
            return actions  # Skip takes priority

        # Rule: student silent mid-chapter, classmate already spoke → check understanding
        if (
            state.student_questions_in_chapter == 0
            and self._classmate_spoke_this_chapter
            and 0 < state.knowledge_index < max(state.knowledge_total - 1, 0)
        ):
            actions.append(PacingAction("ASK_QUESTION", "check quiet student's understanding"))

        # Unified classmate interjection decision
        classmate_name = self._should_classmate_speak()
        if classmate_name:
            actions.append(PacingAction(
                "CLASSMATE_SPEAK",
                "engage classmate",
                classmate_name=classmate_name,
            ))
            self._classmate_spoke_this_chapter = True

        if not actions:
            actions.append(PacingAction("CONTINUE", "normal progress"))

        return actions

    # -- Internal -----------------------------------------------------------

    def _should_classmate_speak(self) -> str | None:
        """Decide whether a classmate should interject now.

        Consolidates the three formerly-independent triggers:
        1. Random chance via ClassmateEngine.should_interject()
        2. Chapter-end force if classmate hasn't spoken yet

        Returns classmate name if one should speak, None otherwise.
        """
        if not self._classmates or not self._classmates.enabled:
            return None

        state = self._state

        # Never interject on the very first knowledge point
        if state.knowledge_index == 0:
            return None

        # Force at chapter end if no classmate has spoken this chapter
        if (
            not self._classmate_spoke_this_chapter
            and state.knowledge_total > 0
            and state.knowledge_index >= state.knowledge_total - 1
        ):
            return self._classmates.should_answer_interaction()

        # Normal random interjection (30% chance, cooldown-aware)
        return self._classmates.should_interject(knowledge_index=state.knowledge_index)

    # -- Backward-compatibility ------------------------------------------

    async def decide(self) -> PacingAction:
        """Legacy interface — returns the first action from evaluate().

        Prefer calling evaluate() directly and iterating over all actions.
        """
        actions = await self.evaluate()
        return actions[0] if actions else PacingAction("CONTINUE", "no actions")

    @property
    def state(self) -> PacingState:
        return self._state


# -- Backward-compatibility aliases -----------------------------------------
# Keep old names working for code that hasn't migrated yet.

ManagerState = PacingState
ManagerDecision = PacingAction
ManagerAgent = PacingEngine
