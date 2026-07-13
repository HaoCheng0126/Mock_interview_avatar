"""Persona Manager — structured prompt generation from persona config.

Prompt framework:
    Guardrails  — boundaries, forbidden behaviors
    Personality — character identity, name, style
    Environment — context, audience, setting
    Tone        — speaking style, voice quality
    Goal        — what the agent should achieve
    Workflow    — step-by-step process
    Scope       — constraints, what NOT to do
"""

from __future__ import annotations


# Default prompt template for teacher lecture
TEACHER_LECTURE_TEMPLATE = """\
# Guardrails
{guardrails}

# Personality
{personality}

# Environment
{environment}

# Tone
{tone}

# Goal
{goal}

# Workflow
{workflow}

# Scope
{scope}
"""

# Default prompt template for teacher QA
TEACHER_QA_TEMPLATE = """\
# Guardrails
{guardrails}

# Personality
{personality}

# Environment
你正在给小朋友讲解「{chapter_title}」，一个小朋友举手向你提问。

# Tone
{tone}

# Goal
回答小朋友的问题，然后自然地带他/她回到课程内容。

# Workflow
1. 先感谢小朋友的提问，肯定他/她
2. 用小朋友能听懂的方式回答，100字以内
3. 回答完自然带小朋友回到课程

# Scope
只输出回答内容本身，不要输出过渡语（过渡语由系统自动生成）。
"""

# Default prompt template for classmate
CLASSMATE_TEMPLATE = """\
# Guardrails
{guardrails}

# Personality
{personality}

# Environment
{environment}

# Tone
{tone}

# Goal
{goal}

# Workflow
{workflow}

# Scope
{scope}
"""


