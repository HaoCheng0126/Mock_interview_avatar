# Teaching Digital Human — Design Spec

**Date:** 2026-06-14
**Status:** Draft
**Scope:** 教学数字人（Teaching Digital Human），首个场景为思维课
**Target Audience:** 低年龄段儿童（4-10 岁）

---

## 1. Overview

在现有聊天数字人 (`agent.py`) 和口播数字人 (`broadcast_agent.py`) 基础上，实现第三种数字人形态：教学数字人。

**目标用户**为 4-10 岁儿童。所有设计选择——语言风格、交互节奏、容错策略、视觉风格——均以儿童认知水平和注意力特点为基准。

核心交互模式：
- 数字人以亲切童趣的语气逐章节讲课
- 用户随时可以通过「举手按钮 + 语音」打断提问
- 回答完问题后 LLM 生成自然过渡语，从断点继续讲课
- 章节间穿插测验、主动提问等教学交互组件

## 2. Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| 内容来源 | 混合模式：骨架脚本 + LLM 润色 | 课程结构可控，表达自然流畅 |
| 打断恢复 | 自然衔接：LLM 生成过渡语 + 断点续讲 | 体验最流畅，不僵硬 |
| 课程粒度 | 章节级 (5-8 章/课) | 适合教学场景，断点管理清晰 |
| 交互方式 | 语音 + 举手按钮 + 前端交互组件 | 防误打断 + 支持测验/可视化 |
| 交互组件 | 测验 + 视觉辅助 + 双向互动 | 完整教学体验 |
| 架构策略 | 新建 TeachingController + CourseManager | 边界清晰，零耦合现有 agent |
| 目标受众 | 4-10 岁儿童 | 语言、节奏、容错、视觉全部围绕儿童设计 |

### Child-Friendly Design Principles

面向 4-10 岁儿童，以下原则贯穿所有设计层面：

**语言**
- 用词简单：避免成语、抽象概念；用具体、可感知的词汇
- 句子短：每句 15 字以内，避免复合句
- 比喻贴近生活：用玩具、动物、食物等儿童熟悉的事物做类比
- 人称：用"你"、"我们"，营造陪伴感；自称"老师姐姐/哥哥"

**节奏**
- 讲课速度：TTS speed 默认 0.9（比成人慢 10%）
- 段落间停顿：500ms（比成人的 300ms 更长）
- 每章 2-4 分钟，避免注意力疲劳
- 章节间有明确的"休息提示"（"听明白了吗？你可以举个手告诉老师哦"）

**鼓励机制**
- 正面强化优先：任何回应都先给予肯定
- 测验答错：不说"错了"，说"差一点点就对了！正确答案是..."
- 测验答对：热情庆祝（"太棒了！你真是逻辑小达人！"）
- 举手提问：先感谢（"谢谢你的提问，这个问题问得真好！"）

**容错**
- ASR：儿童发音不标准、语速慢、停顿多 → silence_duration 放宽到 600ms
- 等待超时：对儿童更宽容（举手后 15s 无声、测验 90s）
- 听不懂时的处理：不说"我没听懂"，说"老师没听清楚，你能再说一遍吗？"

**视觉**
- 前端组件用明亮色彩、大字体、圆角卡片
- 测验选项配 emoji/插图辅助理解
- 举手按钮醒目、大尺寸、带动画效果

## 3. File Structure

```
python-agent/
├── teaching_agent.py           # 入口：HTTP 路由 + 全局状态管理 (port 8082)
├── teaching_controller.py      # 教学状态机：讲课 ↔ 问答 ↔ 测验
├── course_manager.py           # 课程 YAML 加载 + 章节/测验/交互管理
├── course_component.py         # 组件协议：前端组件消息的数据结构
├── config/
│   └── courses/
│       └── thinking.yaml       # 思维课第一版课程内容
├── tests/
│   ├── test_teaching_controller.py
│   ├── test_course_manager.py
│   └── test_course_component.py
├── llm_client.py               # [复用] 共享 LLM 客户端
├── agent.py                    # [不变] 聊天数字人
├── broadcast_agent.py          # [不变] 口播数字人
└── ...
```

新增 4 个文件，零改动现有代码。

## 4. Course Content Format

YAML 格式，以思维课为例：

