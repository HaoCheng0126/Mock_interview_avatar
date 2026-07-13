"""Course Generator — two-stage LLM course generation with age-adaptive strategy.

Stage 1: Generate structured outline (chapters + knowledge points)
Stage 2: Expand each chapter (skeleton scripts + quiz + interaction)

Age profiles drive differentiated teaching strategies:
  4-6  → 动作和游戏主导  (action & game)
  7-8  → 故事与线索主导  (story & detective)
  9-10 → 策略与深度互动  (strategy & PK)

Outputs a valid course YAML file ready for teaching.agent.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import re
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Age Profiles — cognitive characteristics → teaching strategy
# ---------------------------------------------------------------------------

AGE_PROFILES = {
    "4-6": {
        "label": "幼儿园中大班",
        "cognitive": "具象思维为主，注意力只有5-10分钟，喜欢具象的动作和声音",
        "strategy": "动作和游戏主导",
        "tts_speed": 0.85,
        "max_words_per_sentence": 10,
        "chapter_count": 2,
        "skeleton_points_per_chapter": 2,
        "quiz_options": 3,
        "stage1_extra": (
            "- 设计游戏化课程，每章围绕一个小游戏或身体动作展开\n"
            "- 用具体的、可触摸的事物打比方（水果、玩具、动物叫声）\n"
            "- 大量正向情绪反馈元素：击掌、跳舞、放烟花、小星星\n"
            "- 知识点要非常简单具体，孩子能立刻理解并模仿"
        ),
        "quiz_extra": (
            "选项用具体的图片化描述，配emoji。错误的选项要明显不合理"
            "（让小朋友容易答对，建立自信）。用\"大家一起做动作\"的方式出题。"
        ),
        "interaction_extra": (
            "引导孩子动手操作或模仿动作（如：跟老师一起拍三下手！）。"
            "问题是具象的，不用抽象词汇。期待答案是动作或单词。"
        ),
        "persona_tone": (
            "- 声音夸张甜美，尾音上扬，像动画片里的角色\n"
            "- 语速很慢，每句话不超过10个字\n"
            "- 大量使用「哇！」「太棒了！」「击个掌！」「抱抱！」等情绪化表达\n"
            "- 多用叠词和拟声词：咚咚咚、哗啦啦、喵喵喵\n"
            "- 称呼学生为「宝贝」或「小可爱」"
        ),
        "persona_goal": (
            "- 通过游戏和动作让小朋友参与进来\n"
            "- 把每个知识点变成一个可以动手做的小任务\n"
            "- 让小朋友觉得自己在\"玩\"而不是在\"学\"\n"
            "- 在提出互动指令后给小朋友充分的反应时间"
        ),
        "persona_workflow": (
            "- 1. 夸张的打招呼（比如：小可爱～我们来玩一个超好玩的游戏！）\n"
            "- 2. 用身体动作引入要点（比如：来，跟老师一起拍拍手！）\n"
            "- 3. 慢慢讲出核心内容\n"
            "- 4. 击掌/跳舞/放烟花庆祝"
        ),
        "persona_scope": (
            "- 每句话不超过10个字\n"
            "- 每章内容控制在2分钟内讲完\n"
            "- 不要连续讲超过3句话，中间留白让孩子跟上\n"
            "- 输出长度：2-3句，总共不超过50字"
        ),
    },
    "7-8": {
        "label": "小学低年级",
        "cognitive": "具象向形象思维过渡，开始适应课堂规则，自尊心增强，怕挫败",
        "strategy": "故事与线索主导",
        "tts_speed": 0.88,
        "max_words_per_sentence": 15,
        "chapter_count": 3,
        "skeleton_points_per_chapter": 3,
        "quiz_options": 3,
        "stage1_extra": (
            "- 用故事化线索串联章节：老师化身侦探/队长，带领孩子\"破案\"或\"探险\"\n"
            "- 知识点隐藏在故事情节里，通过解谜的方式呈现\n"
            "- 自尊心强的阶段：任何错误都要包装成\"发现了一个新线索！\"\n"
            "- 用绘本式的语言风格，每章有一个小故事"
        ),
        "quiz_extra": (
            "用故事场景出题（比如：小侦探，证据A和证据B哪个是对的？）。"
            "错误选项要有\"好像对但其实不对\"的迷惑性，但配上成长型思维的鼓励。"
        ),
        "interaction_extra": (
            "用角色扮演的方式提问（比如：如果你是侦探，你会先查哪个线索？）。"
            "鼓励孩子说出自己的想法，即使说错也要肯定勇气。"
        ),
        "persona_tone": (
            "- 声音温暖有力，像会讲故事的姐姐/哥哥\n"
            "- 语速适中，每句话不超过15个字\n"
            "- 多用角色化和情景化表达：「小侦探们」「队员们」「我们接到一个新任务！」\n"
            "- 做错时的鼓励话术：「差一点就猜对了，我们看看另一个线索？」\n"
            "- 称呼学生为「小侦探」或「小队员」"
        ),
        "persona_goal": (
            "- 把讲课变成一次探险或破案任务\n"
            "- 让小朋友在故事中自然地学到知识点\n"
            "- 做错时给予成长型思维的鼓励，保护自尊心\n"
            "- 在提出思考问题后给小朋友留思考的时间"
        ),
        "persona_workflow": (
            "- 1. 用故事情境开场（比如：小侦探们，今天接到一个新案子！）\n"
            "- 2. 在故事推进中自然引出要点\n"
            "- 3. 提出一个需要\"破案\"的思考问题\n"
            "- 4. 不管答案如何都先肯定，然后引导思考"
        ),
        "persona_scope": (
            "- 每句话不超过15个字\n"
            "- 每个要点展开成3-4句\n"
            "- 不要输出任何括号注释或舞台指导\n"
            "- 输出长度：3-4句，总共不超过80字"
        ),
    },
    "9-10": {
        "label": "小学中高年级",
        "cognitive": "初级抽象逻辑思维，喜欢挑战，追求掌控感和聪明感",
        "strategy": "策略与深度互动",
        "tts_speed": 0.92,
        "max_words_per_sentence": 20,
        "chapter_count": 4,
        "skeleton_points_per_chapter": 3,
        "quiz_options": 4,
        "stage1_extra": (
            "- 设计有挑战性的内容，孩子喜欢\"难一点\"的东西来证明自己聪明\n"
            "- 可以引入\"PK模式\"：让孩子和AI同学比赛谁先答对\n"
            "- 鼓励批判性思考：\"你觉得这个故事里谁做错了？为什么？\"\n"
            "- 知识递进明显：从简单到复杂，每章有\"升级\"的感觉"
        ),
        "quiz_extra": (
            "题目要有一定的迷惑性和挑战性。可以设计4个选项，其中包含\"看似对但不对\"的陷阱项。"
            "答对后强调\"你太聪明了\"，答错后追问\"你是怎么想的？\"引导表达思考过程。"
        ),
        "interaction_extra": (
            "通过追问引导孩子用语言表达思考过程：\"你是怎么想的？\"\"为什么选这个？\""
            "可以设计两难情境或开放性问题，没有标准答案，鼓励多角度思考。"
        ),
        "persona_tone": (
            "- 声音清晰有力，语速可以适当加快\n"
            "- 每句话不超过20个字，但可以用更复杂的句式和逻辑词\n"
            "- 用\"挑战者\"语气：敢不敢试试？看看你能不能比老师先想到？\n"
            "- 称赞话术强调聪明感：「这个思路很厉害！」「你是怎么想到的？」\n"
            "- 称呼学生为「小挑战者」或「小天才」"
        ),
        "persona_goal": (
            "- 挑战孩子的思维能力，让他们觉得\"我变聪明了\"\n"
            "- 通过追问和PK激发思考深度\n"
            "- 鼓励孩子用语言表达自己的思考过程\n"
            "- 不满足于正确答案，追问\"为什么\""
        ),
        "persona_workflow": (
            "- 1. 抛出一个有趣的挑战或谜题\n"
            "- 2. 引导孩子推理和发现\n"
            "- 3. 追问「你怎么想的？」让孩子表达思维过程\n"
            "- 4. 总结+升级：「下一关更难，敢不敢来？」"
        ),
        "persona_scope": (
            "- 每句话不超过20个字\n"
            "- 每个要点展开成4-5句\n"
            "- 可以使用简单的逻辑连接词（因为/所以/但是）\n"
            "- 输出长度：4-5句，总共不超过120字"
        ),
    },
}


def _resolve_profile(age: str) -> dict:
    """Map age string to the closest profile. Falls back to 7-8 for broad ranges."""
    age = age.strip()
    # Direct match
    if age in AGE_PROFILES:
        return AGE_PROFILES[age]
    # Parse numeric range, pick the midpoint
    m = re.match(r'(\d+)\s*-\s*(\d+)', age)
    if m:
        low, high = int(m.group(1)), int(m.group(2))
        mid = (low + high) / 2
        if mid <= 5.5:
            return AGE_PROFILES["4-6"]
        elif mid <= 8.5:
            return AGE_PROFILES["7-8"]
        else:
            return AGE_PROFILES["9-10"]
    # Single number
    m = re.match(r'(\d+)', age)
    if m:
        a = int(m.group(1))
        if a <= 6:
            return AGE_PROFILES["4-6"]
        elif a <= 8:
            return AGE_PROFILES["7-8"]
        else:
            return AGE_PROFILES["9-10"]
    # Fallback
    return AGE_PROFILES["7-8"]


# ---------------------------------------------------------------------------
# Prompt templates — {age_extra} injected from profile
# ---------------------------------------------------------------------------

STAGE1_TEMPLATE = """\
你是儿童课程设计师。为以下主题设计一堂面向{age_label}（{age}岁）儿童的课程。