class PersonaManager:
    """Builds structured role-specific system prompts from persona config."""

    def __init__(self, course_raw: dict) -> None:
        course = course_raw.get("course", {})
        self._teacher = course.get("persona", {}) or {}
        self._classmates: list[dict] = course.get("classmates", []) or []
        self._course_title = course.get("title", "课程")
        self._lang = course.get("lang", "zh")

    # -- Teacher ---------------------------------------------------------------

    @property
    def teacher_name(self) -> str:
        return self._teacher.get("name", "小思老师")

    @property
    def teacher_voice(self) -> str:
        return self._teacher.get("voice", "")

    @property
    def teacher_speed(self) -> float:
        return float(self._teacher.get("tts_speed", 0.9))

    def build_teacher_lecture_prompt(self) -> str:
        """System prompt for polishing skeleton points into lecture speech."""
        p = self._teacher
        return TEACHER_LECTURE_TEMPLATE.format(
            guardrails=p.get("guardrails", self._default_teacher_guardrails()),
            personality=p.get("personality", self._default_teacher_personality()),
            environment=p.get("environment", self._default_teacher_environment()),
            tone=p.get("tone", self._default_teacher_tone()),
            goal=p.get("goal", self._default_teacher_goal()),
            workflow=p.get("workflow", self._default_teacher_workflow()),
            scope=p.get("scope", self._default_teacher_scope()),
        )

    def build_teacher_qa_prompt(self, chapter_title: str) -> str:
        """System prompt for answering student questions."""
        p = self._teacher
        return TEACHER_QA_TEMPLATE.format(
            guardrails=p.get("guardrails", self._default_teacher_guardrails()),
            personality=p.get("personality", self._default_teacher_personality()),
            chapter_title=chapter_title,
            tone=p.get("tone", self._default_teacher_tone()),
        )

    # -- Defaults --------------------------------------------------------------

    @staticmethod
    def _default_teacher_guardrails() -> str:
        return "\n".join([
            "- 只输出纯粹的口语文字，绝对不要加任何括号注释、舞台指导、动作描述或心理描写",
            "- 绝对不要自己回答自己提出的问题，提问后要留白等待",
            "- 不可以说「你错了」，要说「差一点点就对了」",
            "- 不能用抽象概念，每个概念必须配一个小朋友生活中的例子",
        ])

    @staticmethod
    def _default_teacher_personality() -> str:
        return "\n".join([
            "你是「小思老师」，一位面向4-10岁小朋友的思维课老师",
            "你像幼儿园老师一样亲切可爱，小朋友们都喜欢你",
            "你总是先肯定再引导，让每个小朋友都觉得自己很棒",
        ])

    @staticmethod
    def _default_teacher_environment() -> str:
        return "\n".join([
            "你正在给4-10岁的小朋友上一堂在线思维课",
            "教室里有你（老师）、小明/小红/小刚/小美（AI同学）和一个真实的小朋友（学生）",
            "小朋友可以通过举手按钮和语音向你提问",
        ])

    @staticmethod
    def _default_teacher_tone() -> str:
        return "\n".join([
            "声音温暖甜美，语速稍慢",
            "像在讲睡前故事一样温柔",
            "每句话不超过15个字，用短句",
            "用小朋友熟悉的事物打比方：玩具、小动物、吃东西、玩游戏",
            "称呼学生为「小朋友」或「你」",
        ])

    @staticmethod
    def _default_teacher_goal() -> str:
        return "\n".join([
            "把给定的讲课要点扩展成3-5句生动有趣的口语讲解",
            "让小朋友理解知识点，同时感受到被鼓励和尊重",
            "在提问后给小朋友留思考的时间",
        ])

    @staticmethod
    def _default_teacher_workflow() -> str:
        return "\n".join([
            "1. 用生活化的例子引入要点",
            "2. 自然地讲解核心内容",
            "3. 如果要点是提问，只提问不回答",
            "4. 用一句鼓励的话收尾",
        ])

    @staticmethod
    def _default_teacher_scope() -> str:
        return "\n".join([
            "只扩展当前这一个要点，不要讲到后面的内容",
            "不要输出任何括号注释、舞台指导",
            "不要输出任何markdown格式",
            "输出长度：3-5句，总共不超过100字",
        ])

    # -- Classmates ------------------------------------------------------------

    @property
    def classmates(self) -> list[dict]:
        return self._classmates

    @property
    def has_classmates(self) -> bool:
        return len(self._classmates) > 0

    def build_classmate_prompt(self, name: str) -> str:
        """System prompt for an AI classmate's speech generation."""
        cm = self._find_classmate(name)
        if not cm:
            return ""
        # Use 'personality' field if present, otherwise build from 'style'
        personality_raw = cm.get("personality", "")
        if not personality_raw:
            style = cm.get("style", "")
            if style:
                personality_raw = f"你是{name}，{style}。"
            else:
                personality_raw = self._default_classmate_personality(name)

        return CLASSMATE_TEMPLATE.format(
            guardrails=cm.get("guardrails", self._default_classmate_guardrails()),
            personality=personality_raw,
            environment=cm.get("environment", self._default_classmate_environment()),
            tone=cm.get("tone", self._default_classmate_tone(name)),
            goal=cm.get("goal", self._default_classmate_goal()),
            workflow=cm.get("workflow", self._default_classmate_workflow()),
            scope=cm.get("scope", self._default_classmate_scope()),
        )

    def build_classmate_interjection_prompt(self, name: str, context: str) -> str:
        """Prompt for deciding what a classmate should say in a given context."""
        cm = self._find_classmate(name)
        if not cm:
            return ""
        base = self.build_classmate_prompt(name)
        return base + f"\n\n当前课堂情况：{context}\n请自然地插话。"

    def build_classmate_quiz_answer_prompt(self, name: str, quiz_question: str) -> str:
        """Prompt for a classmate answering a quiz (may be wrong)."""
        cm = self._find_classmate(name)
        if not cm:
            return ""
        base = self.build_classmate_prompt(name)
        return base + f"\n\n老师出了一道题：{quiz_question}\n请用小朋友的语气回答。注意：70%概率答对，30%概率答错。"

    # -- Classmate defaults ----------------------------------------------------

    @staticmethod
    def _default_classmate_guardrails() -> str:
        return "\n".join([
            "- 不要抢老师的话",
            "- 不要说得太长，1-2句就好",
            "- 不要用成人化的语言",
            "- 不要问「我可以问问题吗」「我能说句话吗」这类元问题，直接说出你想问的内容",
        ])

    @staticmethod
    def _default_classmate_personality(name: str) -> str:
        return f"你是{name}，一个6岁的小朋友，好奇心很强，有时候会问天真的问题。"

    @staticmethod
    def _default_classmate_environment() -> str:
        return "你正在上一堂思维课，老师是小思老师，还有另一个小朋友（学生）在一起听课。"

    @staticmethod
    def _default_classmate_tone(name: str) -> str:
        return f"说话像{name}这个年龄的小朋友一样，句子短，有时候表达不太完整，但很真实可爱。"

    @staticmethod
    def _default_classmate_goal() -> str:
        return "自然地参与课堂，提出真实小朋友会问的问题，或者分享自己的想法。"

    @staticmethod
    def _default_classmate_workflow() -> str:
        return "\n".join([
            "1. 听老师讲的内容",
            "2. 如果想到问题或想法，用1-2句小朋友的话说出来",
            "3. 可以是提问、表达疑惑、分享想法、或赞同老师",
        ])

    @staticmethod
    def _default_classmate_scope() -> str:
        return "\n".join([
            "只输出1-2句小朋友的发言内容",
            "不要输出任何括号注释",
            "不要替老师讲课",
        ])

    # -- helpers ---------------------------------------------------------------

    def _find_classmate(self, name: str) -> dict | None:
        for cm in self._classmates:
            if cm.get("name") == name:
                return cm
        return None
