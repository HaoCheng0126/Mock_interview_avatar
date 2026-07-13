"""Default LLM prompt templates and spoken phrases for the interview agent.

Every LLM prompt and spoken phrase the agent uses is defined here as an
overridable default. ``config/interview.yaml`` can override any of them via
the ``prompts:`` / ``speech:`` sections. Templates use ``{placeholder}``
substitution rendered by :func:`render_template`; unknown placeholders are
left as-is so a typo stays visible instead of silently disappearing.

Persona placeholders (available in every template):
  {interviewer_name} {interviewer_style} {interviewer_rules}
  {target_role} {candidate_background} {title} {duration_minutes}

Runtime placeholders (per template, filled at call time):
  evaluator          — {question} {answer} {rubric_dimensions} {transcript}
  follow_up_decider  — {payload}
  report             — {rubric_dimensions} {termination_reason} {transcript}
"""

from __future__ import annotations

from typing import Any


def render_template(template: str, mapping: dict[str, Any]) -> str:
    """Fill ``{placeholder}`` slots by literal replacement.

    Only keys present in ``mapping`` are substituted; anything else —
    including unknown placeholders and literal JSON braces in user-authored
    templates — is left untouched.
    """
    result = template
    for key, value in mapping.items():
        result = result.replace("{" + key + "}", str(value))
    return result


# ---------------------------------------------------------------------------
# LLM prompt templates
# ---------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPT = (
    "你是{interviewer_name}，一位专业面试官，面试风格：{interviewer_style}。\n"
    "面试规则：{interviewer_rules}\n"
    "候选人目标岗位：{target_role}；候选人背景：{candidate_background}。\n"
    "请始终输出结构化 JSON 评估。"
    "{knowledge_block}"
)

# Rendered into {knowledge_block} when knowledge entries exist; "" otherwise.
KNOWLEDGE_BLOCK_HEADER = "本场面试参考资料（提问、追问、评估和撰写报告时请结合使用）："
KNOWLEDGE_TRUNCATION_NOTE = "（参考资料过长，已截断）"
DEFAULT_KNOWLEDGE_MAX_CHARS = 6000

DEFAULT_EVALUATOR_PROMPT = (
    "请评估候选人的面试回答，输出 JSON。\n"
    "问题：{question}\n"
    "回答：{answer}\n"
    "评分维度：{rubric_dimensions}\n"
    "完整对话记录：{transcript}\n"
)

DEFAULT_FOLLOW_UP_DECIDER_PROMPT = (
    "请判断当前面试回答是否需要追问，输出 JSON。\n"
    "字段：needed, reason, missingSignal, followUpType, suggestedQuestion。\n"
    "followUpType 只能是 clarify/deepen/challenge/evidence/skip。\n"
    "完整对话记录和上下文：{payload}"
)

DEFAULT_REPORT_PROMPT = (
    "请根据完整面试对话记录生成最终面试评估报告，输出 JSON。\n"
    "每个维度必须给出 score、evidence、concerns、recommendations、confidence。\n"
    "评分维度：{rubric_dimensions}\n"
    "终止原因：{termination_reason}\n"
    "完整面试对话记录：{transcript}\n"
)

# ---------------------------------------------------------------------------
# Spoken phrases
# ---------------------------------------------------------------------------

DEFAULT_OPENING_TEMPLATE = (
    "你好，我是{interviewer_name}。"
    "今天我们先简单聊聊你和{target_role}这个方向的匹配度，"
    "大概会占用你 {duration_minutes} 分钟。"
    "我会从项目经历开始问，过程中如果有需要确认的地方，会顺着你的回答多问一两句。"
    "不用背答案，按你真实做过的事情讲就可以。"
)

DEFAULT_ANSWER_ACKNOWLEDGEMENTS = [
    "好，我听明白了，我先顺一下你刚才讲的。",
    "嗯，这个点我记下了，我稍微接着往下看。",
    "好的，你刚才这段信息挺关键的，我先整理一下。",
    "明白，我先把你这段回答放到上下文里看一下。",
]

DEFAULT_FINAL_ANSWER_ACKNOWLEDGEMENTS = [
    "好，这一题我先记下，等下我会把整体情况一起看一下。",
    "好的，这部分信息够了，我接下来会整体看一下你的回答。",
    "明白，这一段我记下了，我先把今天聊到的内容合在一起看一下。",
]

DEFAULT_FOLLOW_UP_PREFIXES = [
    "我想顺着这里多问一句。",
    "这里我想再确认一个细节。",
    "这个点我们稍微展开一下。",
    "我追一下刚才你提到的部分。",
]

DEFAULT_FIRST_QUESTION_TRANSITION = "我们先从第一个问题开始。"
DEFAULT_NEXT_QUESTION_TRANSITION = "好的，我们看下一个问题。"
DEFAULT_SKIP_TRANSITION = "没关系，这个问题我们先跳过。"
DEFAULT_CLOSING = "今天的模拟面试就到这里，稍后你可以查看完整反馈报告。"
DEFAULT_TERMINATION = "由于这次面试中没有收到足够的有效回答，本次面试将提前结束。"

DEFAULT_THINKING_CHECKS = [
    (20.0, "我看到你还在思考。你可以先从一个具体经历或关键决策讲起。"),
    (45.0, "这个问题可以再给你一点时间。如果暂时没有思路，也可以简单说明。"),
]

# ---------------------------------------------------------------------------
# Workflow defaults
# ---------------------------------------------------------------------------

DEFAULT_WORKFLOW = {
    "hard_timeout_seconds": 75.0,
    "opening_to_question_delay_seconds": 0.8,
    "prompt_playback_timeout_seconds": 30.0,
    "candidate_speech_grace_seconds": 8.0,
    "evaluation_join_timeout_seconds": 5.0,
    "max_skipped_questions": 3,
    "max_consecutive_skipped_questions": 2,
}