```yaml
course:
  title: "思维小达人 — 第一课：火眼金睛辨对错"
  lang: zh
  default_tts_speed: 0.9              # 儿童语速稍慢

  assets:
    cards:
      - id: "what_is_logic"
        title: "什么是火眼金睛？🧐"
        content: "就是学会判断别人说的话对不对！"
        image: "/assets/card-logic-kids.png"

chapters:
  - id: "intro"
    title: "小故事：大家都说对，就是对吗？"
    skeleton:
      - "讲一个小故事：森林里的小动物们都往东边跑，小兔子问为什么，大家说「因为大家都在跑呀！」"
      - "问小朋友：你觉得这些小动物做得对吗？如果是你，你会跟着跑吗？"
      - "引出今天的主题：不能因为做的人多，就觉得一件事情是对的哦！"
    interaction:
      prompt: "如果是你，你会跟着大家一起跑吗？为什么呀？"
      expect_keywords: ["会", "不会", "跑", "不跑"]
    visual:
      type: card
      ref: "what_is_logic"

  - id: "fallacy_types"
    title: "三种常见的「小陷阱」"
    skeleton:
      - "第一个小陷阱叫「大家都这样」：别的小朋友都在吃糖，但这不代表你也一定要吃糖哦"
      - "第二个小陷阱叫「大人说的都对」：爸爸妈妈和老师很厉害，但他们有时候也会弄错呢"
      - "第三个小陷阱叫「只能选一个」：就像不是只有红色和蓝色，世界上还有很多好看的颜色！"
    quiz:
      question: "小明说：'我们班同学都有这个玩具，所以我也一定要买！' 小明掉进了哪个小陷阱呀？"
      options:
        - { key: "A", text: "「大家都这样」陷阱 🐑", correct: true }
        - { key: "B", text: "「大人说的都对」陷阱 👨‍🏫", correct: false }
        - { key: "C", text: "「只能选一个」陷阱 🎨", correct: false }
      explanation_correct: "太棒了！🌟 小明因为'别人都有'就想要，这就是「大家都这样」小陷阱！"
      explanation_wrong: "差一点点就对了！💪 再想想，小明是因为'班里的同学都有'才想要的，跟大人没关系哦～"
    visual:
      type: card
      ref: "fallacy_traps"
```

### Field Semantics

| Field | Required | Description |
|-------|----------|-------------|
| `course.title` | Yes | 课程标题 |
| `course.lang` | Yes | 语言 (`zh` / `en`) |
| `course.assets` | No | 全局素材（思维导图、知识点卡片） |
| `chapters[].id` | Yes | 章节唯一标识 |
| `chapters[].title` | Yes | 章节标题 |
| `chapters[].skeleton` | Yes | 骨架讲稿要点列表，每项一句话 |
| `chapters[].interaction` | No | 主动提问配置 |
| `chapters[].quiz` | No | 测验配置（选项必须恰好一个 correct: true） |
| `chapters[].visual` | No | 可视化素材引用 |

## 5. Teaching State Machine

```
                    ┌─────────────┐
                    │    IDLE     │
                    └──────┬──────┘
                           │ start()
                    ┌──────▼──────┐
           ┌───────│ LECTURING   │◄──────────────────┐
           │       └──┬───┬───┬──┘                   │
           │          │   │   │                      │
           │  user    │   │   │ chapter_end          │
           │  raises  │   │   │ (有interaction)       │
           │  hand    │   │   │                      │
           │          │   │   ▼                      │
           │          │   │ ┌──────────────────┐     │
           │          │   │ │ WAITING_INTERACT │     │
           │          │   │ └────────┬─────────┘     │
           │          │   │          │ user responds  │
           │          │   │          ▼                │
           │          │   │ ┌──────────────────┐     │
           │          │   │ │ PROCESSING_INTER │     │
           │          │   │ └────────┬─────────┘     │
           │          │   │          │ LLM feedback   │
           │          │   │          ▼                │
           │          │   │         (继续 LECTURING)──┘
           │          │   │
           │          │   │ chapter_end
           │          │   │ (有quiz)
           │      ┌───▼───▼──────┐
           │      │   QUIZZING   │
           │      └──────┬───────┘
           │             │ user answers
           │             ▼
           │      ┌──────────────┐
           │      │ QUIZ_RESULT  │──► (下一章 LECTURING)
           │      └──────────────┘
           │
           ▼
    ┌──────────────┐
    │  ANSWERING   │
    └──────┬───────┘
           │ LLM reply done
           ▼
    ┌──────────────┐
    │ TRANSITIONING│──► (生成过渡语，回到 LECTURING)
    └──────────────┘
```

