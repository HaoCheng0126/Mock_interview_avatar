"""Default LLM prompt templates and spoken phrases for the interview agent.

Every LLM prompt and spoken phrase the agent uses is defined here as an
overridable default. ``config/interview.yaml`` can override any of them via
the ``prompts:`` / ``speech:`` sections. Templates use ``{placeholder}``
substitution rendered by :func:`render_template`; unknown placeholders are
left as-is so a typo stays visible instead of silently disappearing.

Persona placeholders (available in every template):
  {interviewer_name} {interviewer_style} {interviewer_rules}
  {target_role} {candidate_background} {title} {duration_minutes}
  {position} {core_competencies} {candidate_brief}
  {jd} {resume}  # compatibility aliases containing condensed fields, never raw text

Runtime placeholders (per template, filled at call time):
  evaluator          — {question} {answer} {transcript}
  follow_up_decider  — {payload}
  report             — {termination_reason} {transcript}
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
    "严格遵循当前任务指定的输出格式：规划、评估和判断任务只输出要求的 JSON；"
    "口头开场或收尾任务只输出自然语言，不要把两种格式混在一起。"
    "{knowledge_block}"
)

# Rendered into {knowledge_block} when knowledge entries exist; "" otherwise.
KNOWLEDGE_BLOCK_HEADER = "本场面试参考资料（提问、追问、评估和撰写报告时请结合使用）："
KNOWLEDGE_TRUNCATION_NOTE = "（参考资料过长，已截断）"
DEFAULT_KNOWLEDGE_MAX_CHARS = 6000

DEFAULT_EVALUATOR_PROMPT = (
    "你是{interviewer_name}，面试风格为：{interviewer_style}。\n"
    "请遵守面试规则：{interviewer_rules}。\n"
    "只围绕本场目标岗位、真实 JD、候选人画像和当前问题评估，不得引用其他岗位题库或残留技术方向。\n"
    "请评估候选人的面试回答，输出 JSON。\n"
    "问题：{question}\n"
    "回答：{answer}\n"
    "岗位要求/考察点：{core_competencies}\n"
    "完整对话记录：{transcript}\n"
)

DEFAULT_FOLLOW_UP_DECIDER_PROMPT = (
    "你是{interviewer_name}，面试风格为：{interviewer_style}。请遵守：{interviewer_rules}。\n"
    "请判断当前回答是否需要追问，只输出 JSON。\n"
    "本岗位核心考察点：{core_competencies}\n"
    "追问原则：只有当回答缺失关键要点、含糊或明显不清楚时才追问；覆盖充分则不追问。\n"
    "输出字段：\n"
    "- needed：是否追问（布尔）\n"
    "- suggestedQuestion：若 needed=true，给出“只包含一个问题”的追问句。\n"
    "格式要求（非常重要）：\n"
    "1. suggestedQuestion 必须只问一个问题。\n"
    "2. suggestedQuestion 中最多只能出现 1 个问号（必须使用中文全角“？”）。\n"
    "3. 不要在 suggestedQuestion 里再补第二个问题，不要出现两个“？”。\n"
    "完整对话记录和上下文（JSON 字符串）：{payload}"
)

REPORT_PROMPT_MODULE_ORDER = (
    "role_and_style",
    "cover_and_summary",
    "strengths_and_risks",
    "dimensions_overview",
    "dimension_commentary",
    "learning_plan",
    "qa_analysis",
    "output_contract",
)

# ---------------------------------------------------------------------------
# Report prompt modules — aligned 1:1 with the modular report-page layout.
# Each block below corresponds to a concrete section of the written report.
# ---------------------------------------------------------------------------

DEFAULT_REPORT_PROMPT_MODULES = {
    "role_and_style": (
        "你是一位资深、直率、见过大量候选人的面试官。\n"
        "基于 transcript 生成一份正式、克制、不讨好的书面报告，像在内部系统里给一个真实候选人写的评语。\n"
        "原则：\n"
        "- 只写候选人在面试中真实表现出来的内容，不编造经历、数据或结论。\n"
        "- 允许直接点出敷衍、回避、糊弄；不要为了体面而美化。\n"
        "- 允许口语化但保持专业，不要客套。\n"
        "- 当候选人没回答、跳过问题、答非所问、明显水时长、对错误结论无判断时，必须照实写。\n"
        "- 只输出 JSON，不要输出 Markdown、注释或额外说明。"
    ),
    "cover_and_summary": (
        "输出 `summary` 和 `overallScore`；报告封面由系统根据岗位、真实时长和生成时间统一生成，"
        "不要输出 `cover`。\n"
        "`overallScore` 为 0~100 的整数。\n"
        "`summary` 是报告最核心的总评段，必须像资深面试官对候选人的私下点评，2~4 句整段书面语，"
        "覆盖：\n"
        "- 候选人整体完成度和配合度。\n"
        "- 与目标岗位的匹配度。\n"
        "- 本场最值得肯定的一项。\n"
        "- 本场最需要敲打的一项。\n"
        "不要写「整体不错」「具备一定能力」「建议继续提升」这种空话；"
        "如果候选人没答几个题、明显糊弄、回避关键点、答非所问，要在 summary 中直接点出来，"
        "例如：候选人本场仅回答 1 道问题，对核心业务问题基本回避，态度比较敷衍，整体信息量不足以做出有效判断。"
    ),
    "strengths_and_risks": (
        "输出 `strengths`、`weaknesses`、`recommendations`、`highlights.alerts`、`highlights.advice`。\n"
        "`strengths` 写真实亮点；\n"
        "`weaknesses` 写具体短板，回答敷衍、回避、缺证据、明显错误时直接点出，不要客气；\n"
        "`recommendations` 写可执行改进。\n"
        "`highlights.alerts` 比 `weaknesses` 更短更锐利；`highlights.advice` 动词开头。\n"
        "全部基于 transcript，避免空话。"
    ),
    "dimensions_overview": (
        "输出 `dimensions`，只使用这 5 个 key，并保持顺序一致：\n"
        "1. communication_clarity（表达能力）：表达是否清楚、有条理，能否让人快速听明白。\n"
        "2. problem_solving（逻辑能力）：是否能拆解问题、建立判断、说明推理过程。\n"
        "3. outcome_orientation（结果导向）：候选人是否具备目标意识、推进意识与复盘意识，关注产出效果和持续优化。\n"
        "4. project_execution（项目展现力）：是否能讲清项目背景、职责、动作、结果与个人贡献。\n"
        "5. role_alignment（岗位契合度）：与目标岗位要求、工作场景和能力预期的匹配度。\n"
        "每个维度都输出 `score`、`evidence`、`concerns`、`recommendations`、`confidence`。\n"
        "`score` 为 0~10 整数，**必须严格基于候选人在 transcript 中的真实表现**，以下规则缺一不可：\n"
        "- **绝对不要给所有维度相同分数**；每个维度独立判断。\n"
        "- **绝对不要默认 7~8**；默认 7~8 等同于不评估。本场表现明显不达预期时，0~4 是合理结果。\n"
        "- 0~2：明显缺位、敷衍、回避、答非所问、沉默、跳题。\n"
        "- 3~4：有回应但缺证据、缺细节、缺深度，或只能讲结论讲不出过程。\n"
        "- 5~6：基础表达到位，有 1~2 个具体点，但亮点有限或闭环不全。\n"
        "- 7~8：有真实案例、有判断依据、能讲清过程与结果。\n"
        "- 9~10：结构清晰、证据充分、推演严密、岗位匹配度高（极少数情况）。\n"
        "- 候选人跳过 ≥2 道题时，对应维度按 0~2 处理；无回答样本的维度默认 0~1。\n"
        "其余字段必须基于 transcript。"
    ),
    "dimension_commentary": (
        "输出 `dimensionCommentaries` 数组，共 5 项，顺序固定为："
        "`communication_clarity` → `problem_solving` → `outcome_orientation` → "
        "`project_execution` → `role_alignment`。\n"
        "标题固定为：`表达能力` / `逻辑能力` / `结果导向` / `项目展现力` / `岗位契合度`。\n"
        "每项包含 `key`、`title`、`score`、`commentary`。\n"
        "`score` 必须与 `dimensions[key].score` 一致；`commentary` 写 35~80 字，"
        "先说表现，再说当前最关键缺口，如果候选人没答或敷衍，这里也要直接点出来。"
    ),
    "learning_plan": (
        "输出 `learningPlan.tags` 和 `learningPlan.phases`。\n"
        "`tags` 写 2~4 个短标签；`phases` 固定写 3 个阶段：立即行动、短期提升、中期规划，"
        "每阶段包含 `title`、`window`、`items`。\n"
        "`items` 要可执行，必须紧扣本场面试暴露的问题，不要写通用套话。"
    ),
    "qa_analysis": (
        "输出 `qaAnalyses`，按题目顺序逐题生成。\n"
        "每题只输出 `questionIndex`、`question`、`answer`、`score`、`strengths`、`risks`、`commentary`。\n"
        "**不要再单独输出 `referenceAnswer` 或 `approach` 字段**，参考思路已经融合到 `commentary` 里。\n"
        "`answer` 保留候选人关键回答（截断到 200 字内）；\n"
        "`commentary` 是本题的核心字段，**必须是一段完整融合文字**，结构如下：\n"
        "  第一段【面试官点评】（70~120 字）：基于候选人真实回答，给出对本题的直接评价，"
        "好就直说好，敷衍/回避/答非所问/无实质内容/水时长/对错误结论无判断等情况要照实点出，"
        "不要为了体面而美化，也不要用「整体不错」「具备一定能力」这种空话。\n"
        "  第二段【参考思路】（60~120 字）：给出本题的**具体**回答思路，"
        "**不要固定套用「背景—目标—动作—结果」**——应该根据本题考察点给出有针对性的步骤或结构，"
        "例如：\n"
        "    - 项目介绍题：先说项目背景与个人角色，再讲当时面对的关键约束与决策，最后用 1~2 个数据结果收尾。\n"
        "    - 业务方案题：先拆解目标和约束，再列 2~3 个候选方案与取舍依据，最后给出推荐方案与验证指标。\n"
        "    - 故障复盘题：先讲故障现象与影响范围，再讲定位链路（监控/日志/上游/下游），最后讲止血措施和长期治理。\n"
        "    - 行为面试题：用 1 个真实 STAR 案例讲清情境—任务—动作—结果，避免讲多个浅尝辄止的案例。\n"
        "  两段用换行分隔（`\\n\\n`），不要分多个 field。\n"
        "`score` 为 0~10 整数，必须根据候选人真实回答给分，0~2 表示敷衍/跳题/无实质内容，"
        "3~4 表示有回应但缺证据，5~6 表示基础到位但亮点有限，7~8 表示有案例有判断，9~10 极少。\n"
        "**不要默认 7~8**；敷衍/跳题时必须给 0~3。\n"
        "严格基于 transcript，不编造项目、指标或工具。"
    ),
    "output_contract": (
        "基础上下文：\n"
        "目标岗位核心考察点：{core_competencies}\n"
        "终止原因：{termination_reason}\n"
        "实际面试时长：{actual_duration_seconds}\n"
        "完整面试对话记录：{transcript}\n"
        "\n"
        "请按以下 JSON schema 输出，不要包含任何 Markdown、注释或额外文字：\n"
        "{\n"
        '  "summary": "2~4 句正式总评",\n'
        '  "overallScore": 0,\n'
        '  "strengths": ["..."],\n'
        '  "weaknesses": ["..."],\n'
        '  "recommendations": ["..."],\n'
        '  "highlights": {\n'
        '    "alerts": ["..."],\n'
        '    "advice": ["..."]\n'
        "  },\n"
        '  "dimensions": {\n'
        '    "communication_clarity": { "score": 0, "evidence": ["..."], "concerns": ["..."], "recommendations": ["..."], "confidence": "low|medium|high" },\n'
        '    "problem_solving": { "score": 0, "evidence": ["..."], "concerns": ["..."], "recommendations": ["..."], "confidence": "low|medium|high" },\n'
        '    "outcome_orientation": { "score": 0, "evidence": ["..."], "concerns": ["..."], "recommendations": ["..."], "confidence": "low|medium|high" },\n'
        '    "project_execution": { "score": 0, "evidence": ["..."], "concerns": ["..."], "recommendations": ["..."], "confidence": "low|medium|high" },\n'
        '    "role_alignment": { "score": 0, "evidence": ["..."], "concerns": ["..."], "recommendations": ["..."], "confidence": "low|medium|high" }\n'
        "  },\n"
        '  "dimensionCommentaries": [\n'
        '    { "key": "communication_clarity", "title": "表达能力", "score": 0, "commentary": "..." },\n'
        '    { "key": "problem_solving", "title": "逻辑能力", "score": 0, "commentary": "..." },\n'
        '    { "key": "outcome_orientation", "title": "结果导向", "score": 0, "commentary": "..." },\n'
        '    { "key": "project_execution", "title": "项目展现力", "score": 0, "commentary": "..." },\n'
        '    { "key": "role_alignment", "title": "岗位契合度", "score": 0, "commentary": "..." }\n'
        "  ],\n"
        '  "learningPlan": {\n'
        '    "tags": ["..."],\n'
        '    "phases": [\n'
        '      { "title": "立即行动", "window": "1~2 周", "items": ["...", "..."] },\n'
        '      { "title": "短期提升", "window": "1 个月", "items": ["..."] },\n'
        '      { "title": "中期规划", "window": "2~3 个月", "items": ["..."] }\n'
        "    ]\n"
        "  },\n"
        '  "qaAnalyses": [\n'
        "    {\n"
        '      "questionIndex": 1,\n'
        '      "question": "...",\n'
        '      "answer": "...",\n'
        '      "score": 0,\n'
        '      "strengths": ["..."],\n'
        '      "risks": ["..."],\n'
        '      "commentary": "【面试官点评】...\\n\\n【参考思路】..."\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "额外要求：\n"
        "1. `overallScore` 必须是 0~100 的整数。\n"
        "2. 各维度 `score` 都必须是 0~10 的整数，**严禁默认 5 以上 / 默认 7~8 / 全部相同**；"
        "必须根据真实回答独立判断；敷衍/跳题时按 0~3 处理。\n"
        "3. `summary` / `weaknesses` / `recommendations` / `qaAnalyses.commentary` 中若涉及具体项目、数据或工具，"
        "必须在 transcript 中能找到依据；找不到时改写为更通用的能力描述。\n"
        "4. `qaAnalyses.commentary` 必须是一段融合文字，**不要再输出 `referenceAnswer` 或 `approach` 字段**。\n"
        "5. 除上述 JSON 外，不要输出任何额外说明、Markdown 或代码块。"
    ),
}


def build_report_prompt_from_modules(modules: dict[str, str]) -> str:
    """Assemble modular report prompt blocks into the runtime prompt text."""
    return "\n\n".join(
        str((modules or {}).get(key) or "").strip()
        for key in REPORT_PROMPT_MODULE_ORDER
        if str((modules or {}).get(key) or "").strip()
    )


DEFAULT_REPORT_PROMPT = build_report_prompt_from_modules(DEFAULT_REPORT_PROMPT_MODULES)

REPORT_OVERVIEW_OUTPUT_CONTRACT = (
    "只输出完整综合分析 JSON，不要输出逐题分析、Markdown 或额外说明：\n"
    "报告封面由系统根据岗位、真实时长和生成时间统一生成，不要输出 cover。\n"
    "{\n"
    ' "summary":"基于真实回答的总评，100字内","overallScore":0,\n'
    ' "evidenceRefs":["本场输入中真实存在的exchangeId"],\n'
    ' "strengths":["具体优势"],"weaknesses":["具体短板"],'
    '"recommendations":["可执行建议"],\n'
    ' "highlights":{"alerts":["最需注意的风险"],"advice":["最优先的建议"]},\n'
    ' "dimensions":{\n'
    '  "communication_clarity":{"score":0,"evidence":"40字内证据","concern":"40字内缺口","confidence":"low|medium|high"},\n'
    '  "problem_solving":{"score":0,"evidence":"40字内证据","concern":"40字内缺口","confidence":"low|medium|high"},\n'
    '  "outcome_orientation":{"score":0,"evidence":"40字内证据","concern":"40字内缺口","confidence":"low|medium|high"},\n'
    '  "project_execution":{"score":0,"evidence":"40字内证据","concern":"40字内缺口","confidence":"low|medium|high"},\n'
    '  "role_alignment":{"score":0,"evidence":"40字内证据","concern":"40字内缺口","confidence":"low|medium|high"}},\n'
    ' "dimensionCommentaries":[{"key":"communication_clarity","title":"表达能力","score":0,"commentary":"维度点评"}],\n'
    ' "learningPlan":{"tags":["提升标签"],"phases":['
    '{"title":"立即行动","window":"1-2周","items":["具体行动"]},'
    '{"title":"短期提升","window":"1个月","items":["专项练习"]},'
    '{"title":"中期规划","window":"2-3个月","items":["能力沉淀"]}]}\n'
    "}\n"
    "分数规则：overallScore 为0~100；维度0~10，禁止全部同分或默认7~8。\n"
    "优势、短板、建议各最多3项，每项控制在35字内；五个核心维度都必须输出点评。\n"
    "learningPlan 至少包含立即行动、短期提升、中期规划三个阶段。\n"
    "目标岗位：{target_role}\n岗位考察点：{core_competencies}\n"
    "终止原因：{termination_reason}\n实际时长：{actual_duration_seconds}\n"
    "完整对话：{transcript}"
)

REPORT_QA_OUTPUT_CONTRACT = (
    "只输出逐题分析 JSON，不要输出综合报告、维度雷达或学习计划，不要输出 Markdown：\n"
    '{"qaAnalyses":[{"questionIndex":1,"question":"题目","answer":"候选人关键回答，200字内",'
    '"score":0,"strengths":["本题真实亮点"],"risks":["本题具体风险"],'
    '"commentary":"【面试官点评】直接评价真实回答。\\n\\n【参考思路】针对本题给出具体回答路径。"}]}\n'
    "每道主问题和追问都按对话顺序分析；score为0~10。不得编造对话中没有的项目或数据。\n"
    "岗位考察点：{core_competencies}\n完整对话：{transcript}"
)


def build_report_overview_prompt(modules: dict[str, str]) -> str:
    keys = (
        "role_and_style",
    )
    body = "\n\n".join(
        str((modules or {}).get(key) or "").strip()
        for key in keys
        if str((modules or {}).get(key) or "").strip()
    )
    return f"{body}\n\n{REPORT_OVERVIEW_OUTPUT_CONTRACT}".strip()


def build_report_qa_prompt(modules: dict[str, str]) -> str:
    body = "\n\n".join(
        str((modules or {}).get(key) or "").strip()
        for key in ("role_and_style", "qa_analysis")
        if str((modules or {}).get(key) or "").strip()
    )
    return f"{body}\n\n{REPORT_QA_OUTPUT_CONTRACT}".strip()


DEFAULT_REPORT_OVERVIEW_PROMPT = build_report_overview_prompt(
    DEFAULT_REPORT_PROMPT_MODULES
)
DEFAULT_REPORT_QA_PROMPT = build_report_qa_prompt(DEFAULT_REPORT_PROMPT_MODULES)

# Spoken closing recap (总评): a short, warm, out-loud review the avatar says at the very
# end, distinct from the detailed written report above. Runtime placeholders come from the
# generated report: {overall_score} {summary} {strengths} {weaknesses} — plus persona
# placeholders ({interviewer_name} {target_role} …).
DEFAULT_CLOSING_COMMENT_PROMPT = (
    "你是{interviewer_name}，刚刚结束了对一位应聘{target_role}的候选人的模拟面试。\n"
    "请你用面试官的口吻，对候选人当面做一个简短的口头复盘，直接对候选人说（用「你」）。\n"
    "要求：\n"
    "1. 2~3 句话，像面试结束时当面说的收尾点评，自然、真诚、以鼓励为主。\n"
    "2. 先给一句整体印象，再点出一个亮点和一个可以改进的地方。\n"
    "3. 只说人话，不要出现分数、维度名称、JSON、条目符号或「报告」等字样。\n"
    "供你参考的本场评估要点（不要照读）：\n"
    "整体印象：{summary}\n"
    "亮点：{strengths}\n"
    "待改进：{weaknesses}\n"
)

# Session-start planner: turns the one-shot compact brief (+ bank) into the question list.
# Runtime placeholders: {candidate_brief} {target_role} {position} {core_competencies}
#                       {business_questionlist} {resume_experiences} {business_questions}
DEFAULT_PLANNER_PROMPT = (
    "你是{interviewer_name}，一位资深面试官，面试风格为：{interviewer_style}。\n"
    "面试规则：{interviewer_rules}\n"
    "请保持该面试官的专业侧重点和表达方式，为候选人规划问题清单，只输出 JSON。\n"
    "候选人目标岗位：{target_role}\n"
    "候选人结构化画像（实习/项目可能带对应 source_excerpt 原文证据）：{candidate_brief}\n"
    "本岗位核心考察点（出题时请尽量覆盖这些点）：{core_competencies}\n"
    "已有业务题库（可参考、可直接选用，按与画像的相关性优先）：{business_questionlist}\n\n"
    "本场完整岗位与简历原文：\n{source_material}\n\n"
    "总体流程要求：\n"
    "A. 简历环节：从 internships / projects 中挑出与目标岗位最相关的 {resume_experiences} 段经历（若只有 1 段，就只用 1 段）。\n"
    "   - 对每段经历先问“项目介绍”一个问题；如果候选人回答过短、答非所问或缺少个人职责，可以按简历追问上限继续追问。\n"
    "   - 然后结合该条目的 source_excerpt 原文证据与岗位考察点，提出 1~2 个具体问题；不要把别的经历混进来。\n"
    "B. 业务题环节：给出 {business_questions} 道开放式业务/专业问题，按与画像的相关性排序；优先从题库选用，题库覆盖不足时再根据画像生成。\n"
    "   - 每道题必须能追溯到本场目标岗位、实际 JD、候选人经历或已匹配题库中的至少一项。\n"
    "   - 没有 JD 时，只能依据目标岗位名称和候选人真实经历出题，不得猜测岗位技术栈。\n"
    "   - 题库为空代表没有匹配到岗位题库，严禁引用其他岗位或其他专业方向的残留题。\n"
    "C. 所有问题必须一次只问一个问题。\n"
    "   - 每条 prompt 必须只包含一个问题。\n"
    "   - 每条 prompt 中最多只能出现 1 个问号（必须使用中文全角“？”），不要出现两个“？”。\n\n"
    "输出 JSON 格式：\n"
    "{\n"
    '  "resumeQuestions": [\n'
    "    {\n"
    '      "experienceId": "internship_1 或 project_1",\n'
    '      "experienceRef": "逐字引用公司/岗位或项目名称",\n'
    '      "projectIntro": { "prompt": \"必须明确点名这段经历的问题？\" },\n'
    '      "coreQuestions": [\n'
    '        { "prompt": \"...？\", "competency": \"...\", "expectedSignals": ["...", "..."] },\n'
    "        ...（最多 2 条）\n"
    "      ]\n"
    "    }\n"
    "  ],\n"
    '  "businessQuestions": [\n'
    '    { "bankId": "题库id" } 或 { "prompt": "...？", "competency": "...", "expectedSignals": ["...", "..."] }\n'
    "  ]\n"
    "}\n"
    "补充要求：\n"
    "1. 每个 resumeQuestions 必须填写 experienceId；画像目录为空时，experienceRef 必须逐字引用简历里的真实公司名或项目名。禁止使用“实习1”“项目1”“第一段经历”或单独的岗位名称作为引用。\n"
    "2. projectIntro 和 coreQuestions 必须明确绑定该经历。口播统一使用自然句式“你在公司/产品名担任岗位时，……？”；没有公司名时才使用简短项目名。禁止朗读部门、日期、表格整行、Markdown 星号或完整超长项目标题，也禁止写“选择一段经历”“介绍一个项目”等泛化问题。\n"
    "3. projectIntro 的 prompt 只问候选人介绍该项目，不夹带第二个问题。\n"
    "4. coreQuestions 的 prompt 必须紧扣画像中该经历与岗位的匹配点，避免泛泛而谈。\n"
    "5. businessQuestions 优先 bankId 引用题库；若自行生成，必须明确服务于目标岗位，且是开放式问题（避免是/否题）。\n"
    "6. 除 JSON 外不要输出任何文字。"
)

# Interview-plan defaults (used by InterviewPlanner; made configurable in a later phase).
DEFAULT_RESUME_EXPERIENCES = 2
DEFAULT_BUSINESS_QUESTIONS = 3
DEFAULT_RESUME_FOLLOWUPS = 1
DEFAULT_BUSINESS_FOLLOWUPS = 1
DEFAULT_SELF_INTRO_FOLLOWUPS = 0
DEFAULT_SELF_INTRO_FOLLOWUPS_NO_RESUME = 0
DEFAULT_SELF_INTRO_PROMPT = (
    "请你先做个简单的自我介绍，重点讲讲你最近负责的方向和最有代表性的一两段经历。"
)
DEFAULT_BUSINESS_FALLBACK_QUESTIONS = [
    "结合你的理解，你认为目标岗位最核心的目标和职责是什么？",
    "请讲一个最能体现你胜任目标岗位的真实案例？",
    "如果你接手一个目标不够清晰、资源有限的任务，你会如何推进？",
]

# Aggregated plan defaults for the console form (mirrors PlanConfig, the same way
# DEFAULT_WORKFLOW mirrors WorkflowConfig). Kept here so hub/interview_config can
# strip form fields back to defaults without importing the dataclass.
DEFAULT_PLAN = {
    "resume_experiences": DEFAULT_RESUME_EXPERIENCES,
    "business_questions": DEFAULT_BUSINESS_QUESTIONS,
    "resume_followups": DEFAULT_RESUME_FOLLOWUPS,
    "business_followups": DEFAULT_BUSINESS_FOLLOWUPS,
    "self_intro_followups": DEFAULT_SELF_INTRO_FOLLOWUPS,
    "self_intro_followups_no_resume": DEFAULT_SELF_INTRO_FOLLOWUPS_NO_RESUME,
}

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

DEFAULT_PREP_TEMPLATE = (
    "你好，我是{avatar_name}。"
    "你可以先在右侧填写岗位名称、岗位 JD 和简历。"
    "准备好后点击开始面试，我们就正式开始。"
)

DEFAULT_ANSWER_ACKNOWLEDGEMENTS = []

DEFAULT_FINAL_ANSWER_ACKNOWLEDGEMENTS = []

DEFAULT_FOLLOW_UP_PREFIXES = [
    "我想顺着这里多问一句。",
    "这里我想再确认一个细节。",
    "这个点我们稍微展开一下。",
    "我追一下刚才你提到的部分。",
]

DEFAULT_FIRST_QUESTION_TRANSITION = "我们先从第一个问题开始。"
DEFAULT_NEXT_QUESTION_TRANSITIONS = [
    "好的，这部分我基本了解了。我们再聊聊另一个方面。",
    "明白了。接下来我想了解一下你在另一个场景中的做法。",
    "好，这个问题先到这里。我们继续往下聊。",
    "清楚了。下面我们换一个角度。",
    "好的，我记下了。接下来再看一个更具体的情况。",
    "明白。我们接着聊聊你在其他场景中的处理方式。",
]
# Backward-compatible symbol for integrations that still expect one phrase.
DEFAULT_NEXT_QUESTION_TRANSITION = DEFAULT_NEXT_QUESTION_TRANSITIONS[0]
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

# When true, in some flows we add a short acknowledgement before passing the
# turn. Kept off by default — we don't want filler chatter before the next
# question.

DEFAULT_WORKFLOW = {
    "hard_timeout_seconds": 75.0,
    "opening_to_question_delay_seconds": 0.8,
    "prompt_playback_timeout_seconds": 30.0,
    "candidate_speech_grace_seconds": 8.0,
    "evaluation_join_timeout_seconds": 5.0,
    "foreground_evaluation_timeout_seconds": 5.0,
    "max_skipped_questions": 3,
    "max_consecutive_skipped_questions": 2,
}
