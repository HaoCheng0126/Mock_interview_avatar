"""Spoken closing recap (总评).

When an interview completes normally, the avatar says a short, warm, out-loud review —
an overall impression plus one strength and one thing to improve. This is distinct from
the detailed written report (see ReportGenerator): it is spoken, brief, and encouraging.

It reuses the already-generated report's analysis (strengths/weaknesses/summary) instead
of re-reading the whole transcript, so it is one small LLM call at the very end.

Robust by design: with no LLM configured, or on any LLM/format failure, it returns "" and
the controller falls back to the plain closing line — the interview always ends cleanly.
"""

from __future__ import annotations

import logging

from interview.models import InterviewReport
from interview.prompts import DEFAULT_CLOSING_COMMENT_PROMPT, render_template

logger = logging.getLogger(__name__)

# A spoken sentence or two — keep it short so the model never rambles.
_MAX_TOKENS = 256


class ClosingCommentGenerator:
    def __init__(
        self,
        llm_client=None,
        *,
        prompt_template: str | None = None,
        context: dict | None = None,
    ) -> None:
        self._llm = llm_client
        self._prompt_template = prompt_template or DEFAULT_CLOSING_COMMENT_PROMPT
        self._context = dict(context or {})

    async def generate_async(
        self, report: InterviewReport | None, *, target_role: str = ""
    ) -> str:
        """Return a short spoken recap, or "" to fall back to the plain closing line."""
        if self._llm is None or report is None:
            return ""
        try:
            prompt = render_template(
                self._prompt_template,
                {
                    **self._context,
                    "target_role": (
                        target_role or self._context.get("target_role", "候选人")
                    ),
                    "overall_score": report.overall_score,
                    "summary": report.summary or "（无）",
                    "strengths": "；".join(report.strengths) or "（暂无明显亮点）",
                    "weaknesses": "；".join(report.weaknesses) or "（暂无明显短板）",
                },
            )
            return (await self._llm.generate_once(prompt, max_tokens=_MAX_TOKENS)).strip()
        except Exception as exc:  # noqa: BLE001 — any failure → plain closing line
            logger.warning(
                "closing comment generation failed, using plain closing: %s", exc
            )
            return ""
