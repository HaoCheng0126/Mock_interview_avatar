from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class InterviewState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    WAITING_SCENE_READY = "waiting_scene_ready"
    OPENING = "opening"
    ASKING = "asking"
    LISTENING = "listening"
    THINKING_CHECK = "thinking_check"
    SKIPPING_QUESTION = "skipping_question"
    ANALYZING = "analyzing"
    DECIDING_FOLLOWUP = "deciding_followup"
    PLANNING_FOLLOWUP = "planning_followup"
    PROBING = "probing"
    TRANSITIONING = "transitioning"
    CLOSING = "closing"
    COMPLETED = "completed"
    TERMINATED = "terminated"
    ERROR = "error"


@dataclass
class InterviewerConfig:
    name: str
    style: str = ""
    rules: list[str] = field(default_factory=list)


@dataclass
class CandidateConfig:
    target_role: str
    background: str = ""


@dataclass
class QuestionSpec:
    section_id: str
    section_title: str
    question_id: str
    prompt: str
    required: bool = True
    competency: str = ""
    difficulty: str = ""
    expected_signals: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    max_followups: int | None = None


@dataclass
class DimensionAssessment:
    score: int
    evidence: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    confidence: str = "low"


@dataclass
class PromptsConfig:
    """LLM prompt templates; placeholders documented in interview/prompts.py."""

    system: str = ""
    evaluator: str = ""
    follow_up_decider: str = ""
    report: str = ""


@dataclass
class ThinkingCheck:
    after_seconds: float
    text: str


@dataclass
class SpeechConfig:
    """Spoken phrases; empty/omitted fields fall back to interview/prompts.py defaults."""

    opening_template: str = ""
    answer_acknowledgements: list[str] = field(default_factory=list)
    final_answer_acknowledgements: list[str] = field(default_factory=list)
    follow_up_prefixes: list[str] = field(default_factory=list)
    first_question_transition: str = ""
    next_question_transition: str = ""
    skip_transition: str = ""
    closing: str = ""
    termination: str = ""
    thinking_checks: list[ThinkingCheck] = field(default_factory=list)


@dataclass
class KnowledgeEntry:
    title: str
    content: str
    enabled: bool = True


@dataclass
class KnowledgeConfig:
    """Reference material (JD, resume, domain docs) injected into LLM prompts."""

    entries: list[KnowledgeEntry] = field(default_factory=list)
    max_chars: int = 6000


@dataclass
class WorkflowConfig:
    hard_timeout_seconds: float = 75.0
    opening_to_question_delay_seconds: float = 0.8
    prompt_playback_timeout_seconds: float = 30.0
    candidate_speech_grace_seconds: float = 8.0
    evaluation_join_timeout_seconds: float = 5.0
    max_skipped_questions: int = 3
    max_consecutive_skipped_questions: int = 2


@dataclass
class InterviewConfig:
    title: str
    lang: str
    duration_minutes: int
    difficulty: str
    max_probe_per_question: int
    interviewer: InterviewerConfig
    candidate: CandidateConfig
    rubric_dimensions: list[str]
    questions: list[QuestionSpec]
    prompts: PromptsConfig = field(default_factory=PromptsConfig)
    speech: SpeechConfig = field(default_factory=SpeechConfig)
    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)


@dataclass
class Evaluation:
    score: int
    dimensions: dict[str, int]
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    follow_up_needed: bool = False
    follow_up_question: str = ""
    dimension_assessments: dict[str, DimensionAssessment] = field(default_factory=dict)


@dataclass
class FollowUpDecision:
    needed: bool
    reason: str = ""
    missing_signal: str = ""
    follow_up_type: str = "skip"
    suggested_question: str = ""


@dataclass
class Exchange:
    exchange_id: str
    question_id: str
    section_id: str
    type: str
    prompt_id: str
    prompt_text: str
    prompt_type: str
    parent_exchange_id: str | None = None
    probe_index: int = 0
    answer_request_id: str | None = None
    answer_text: str = ""
    evaluation: Evaluation | None = None


@dataclass
class InterviewReport:
    summary: str
    overall_score: int
    strengths: list[str]
    weaknesses: list[str]
    recommendations: list[str]
    exchanges: list[Exchange]
    dimension_scores: dict[str, DimensionAssessment] = field(default_factory=dict)


@dataclass
class TranscriptTurn:
    turn_id: str
    interview_id: str
    role: str
    type: str
    text: str
    question_id: str | None = None
    exchange_id: str | None = None
    metadata: dict = field(default_factory=dict)
