# Teaching Digital Human v2 — Design Spec

**Date:** 2026-06-14
**Status:** Draft
**Scope:** 教学数字人 v2 升级 — 人设配置、AI 同学、自适应调度、课程生成、白板可视化

---

## 1. Overview

在 v1 教学数字人基础上，参考 OpenMAIC 的教学模式进行五个维度的升级。

| 维度 | v1 | v2 |
|------|:--:|:--:|
| 角色 | 单人老师 | 老师 + AI 同学（可配置 persona） |
| 调度 | 固定章节顺序 | Manager Agent 自适应决策 |
| 内容 | 手写 YAML | 手写 YAML + 一键生成 |
| 视觉 | 静态卡片 | 白板式逐步动画 |
| 人设 | 硬编码 | YAML 可配置 |

## 2. Teacher & Classmate Persona（P0）

### 2.1 Persona Config Schema

```yaml
# config/courses/thinking.yaml
course:
  title: "思维小达人 — 第一课：火眼金睛辨对错"
  lang: zh

  persona:                          # 🆕 老师人设
    name: "小思老师"
    style: "亲和活泼"               # 亲和活泼 | 严谨启发 | 幽默风趣
    voice: "温暖甜美女声，语速稍慢"
    tts_speed: 0.9
    rules:
      - "每句话不超过15个字"
      - "用小朋友熟悉的事物打比方"
      - "先肯定再引导"
      - "称呼学生为'小朋友'"

  classmates:                       # 🆕 AI 同学
    - name: "小明"
      style: "好奇心强、偶尔问天真问题、有时回答错误"
      voice: "活泼小男孩声音"
      rules:
        - "在老师讲完一个知识点后，偶尔举手提问"
        - "老师提问时如果学生没反应，你先尝试回答做示范"
        - "你有时会答错，展示真实的思考过程"

  assets: ...
  chapters: ...
```

### 2.2 PersonaManager

新增 `persona_manager.py`，职责：
- 解析 `persona` 和 `classmates` 配置
- 为不同角色生成对应的 system prompt
- `get_teacher_prompt()` → 老师的 system prompt
- `get_classmate_prompt(name)` → AI 同学的 system prompt
- `get_classmate_behavior(name, context)` → AI 同学的行为决策 prompt

### 2.3 Prompt 生成逻辑

不再硬编码 `LECTURE_SYSTEM_PROMPT`，改为从 persona config 动态构建：

```python
def build_teacher_prompt(persona: dict) -> str:
    rules = "\n".join(f"- {r}" for r in persona.get("rules", []))
    return f"""你叫{persona['name']}，是一位面向4-10岁小朋友的思维课老师。
你的风格是{persona['style']}。
说话声音：{persona['voice']}。

规则：
{rules}
"""
```

## 3. AI Classmate（P1）

### 3.1 架构

```
teaching_agent.py
  └── TeachingListener
        └── ClassmateEngine (新增)
              ├── 为每个 AI 同学维护独立的 LlmClient
              ├── 行为决策：何时发言
              └── 发言生成：说什么
```

### 3.2 ClassmateEngine

```python
class ClassmateEngine:
    """Manages AI classmates — decides when they speak and what they say."""

    def __init__(self, classmates: list[dict], llm_factory):
        self._classmates = classmates
        self._llm_clients = {
            c["name"]: llm_factory(c["name"]) for c in classmates
        }
        self._speak_cooldown: dict[str, float] = {}  # per-classmate cooldown
        self._enabled = True

    async def maybe_interject(self, context: ClassContext) -> str | None:
        """Decide whether a classmate should speak now.
        Returns the classmate's speech text, or None if nobody speaks.
        """
        # 20% base probability, adjusted by cooldown
        ...

    async def answer_interaction(self, question: str) -> str | None:
        """Have a classmate answer the teacher's interaction question.
        Used when the real student doesn't respond within 8 seconds.
        """
        ...

    async def answer_quiz(self, quiz: dict) -> dict | None:
        """Have a classmate answer a quiz question (sometimes wrong)."""
        ...
```