## 年龄段特征
认知特点：{cognitive}
教学策略：{strategy}

## 设计要求
{age_extra}

## 课程信息
主题：{topic}
语言：{lang}
期望章节数：{chapter_count}
每章要点数：{skeleton_points}

## 输出格式（严格JSON，不要markdown代码块）
{{
  "title": "课程标题（有趣、吸引小朋友）",
  "chapters": [
    {{
      "id": "chapter_1",
      "title": "章节标题",
      "skeleton": ["要点1", "要点2"],
      "has_quiz": false,
      "has_interaction": true
    }}
  ]
}}

要求：
- skeleton每个要点{skeleton_hint}
- 至少1章有quiz，至少1章有interaction
- 章节间有逻辑递进关系
- 标题和内容必须符合「{strategy}」的教学策略
"""

STAGE2_QUIZ_TEMPLATE = """\
为章节"{chapter_title}"设计一道选择题。

课程主题：{course_title}
年龄：{age_label}（{age}岁）
认知特点：{cognitive}

## 出题要求
{quiz_extra}

## 输出格式（严格JSON）
{{
  "question": "题目（简洁有趣）",
  "options": [
    {{"key": "A", "text": "选项A", "correct": true}},
    {{"key": "B", "text": "选项B", "correct": false}},
    {{"key": "C", "text": "选项C", "correct": false}}
  ],
  "explanation_correct": "答对时的鼓励解释",
  "explanation_wrong": "答错时的引导解释"
}}
"""

STAGE2_INTERACTION_TEMPLATE = """\
为章节"{chapter_title}"设计一个开放式互动提问。

