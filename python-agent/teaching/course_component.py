"""Component message protocol for teaching agent -> frontend communication.

Agent pushes component messages via Live Avatar's send_custom_event.
Frontend renders components based on type and action fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# All valid component types
COMPONENT_TYPES = [
    "visual_card",
    "mindmap",
    "quiz",
    "quiz_result",
    "raise_hand",
    "interaction_prompt",
    "chapter_indicator",
    "lecture_progress",
    "encouragement",
    "classmate_message",    # AI classmate visible classroom message
    "microphone",           # Explicit microphone UI state hint
    "whiteboard_step",      # Step-by-step reasoning animation
    "whiteboard_compare",   # Side-by-side comparison (correct vs fallacy)
    "interactive_scene",    # Generic generated courseware scene
    "play_audio",           # Play external TTS audio (classmate voice)
]


@dataclass
class ComponentMessage:
    """A message pushed from agent to frontend to control teaching UI components.

    Attributes:
        type: Component type -- one of COMPONENT_TYPES.
        action: Lifecycle action -- "show", "hide", or "update".
        data: Component-specific payload (see per-type schema).
        timestamp: Unix timestamp in milliseconds.
    """

    type: str
    action: str
    data: dict = field(default_factory=dict)
    timestamp: int = 0