### 3.3 AI 同学触发时机

| 时机 | 触发条件 | 行为 |
|------|---------|------|
| `ON_KNOWLEDGE_DONE` | 老师讲完一个知识点，20% 概率 | AI 同学插话提问或发表感想 |
| `ON_INTERACTION_TIMEOUT` | 老师提问后 8s 学生未回应 | AI 同学尝试回答（做示范） |
| `ON_QUIZ` | 测验环节 | AI 同学也作答（70%对 / 30%错） |
| `ON_STUDENT_QA_DONE` | 学生提问被回答后 | AI 同学表示赞同或追问 |

### 3.4 TTS 切换

AI 同学发言时，先通过 `send_custom_event` 切换 voice，然后 `send_prompt`。发言后切回老师 voice。

```python
# 发送 AI 同学语音前切换 voice
await agent.send_custom_event(None, "voice.switch", {"voice": classmate_voice})
await agent.send_prompt(classmate_text)
# 说完后切回老师 voice
await agent.send_custom_event(None, "voice.switch", {"voice": teacher_voice})
```

## 4. Manager Agent — Adaptive Scheduling（P2）

### 4.1 架构

```
_run_lecture_loop()
  → _broadcast_chapter()
    → _broadcast_knowledge_point()
      → _manager_decide()   ← 🆕 每个知识点讲完后调用
        → 决策: CONTINUE | ASK_QUESTION | CLASSMATE_SPEAK | RE_EXPLAIN | SKIP
```

### 4.2 ManagerAgent

```python
class ManagerAgent:
    """Lightweight LLM-based decision maker for classroom pacing."""

    async def decide(self, state: ManagerState) -> ManagerAction:
        """Given the current classroom state, decide what to do next.
        
        Uses a small LLM call (~200 token prompt) to choose among:
        - CONTINUE: proceed to next knowledge point
        - ASK_QUESTION: teacher asks a targeted question
        - CLASSMATE_SPEAK: let AI classmate speak
        - RE_EXPLAIN: re-explain the current point differently
        - SKIP: skip remaining points in this chapter
        """
```

### 4.3 ManagerState

```python
@dataclass
class ManagerState:
    chapter_id: str
    knowledge_index: int
    knowledge_total: int
    student_questions_in_chapter: int       # 学生在本章提问次数
    student_quiz_correct: bool | None       # 上次测验结果
    student_last_response_time: float | None # 学生上次回应耗时
    elapsed_seconds: float                   # 本章已用时间
    classmate_recently_spoke: bool           # AI 同学最近是否发过言
```

### 4.4 触发频率

Manager Agent 在每个知识点讲完后触发一次（每 30-60 秒一次决策）。prompt 短（~200 tokens），用 flash 模型，延迟 < 1s。

## 5. Course Generator（P3）

### 5.1 API

```
POST /api/teaching/generate
Body: {
  "topic": "逻辑谬误——诉诸大众与虚假二分",
  "age": "4-10",            // 目标年龄
  "lang": "zh",
  "chapter_count": 3,       // 期望章节数（可选，默认3-5）
  "materials": ""           // 可选：上传的参考材料文本
}
→ 200: { "success": true, "course_name": "thinking_20260614", "chapters": 3 }
```

### 5.2 Two-Stage Generation

**Stage 1 — 大纲生成：**
```
LLM prompt:
  你是儿童课程设计师。为"{topic}"设计一堂{age}岁儿童的课程。
  生成{chapter_count}个章节，每章包含：
  - id, title, 3-5个skeleton要点, 是否需要quiz, 是否需要interaction
  输出JSON格式。
```

**Stage 2 — 内容展开：**
```
逐章调用LLM:
  为章节"{chapter_title}"展开内容：
  - skeleton: 3-5个口语化要点（每个20-40字）
  - quiz: 选择题 + 4个选项 + 正误解释（如需要）
  - interaction: 一个开放式提问（如需要）
```