### State Details

| State | Trigger | Behavior |
|-------|---------|----------|
| `IDLE` | 初始 / stop() | 等待 start |
| `LECTURING` | start() / 恢复后 | 逐章节：skeleton → LLM 润色 → send_prompt → await TTS idle |
| `WAITING_INTERACT` | 章节讲完 + 有 interaction | 数字人主动提问，等待语音回应 |
| `PROCESSING_INTER` | 用户回应 interaction | LLM 评估回应，生成反馈 |
| `ANSWERING` | 用户举手 + 提问 | 中断讲课，记录断点，LLM 生成回答 |
| `TRANSITIONING` | 回答完成 | LLM 生成过渡语（"好，我们继续..."），衔接回断点 |
| `QUIZZING` | 章节讲完 + 有 quiz | 推送题目 + 禁用举手，等待作答 |
| `QUIZ_RESULT` | 用户作答 / 超时 | 推送正误 + 解释，3s 后自动下一章 |

### Interrupt-Resume Flow (Core)

```
LECTURING → (举手 + 语音) → ANSWERING → (回答完成) → TRANSITIONING → (过渡语完成) → LECTURING (从断点)
```

断点数据结构：`{ chapter_id, skeleton_index }`

### Session Lifecycle

- **Auto-start**: 同 broadcast_agent，监听 `scene.ready` 事件后自动调用 `TeachingController.start()`
- **Course end**: 末章结束后停止（不 loop）。可通过 HTTP API 重新 start 回到首章
- **ASR**: 始终启用（同 agent.py），但仅在 `ANSWERING` 和 `WAITING_INTERACT` 状态下将识别结果作为有效输入

### Teaching Persona & LLM Context

教学 agent 维护**双上下文**的 LLM 策略：

| Context | Purpose | Behavior |
|---------|---------|----------|
| **Lecture context** | 润色 skeleton 点、生成过渡语 | 短期上下文，每次调用独立；system prompt 定义讲师人设 |
| **Q&A context** | 回答用户提问 | 带课程上下文（当前章节、已讲内容摘要），保留对话历史 |

```python
LECTURE_SYSTEM_PROMPT = """
你是一位面向 4-10 岁小朋友的思维课老师，名字叫"小思老师"。
说话要像幼儿园/小学老师一样亲切可爱，用小朋友能听懂的语言。

规则：
- 每句话不超过 15 个字，用短句
- 用小朋友熟悉的事物打比方（玩具、小动物、吃东西、玩游戏）
- 称呼学生为"小朋友"或"你"
- 每次讲解完一个要点，加一句鼓励的话
- 语气温暖、活泼，像在讲故事
- 避免抽象概念，每个概念都要配一个具体的例子
- 每次根据给定的要点，扩展成 3-5 句自然的讲课语言
"""

QA_SYSTEM_PROMPT = """
你是一位面向 4-10 岁小朋友的思维课老师"小思老师"。
你正在讲解{chapter_title}，一个小朋友举手向你提问。

规则：
- 先感谢小朋友的提问，肯定他/她（"这个问题问得真好！"）
- 用小朋友能听懂的方式回答，控制在 100 字以内
- 句子要短，用生活化的例子
- 回答完后，自然地带小朋友回到课程（"好啦，我们继续来学习..."）
- 如果小朋友的提问不清楚，温柔地请他/她再说一遍
"""
```

Lecture context 每次调用 `reset_context()` 后独立执行；Q&A context 保留整个 session 的对话历史（同 agent.py 的上下文管理）。

### ASR Configuration (Child-Optimized)

相比成人 agent.py，儿童场景的 ASR 参数调整：

```python
# agent.py (成人)
turn_detection_silence_duration_ms=400

# teaching_agent.py (儿童) — 更宽容的停顿容忍
turn_detection_silence_duration_ms=600  # 小朋友说话慢、停顿多
```

### ASR Gating Strategy

