"""Session-start position matcher.

Match against the candidate's target-role text plus their actual JD.  The JD remains
optional and is never synthesized merely to make matching work.
"""

from __future__ import annotations

from interview.models import PositionConfig

_NAME_WEIGHT = 3
_KEYWORD_WEIGHT = 2


def match_position(
    positions: list[PositionConfig],
    *,
    jd_text: str = "",
    target_role: str = "",
) -> PositionConfig | None:
    """Return the position the candidate input genuinely matches, or None.

    - No positions → None (the planner generates everything from the JD).
    - Otherwise → the highest keyword/name score against role + JD (ties → earliest),
      but ONLY when it actually scores (> 0). If nothing in the bank matches the JD
      (e.g. a 产品经理 JD against a 后端 bank), return None so the interview runs off
      the candidate's own uploaded JD instead of a mismatched, residual position.
    """
    if not positions:
        return None
    haystack = f"{target_role or ''}\n{jd_text or ''}".lower()
    best = max(positions, key=lambda p: _score(p, haystack))
    return best if _score(best, haystack) > 0 else None


def _score(position: PositionConfig, haystack: str) -> int:
    total = 0
    name = (position.name or "").strip().lower()
    if name and name in haystack:
        total += _NAME_WEIGHT
    for keyword in position.match_keywords:
        kw = keyword.strip().lower()
        if kw and kw in haystack:
            total += _KEYWORD_WEIGHT
    return total