### 5.3 输出

生成的 YAML 写入 `config/courses/{course_name}.yaml`，自动设置 `TEACHING_COURSE` 环境变量或通过 API 热加载。

## 6. Whiteboard Visualization（P4）

### 6.1 新组件类型

在 `course_component.py` 的 `COMPONENT_TYPES` 中新增：

```python
"whiteboard_step",      # 逐步展示推理步骤
"whiteboard_highlight", # 高亮关键概念
"whiteboard_compare",   # 左右对比
```

### 6.2 组件 data schema

```python
# whiteboard_step
{
  "steps": [
    {"text": "大家都说对 → 就是对的吗？", "highlight": False},
    {"text": "不一定！因为很多人相信 ≠ 事实", "highlight": True},
  ],
  "current_step": 1,
  "total_steps": 2,
}

# whiteboard_compare
{
  "left": {"title": "正确推理 ✅", "text": "自己想一想再决定"},
  "right": {"title": "谬误 ❌", "text": "别人都这样所以我也这样"},
}
```

### 6.3 前端渲染

白板组件渲染在 avatar 视频的下方或右侧覆盖层，CSS 动画逐步展示：
- `whiteboard_step`: 逐条 fade-in，高亮条用黄色背景 + 放大
- `whiteboard_compare`: 左右卡片同时出现，正确侧绿色边框，错误侧红色边框

### 6.4 触发方式

在 course YAML 的 skeleton 中标注白板步骤：

```yaml
skeleton:
  - point: "第一种小陷阱叫「大家都这样」"
    whiteboard:
      type: whiteboard_step
      steps:
        - "大家都说对 → 就是对的吗？"
        - "不一定！很多人相信 ≠ 事实"
```

## 7. File Structure（v2 新增/修改）

```
python-agent/
├── persona_manager.py           # 🆕 人设解析 + prompt 生成
├── classmate_engine.py          # 🆕 AI 同学行为决策 + 发言生成
├── manager_agent.py             # 🆕 自适应调度决策
├── course_generator.py          # 🆕 两阶段课程生成
├── teaching_controller.py       # 🔧 集成 Manager + Classmate
├── teaching_agent.py            # 🔧 新增 API + ClassmateEngine
├── course_component.py          # 🔧 新增 whiteboard 组件
├── course_manager.py            # 🔧 新增 persona/classmates 解析
├── config/courses/thinking.yaml # 🔧 新增 persona + classmates 配置
├── frontend/teaching.html       # 🔧 白板组件渲染
└── tests/                       # 🔧 新增对应测试文件
```

## 8. Implementation Order

| Step | Module | Dependencies | Est. Effort |
|:----:|--------|:----------:|:----------:|
| 1 | `persona_manager.py` + tests | None | 小 |
| 2 | YAML schema 升级（persona + classmates） | Step 1 | 小 |
| 3 | `course_manager.py` 集成 persona 解析 | Step 1 | 小 |
| 4 | `teaching_controller.py` 改用 persona prompt | Step 3 | 小 |
| 5 | `classmate_engine.py` + tests | Step 3 | 中 |
| 6 | `teaching_agent.py` 集成 ClassmateEngine | Step 5 | 中 |
| 7 | `manager_agent.py` + tests | Step 6 | 中 |
| 8 | `teaching_controller.py` 集成 ManagerAgent | Step 7 | 中 |
| 9 | `course_generator.py` + tests + API | Step 1 | 中 |
| 10 | Whiteboard 组件 + 前端 | Step 1 | 小 |
| 11 | 全量测试 + 端到端验证 | All | 小 |

## 9. Non-Goals（v2 不做）

- 多个渲染实例的数字人（AI同学共享老师同一数字人渲染，仅切换 voice）
- 学生长期学习档案
- 课程市场/分享
- 实时多人课堂
