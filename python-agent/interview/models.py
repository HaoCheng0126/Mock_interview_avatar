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
    REPORT_GENERATING = "report_generating"
    REPORT_ERROR = "report_error"
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
class PositionConfig:
    """A 岗位 — the retrieval unit. At session start the candidate's JD is matched to
    one position by ``match_keywords``. ``business_questions`` contains the spoken
    prompts, while ``business_question_competencies`` keeps optional per-question
    assessment points. ``core_competencies`` is the role-wide requirements paragraph.
    """

    name: str
    match_keywords: list[str] = field(default_factory=list)
    business_questions: list[str] = field(default_factory=list)
    business_question_competencies: dict[str, str] = field(default_factory=dict)
    core_competencies: str = ""


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
    # Concrete company / role / project named by a resume question. Empty for
    # self-introduction and business questions.
    source_reference: str = ""


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
    planner: str = ""
    closing_comment: str = ""


@dataclass
class ThinkingCheck:
    after_seconds: float
    text: str


@dataclass
class SpeechConfig:
    """Spoken phrases; empty/omitted fields fall back to interview/prompts.py defaults."""

    self_intro_prompt: str = ""
    prep_template: str = ""
    opening_template: str = ""
    answer_acknowledgements: list[str] = field(default_factory=list)
    final_answer_acknowledgements: list[str] = field(default_factory=list)
    follow_up_prefixes: list[str] = field(default_factory=list)
    first_question_transition: str = ""
    next_question_transitions: list[str] = field(default_factory=list)
    # Legacy single-value field; configs are migrated to the list above.
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
class CompanyKnowledgeEntry:
    id: str
    title: str
    category: str
    content: str
    visibility: str = "interview"
    enabled: bool = True


@dataclass
class CompanyKnowledgeConfig:
    entries: list[CompanyKnowledgeEntry] = field(default_factory=list)
    max_interview_chars: int = 4000
    max_internal_chars: int = 3000


@dataclass
class WorkflowConfig:
    hard_timeout_seconds: float = 75.0
    opening_to_question_delay_seconds: float = 0.8
    prompt_playback_timeout_seconds: float = 30.0
    candidate_speech_grace_seconds: float = 8.0
    evaluation_join_timeout_seconds: float = 5.0
    foreground_evaluation_timeout_seconds: float = 5.0
    max_skipped_questions: int = 3
    max_consecutive_skipped_questions: int = 2


@dataclass
class PlanConfig:
    """Shape of the session-start interview plan: how many stages of each kind and
    how hard to probe each. Consumed by InterviewPlanner at the opening.

    Defaults mirror prompts.DEFAULT_* (kept in sync the same way WorkflowConfig
    mirrors prompts.DEFAULT_WORKFLOW).
    """

    resume_experiences: int = 2  # résumé experiences to deep-dive (0 = skip stage)
    business_questions: int = 3  # business questions to ask
    resume_followups: int = 1  # follow-up budget per résumé experience
    business_followups: int = 1  # follow-up budget per business question
    self_intro_followups: int = 0  # self-intro follow-up budget (résumé present)
    self_intro_followups_no_resume: int = 0  # self-intro budget when no résumé


@dataclass
class InterviewConfig:
    title: str
    lang: str
    duration_minutes: int
    difficulty: str
    max_probe_per_question: int
    interviewer: InterviewerConfig
    candidate: CandidateConfig
    positions: list[PositionConfig]  # source of truth (岗位 → 业务题库 + 核心考察点)
    rubric_dimensions: list[str]  # derived from positions' core_competencies
    questions: list[QuestionSpec]  # derived from positions' business_questions
    prompts: PromptsConfig = field(default_factory=PromptsConfig)
    speech: SpeechConfig = field(default_factory=SpeechConfig)
    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)
    plan: PlanConfig = field(default_factory=PlanConfig)
    company_knowledge: CompanyKnowledgeConfig = field(default_factory=CompanyKnowledgeConfig)


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
class ReportCover:
    title: str = ""
    interview_type: str = ""
    duration_text: str = ""
    generated_at: str = ""
    score: int = 0


@dataclass
class ReportHighlightBlock:
    alerts: list[str] = field(default_factory=list)
    advice: list[str] = field(default_factory=list)


@dataclass
class ReportDimensionCommentary:
    key: str
    title: str
    score: int
    commentary: str = ""


@dataclass
class ReportLearningPhase:
    title: str
    window: str = ""
    items: list[str] = field(default_factory=list)


@dataclass
class ReportLearningPlan:
    tags: list[str] = field(default_factory=list)
    phases: list[ReportLearningPhase] = field(default_factory=list)


@dataclass
class ReportQaAnalysis:
    question_index: int
    question: str
    answer: str = ""
    strengths: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    commentary: str = ""
    approach: list[str] = field(default_factory=list)
    reference_answer: str = ""


@dataclass
class InterviewReport:
    summary: str
    overall_score: int
    strengths: list[str]
    weaknesses: list[str]
    recommendations: list[str]
    exchanges: list[Exchange]
    dimension_scores: dict[str, DimensionAssessment] = field(default_factory=dict)
    cover: ReportCover = field(default_factory=ReportCover)
    highlights: ReportHighlightBlock = field(default_factory=ReportHighlightBlock)
    dimension_commentaries: list[ReportDimensionCommentary] = field(default_factory=list)
    learning_plan: ReportLearningPlan = field(default_factory=ReportLearningPlan)
    qa_analyses: list[ReportQaAnalysis] = field(default_factory=list)
    generation_source: str = "fallback"


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