```
状态              未举手 VAD → 行为
LECTURING         忽略（echo cooldown 期内也忽略）
WAITING_INTERACT  接受（免举手）
ANSWERING         接受（正在回答中，新一轮识别取消当前回答）
QUIZZING          忽略（前端按钮作答，不用语音）
TRANSITIONING     忽略
```

## 6. Component Protocol

Agent 通过 `send_custom_event` 推送组件消息给前端。格式定义在 `course_component.py`：

```python
@dataclass
class ComponentMessage:
    type: str       # 组件类型
    action: str     # "show" | "hide" | "update"
    data: dict      # 组件数据
    timestamp: int  # ms
```

### Component Types

| type | Purpose | data Content |
|------|---------|-------------|
| `visual_card` | 知识点卡片 | `{id, title, content, image?}` |
| `mindmap` | 思维导图 | `{id, title, image_url}` |
| `quiz` | 选择题 | `{question, options: [{key, text}], chapter_id}` |
| `quiz_result` | 作答结果 | `{correct, explanation, correct_answer?}` |
| `raise_hand` | 举手按钮状态 | `{enabled: bool}` |
| `interaction_prompt` | 数字人提问 | `{text, chapter_id}` |
| `chapter_indicator` | 章节进度 | `{current, total, title}` |
| `lecture_progress` | 段落进度 | `{segment_current, segment_total}` |
| `encouragement` | 鼓励动画 | `{text, style: "star"|"clap"|"heart"}` |

注：`encouragement` 用于测验答对时触发前端庆祝动画（撒星星、鼓掌等），增强儿童的成就感和参与度。

### User → Agent Feedback

| Endpoint | Trigger |
|----------|---------|
| `POST /api/teaching/raise-hand` | 用户点击举手 |
| `POST /api/teaching/cancel-hand` | 用户取消举手 |
| `POST /api/teaching/quiz-answer` | 提交测验答案 `{chapter_id, answer}` |

## 7. HTTP API

`teaching_agent.py` port **8082**.

### Agent Control

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/start-session` | 启动教学 session + 加载课程 |
| `POST` | `/api/stop-session` | 结束教学 |
| `POST` | `/api/teaching/start` | 开始讲课 |
| `POST` | `/api/teaching/pause` | 暂停讲课 |
| `POST` | `/api/teaching/resume` | 恢复讲课 |

### Interaction (Frontend → Agent)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/teaching/raise-hand` | 用户举手 → ANSWERING |
| `POST` | `/api/teaching/cancel-hand` | 取消举手 → 回 LECTURING |
| `POST` | `/api/teaching/quiz-answer` | 提交测验答案 |
| `GET`  | `/api/teaching/status` | 当前状态 + 进度 + 组件队列 |

### Frontend Serving

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/` | 教学前端页面 (`frontend/teaching.html`) |
| `GET`  | `/sdk.js` | Live Avatar JS SDK |

## 8. Core Data Flows

### Flow 1: Normal Lecture

```
CourseManager.load("thinking")
  → TeachingController.start()
  → _run_lecture_loop()
  → for each chapter:
      ├─ send component "chapter_indicator"       // show chapter title
      ├─ send component "visual_card"/"mindmap"   // show visual aid
      ├─ for each skeleton point:
      │    ├─ LlmClient.generate(skeleton_point)   // LLM polishes
      │    ├─ agent.send_prompt(polished_text)     // TTS playback
      │    └─ await tts_idle                       // wait for completion
      ├─ [has interaction] → WAITING_INTERACT
      ├─ [has quiz] → QUIZZING
      └─ else → next chapter
```

### Flow 2: User Interrupt & Question

```
During LECTURING, user clicks raise-hand
  → raise_hand() → record breakpoint {chapter_id, skeleton_index}
  → state → ANSWERING
  → VAD detects speech → ASR → text
  → LlmClient.generate(user_question, system_prompt=teacher_persona)
  → agent.send_prompt(answer)
  → await tts_idle
  → state → TRANSITIONING
  → LlmClient.generate("generate transition back to breakpoint context")
  → agent.send_prompt(transition)
  → await tts_idle
  → state → LECTURING (resume from breakpoint)
