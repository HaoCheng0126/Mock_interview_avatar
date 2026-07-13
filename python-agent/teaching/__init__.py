"""Teaching Digital Human — multi-agent interactive classroom.

Modules:
    course_component   — Component message protocol for agent↔frontend
    course_manager     — YAML course loading + validation + persona
    teaching_controller — State machine: LECTURING ↔ ANSWERING ↔ QUIZ
    agent             — HTTP server + ASR + platform integration
    persona_manager    — Teacher & classmate persona config → prompt
    classmate_engine   — AI classmate behavior decision + speech gen
    manager_agent      — Adaptive scheduling decisions
    course_generator   — Two-stage LLM course generation from topic
"""

from teaching.course_component import ComponentMessage, COMPONENT_TYPES
from teaching.course_manager import CourseManager, CourseLoadError, ValidationError
from teaching.teaching_controller import (
    TeachingController,
    TeachingState,
    LECTURE_SYSTEM_PROMPT,
    QA_SYSTEM_PROMPT,
    TRANSITION_PROMPT,
    INTERACTION_FEEDBACK_PROMPT,
)
from teaching.persona_manager import PersonaManager
from teaching.classmate_engine import ClassmateEngine
from teaching.manager_agent import ManagerAgent, ManagerState, ManagerDecision
from teaching.pacing_engine import PacingEngine, PacingState, PacingAction
from teaching.course_generator import CourseGenerator

__all__ = [
    "ComponentMessage", "COMPONENT_TYPES",
    "CourseManager", "CourseLoadError", "ValidationError",
    "TeachingController", "TeachingState",
    "LECTURE_SYSTEM_PROMPT", "QA_SYSTEM_PROMPT",
    "TRANSITION_PROMPT", "INTERACTION_FEEDBACK_PROMPT",
    "PersonaManager",
    "ClassmateEngine",
    "ManagerAgent", "ManagerState", "ManagerDecision",
    "PacingEngine", "PacingState", "PacingAction",
    "CourseGenerator",
]