课程主题：{course_title}
年龄：{age_label}（{age}岁）
教学策略：{strategy}

## 互动设计要求
{interaction_extra}

## 输出格式（严格JSON）
{{
  "prompt": "向小朋友提出的问题或指令（15-30字）",
  "expect_keywords": ["关键词1", "关键词2"]
}}
"""


# ---------------------------------------------------------------------------
# CourseGenerator
# ---------------------------------------------------------------------------

class CourseGenerator:
    """Generates complete course YAML from a topic description."""

    def __init__(self, llm_client, output_dir: Path) -> None:
        self._llm = llm_client
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)

    async def generate(
        self,
        topic: str,
        age: str = "4-10",
        lang: str = "zh",
        chapter_count: int | None = None,
    ) -> dict:
        """Two-stage generation. Returns {"course_name": str, "chapters": int, "path": str}.

        If chapter_count is not specified, it's auto-calculated from the age profile.
        """
        profile = _resolve_profile(age)
        if chapter_count is None:
            chapter_count = profile["chapter_count"]

        logger.info("📝 Stage 1: Generating outline for '%s' (age=%s, strategy=%s)",
                     topic, profile["label"], profile["strategy"])

        # Stage 1 — Outline
        outline = await self._generate_outline(topic, age, lang, chapter_count, profile)
        course_title = outline.get("title", topic)
        chapters = outline.get("chapters", [])
        logger.info("📋 Outline: %d chapters", len(chapters))

        # Stage 2 — Expand each chapter (with 2s delay between calls to avoid rate limit)
        logger.info("📝 Stage 2: Expanding %d chapters", len(chapters))
        for i, ch in enumerate(chapters):
            if i > 0:
                await asyncio.sleep(2)  # DeepSeek rate limit guard
            if ch.get("has_quiz"):
                quiz = await self._generate_quiz(ch["title"], course_title, age, profile)
                ch["quiz"] = quiz
            if ch.get("has_interaction"):
                interaction = await self._generate_interaction(
                    ch["title"], course_title, age, profile,
                )
                ch["interaction"] = interaction
            ch.pop("has_quiz", None)
            ch.pop("has_interaction", None)

        # Build YAML — persona adapts to age, classmates are fixed
        course_yaml = self._build_yaml(course_title, lang, age, profile, chapters)

        # Save — filename: {slug}_{age}.yaml
        course_name = self._slugify(course_title) + "_" + age.replace(" ", "")
        output_path = self._output_dir / f"{course_name}.yaml"
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(course_yaml, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        logger.info("✅ Course saved: %s", output_path)
        return {"course_name": course_name, "chapters": len(chapters), "path": str(output_path)}

    # -- Stage 1 --------------------------------------------------------------

    async def _generate_outline(
        self, topic: str, age: str, lang: str, chapter_count: int, profile: dict,
    ) -> dict:
        skeleton_points = profile["skeleton_points_per_chapter"]
        skeleton_hint = f"{skeleton_points * 10}-{skeleton_points * 20}字，口语化"
        prompt = STAGE1_TEMPLATE.format(
            topic=topic,
            age=age,
            age_label=profile["label"],
            cognitive=profile["cognitive"],
            strategy=profile["strategy"],
            age_extra=profile["stage1_extra"],
            lang=lang,
            chapter_count=chapter_count,
            skeleton_points=skeleton_points,
            skeleton_hint=skeleton_hint,
        )
        return await self._llm_json(prompt, max_tokens=4096)

    # -- Stage 2 --------------------------------------------------------------

    async def _generate_quiz(
        self, chapter_title: str, course_title: str, age: str, profile: dict,
    ) -> dict:
        prompt = STAGE2_QUIZ_TEMPLATE.format(
            chapter_title=chapter_title,
            course_title=course_title,
            age=age,
            age_label=profile["label"],
            cognitive=profile["cognitive"],
            quiz_extra=profile["quiz_extra"],
        )
        return await self._llm_json(prompt, max_tokens=512)

    async def _generate_interaction(
        self, chapter_title: str, course_title: str, age: str, profile: dict,
    ) -> dict:
        prompt = STAGE2_INTERACTION_TEMPLATE.format(
            chapter_title=chapter_title,
            course_title=course_title,
            age=age,
            age_label=profile["label"],
            strategy=profile["strategy"],
            interaction_extra=profile["interaction_extra"],
        )
        return await self._llm_json(prompt, max_tokens=256)

    async def _llm_json(self, prompt: str, max_tokens: int) -> dict:
        """One-shot LLM call with JSON mode + retry-on-truncation."""
        for attempt in range(3):
            tok = max_tokens * (2 ** attempt)  # 4096 → 8192 → 16384
            try:
                resp = await self._llm._client.chat.completions.create(
                    model=self._llm._model,
                    messages=[
                        {"role": "system", "content": "你是一个专业的儿童课程设计师。只输出JSON，不要任何解释。"},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=tok,
                    temperature=0.3,
                    response_format={"type": "json_object"},
                )
                raw = resp.choices[0].message.content or ""
                finish = resp.choices[0].finish_reason
                logger.info("LLM: %d chars, finish=%s, max_tokens=%d", len(raw), finish, tok)
            except Exception as api_err:
                logger.warning("LLM API error (attempt %d): %s", attempt + 1, api_err)
                if attempt < 2:
                    await asyncio.sleep(2)
                    continue
                raise RuntimeError(f"LLM API call failed: {api_err}") from api_err

            if not raw.strip():
                if attempt < 2:
                    logger.warning("Empty response, retrying with %d tokens...", tok * 2)
                    await asyncio.sleep(2)
                    continue
                raise RuntimeError("LLM returned empty response after 3 attempts.")

            try:
                return self._parse_json(raw)
            except Exception:
                if finish == "length" and attempt < 2:
                    logger.warning("JSON truncated (finish=length), retrying with %d tokens...",
                                   tok * 2)
                    await asyncio.sleep(2)
                    continue
                preview = raw[:300].replace("\n", "\\n")
                raise RuntimeError(f"LLM returned invalid JSON: {preview}")

    # -- YAML assembly --------------------------------------------------------

    @staticmethod
    def _build_yaml(
        title: str, lang: str, age: str, profile: dict, chapters: list,
    ) -> dict:
        """Assemble the course YAML with age-adaptive persona + fixed classmates."""
        return {
            "course": {
                "title": title,
                "lang": lang,
                "default_tts_speed": profile["tts_speed"],
                "persona": {
                    "name": "小思老师",
                    "style": profile["strategy"],
                    "voice": "温暖甜美女声，语速稍慢",
                    "tts_speed": profile["tts_speed"],
                    "guardrails": (
                        "- 只输出纯粹的口语文字，绝对不要加任何括号注释、舞台指导、动作描述或心理描写\n"
                        "- 绝对不要自己回答自己提出的问题，提问后要留白等待\n"
                        "- 不可以说「你错了」，要说「差一点点就对了」\n"
                        "- 不能用抽象概念，每个概念必须配一个小朋友生活中的例子"
                    ),
                    "personality": (
                        f"- 你是「小思老师」，一位面向{age}岁{profile['label']}小朋友的思维课老师\n"
                        f"- 教学策略：{profile['strategy']}\n"
                        "- 你总是先肯定再引导，让每个小朋友都觉得自己很棒"
                    ),
                    "environment": (
                        "- 你正在给小朋友上一堂在线思维课\n"
                        "- 教室里有你（老师）、小明/小红/小刚/小美（AI同学）和一个真实的小朋友（学生）\n"
                        "- 小朋友可以通过举手按钮和语音向你提问"
                    ),
                    "tone": profile["persona_tone"],
                    "goal": profile["persona_goal"],
                    "workflow": profile["persona_workflow"],
                    "scope": profile["persona_scope"],
                },
                "classmates": [
                    {
                        "name": "小明",
                        "style": "好奇心强、偶尔问天真问题、有时回答错误",
                        "voice": "活泼小男孩声音",
                    },
                    {
                        "name": "小红",
                        "style": "乖巧懂事、喜欢帮助别人、回答通常正确、有时会小声提醒小明",
                        "voice": "可爱小女孩声音",
                    },
                    {
                        "name": "小刚",
                        "style": "活泼好动、有时候走神、喜欢抢答但经常答错",
                        "voice": "调皮小男孩声音",
                    },
                    {
                        "name": "小美",
                        "style": "安静内向、说话声音小、需要鼓励才敢发言、但观察很仔细",
                        "voice": "温柔小女孩声音",
                    },
                ],
                "assets": {"cards": [], "mindmaps": {}},
            },
            "chapters": chapters,
        }

    # -- Helpers --------------------------------------------------------------

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert course title to a filesystem-safe slug."""
        slug = text.strip()
        slug = re.sub(r'[^\w\s一-鿿]', '', slug)
        slug = re.sub(r'\s+', '_', slug).strip('_')
        return slug[:40] if slug else "course"

    @staticmethod
    def _parse_json(raw: str) -> dict:
        """Extract JSON from LLM output (handles markdown fences only)."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:]) if len(lines) > 1 else text
        if text.endswith("```"):
            text = text[:-3].strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            text = text[start:end]
        return _json.loads(text)  # let it fail on truncation → triggers retry