```

### Flow 3: Chapter Quiz

```
Chapter skeleton complete + quiz configured
  → state → QUIZZING
  → send component "quiz" {question, options}
  → send component "raise_hand" {enabled: false}   // disable during quiz
  → await user_response (timeout: 90s, for children)
  → state → QUIZ_RESULT
  → if correct:
      send component "encouragement" {text: "太棒了！🌟", style: "star"}
    else:
      (encouragement still sent with milder style)
  → send component "quiz_result" {correct, explanation}
  → await asyncio.sleep(4)                          // let child read slowly
  → send component "raise_hand" {enabled: true}
  → next chapter
```

## 9. Error Handling

### ASR / VAD False Triggers
- TTS 播完后 1.5s echo cooldown（比成人 agent 的 1s 更保守）
- 举手按钮作为门控：未举手时 VAD 触发不进入 ANSWERING
- `WAITING_INTERACT` 状态例外：允许免举手语音

### User Speaks Nothing After Raise-Hand
- 举手后 **15s** 无声（儿童比成人多 5s）→ 取消举手，回 LECTURING
- TTS 播报提示："老师没听到你的声音哦，你想好了再举手告诉老师吧！"

### LLM Failures (Degrade Gracefully)
- 润色 skeleton 失败 → 直接用原始骨架文本播讲
- 回答提问失败 → 固定语："哎呀，老师需要想一想这个问题。我们先把刚才的内容学完，好不好？"
- 过渡语生成失败 → 固定模板："好啦，我们继续看下一个有趣的知识吧！"

### Quiz Timeout
- **90s** 未作答（儿童比成人多 30s）→ 显示正确答案 + 鼓励语 → 3s 后下一章
- 鼓励语："没关系，老师告诉你答案哦～"

### Rapid Consecutive Interrupts
- TRANSITIONING/ANSWERING 中再次举手 → cancel 当前生成任务
- 以最新提问为准，重置 ANSWERING

### Course Config Validation
- 文件不存在 → CourseLoadError
- skeleton 为空 → ValidationError
- quiz options 无 correct: true → ValidationError

## 10. Testing Strategy

### Unit Tests (no platform connection needed)

**test_course_manager.py**
- Load valid course YAML → correct chapters/assets/quiz/interaction parse
- Load non-existent file → CourseLoadError
- Empty skeleton chapter → ValidationError
- Chapter navigation: first, last, boundary
- Quiz options: exactly one correct

**test_teaching_controller.py**
- State transition legality:
  - IDLE → (start) → LECTURING ✓
  - IDLE → (pause) → stays IDLE (no-op) ✓
  - LECTURING → (raise_hand) → ANSWERING ✓
  - ANSWERING → (reply_done) → TRANSITIONING ✓
  - TRANSITIONING → (transition_done) → LECTURING ✓
  - QUIZZING + raise_hand → no effect ✓
- Breakpoint record/restore: chapter_id + skeleton_index correct after interrupt
- Rapid double-interrupt: second cancels first TRANSITIONING
- Chapter progression: last chapter → loop to first / end

**test_course_component.py**
- ComponentMessage serialize/deserialize
- Data schema validation per component type
- raise_hand enabled/disabled state flow

### Integration Tests (mock LLM + mock Agent)
- Full lecture flow: load course → per-chapter TTS idle simulation → verify chapter order
- Interrupt-resume flow: LECTURING → raise-hand → answer → transition → back to breakpoint
- Quiz flow: chapter end → push question → simulate answer → verify result push

### Manual Verification Checklist
- [ ] Start teaching_agent.py, confirm console logs
- [ ] Open browser, verify raise-hand button enabled/disabled states
- [ ] Normal lecture: visual cards switch per chapter
- [ ] Raise-hand + speak → avatar stops + answers
- [ ] After answer, natural transition back to course
- [ ] Chapter-end quiz: options → select → result → auto-continue
- [ ] Double rapid raise-hand: only latest question processed

## 11. Non-Goals (Explicitly Out of Scope)

- Multi-course management / course switching UI
- User progress persistence (across sessions)
- Real-time student performance analytics
- Voice tone/emotion adaptation
- Course authoring UI (YAML editing is sufficient for v1)
- Support for non-Chinese courses in v1

## 12. Dependencies

- Live Avatar Platform SDK (unchanged)
- DashScope Qwen ASR (same as agent.py)
- DeepSeek LLM (same as both agents)
- aiohttp HTTP server (same as both agents)
- Frontend: teaching.html + Live Avatar JS SDK
