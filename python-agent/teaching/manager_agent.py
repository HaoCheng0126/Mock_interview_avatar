"""Manager Agent — adaptive classroom pacing via lightweight LLM decisions.

After each knowledge point, decides: continue, ask question, let classmate
speak, re-explain, or skip. Uses a ~200 token prompt for sub-second latency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MANAGER_SYSTEM_PROMPT = """\
你是课堂调度员。根据当前课堂状态，决定下一步做什么。

可选动作:
- CONTINUE: 继续讲下一个知识点
- ASK_QUESTION: 老师向学生提一个检查理解的问题
- CLASSMATE_SPEAK: 让AI同学发言活跃气氛
- RE_EXPLAIN: 换个方式重新讲解当前知识点
- SKIP: 跳过本章剩余内容

决策依据:
- 学生提问多 → 可能需要RE_EXPLAIN
- 学生一直沉默 → ASK_QUESTION或CLASSMATE_SPEAK
- 本章时间太长 → SKIP
- 正常推进 → CONTINUE

只输出动作名称，不要解释。"""


@dataclass
class ManagerState:
    chapter_id: str = ""
    knowledge_index: int = 0
    knowledge_total: int = 0
    student_questions_in_chapter: int = 0
    student_quiz_correct: bool | None = None
    elapsed_seconds: float = 0.0
    classmate_recently_spoke: bool = False


@dataclass
class ManagerDecision:
    action: str = "CONTINUE"  # CONTINUE | ASK_QUESTION | CLASSMATE_SPEAK | RE_EXPLAIN | SKIP
    reason: str = ""


class ManagerAgent:
    """Lightweight LLM-based decision maker for classroom pacing."""

    def __init__(self, llm_client) -> None:
        self._llm = llm_client
        self._state = ManagerState()
        self._decision_count = 0

    def update_state(self, **kwargs) -> None:
        """Update manager state fields."""
        for key, value in kwargs.items():
            if hasattr(self._state, key):
                setattr(self._state, key, value)

    async def decide(self) -> ManagerDecision:
        """Given current classroom state, decide what to do next."""
        self._decision_count += 1

        # Fast path: mostly CONTINUE to avoid LLM cost
        state = self._state

        # If student asked 2+ questions in this chapter → RE_EXPLAIN
        if state.student_questions_in_chapter >= 2:
            return ManagerDecision("RE_EXPLAIN", "student asked multiple questions")

        # If chapter running long (>4 min) and not at start → SKIP
        if state.elapsed_seconds > 240 and state.knowledge_index > 0:
            return ManagerDecision("SKIP", "chapter running long")

        # If student has been quiet mid-chapter, check understanding.
        if (
            state.student_questions_in_chapter == 0
            and state.classmate_recently_spoke
            and 0 < state.knowledge_index < max(state.knowledge_total - 1, 0)
        ):
            return ManagerDecision("ASK_QUESTION", "check quiet student's understanding")

        # Ensure a classmate moment before a chapter can finish.
        if (
            not state.classmate_recently_spoke
            and state.knowledge_total > 0
            and state.knowledge_index >= state.knowledge_total - 1
        ):
            return ManagerDecision("CLASSMATE_SPEAK", "classmate has not spoken this chapter")

        # If classmate hasn't spoken recently and 30% chance → CLASSMATE_SPEAK
        if not state.classmate_recently_spoke and state.knowledge_index > 0:
            import random
            if random.random() < 0.30:
                return ManagerDecision("CLASSMATE_SPEAK", "engage classmate")

        # Default: CONTINUE
        return ManagerDecision("CONTINUE", "normal progress")

    @property
    def state(self) -> ManagerState:
        return self._state
