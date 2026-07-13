# Quickstart — Teaching Digital Human (teaching/agent.py)

Step-by-step guide to get the multi-agent interactive classroom running.

## Prerequisites

| Software | Version | Check |
|----------|---------|-------|
| Python | ≥ 3.11 | `python3 --version` |
| Node.js | ≥ 18 | `node --version` |

## Step 1: Get the code

```bash
cd liveavatar-ws-integration-demo
```

## Step 2: Install frontend dependencies

```bash
cd frontend
npm install
cd ..
```

## Step 3: Set up Python environment

```bash
cd python-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Step 4: Set environment variables

```bash
# Required
export LIVEAVATAR_API_KEY="lk_live_xxx"      # Live Avatar platform key
export LIVEAVATAR_AVATAR_ID="avatar_xxx"     # Avatar ID
export DEEPSEEK_API_KEY="sk-xxx"             # DeepSeek LLM key
export DASHSCOPE_API_KEY="sk-xxx"            # DashScope (ASR + classmate TTS)

# Optional
export TEACHING_HTTP_PORT="8082"             # Port (default 8082)
export TEACHING_COURSE="thinking"            # Course base name (default "thinking")
# Auto-loads thinking_4-10.yaml (4-10 is the default age range)
# Explicit age: TEACHING_COURSE="thinking_7-10" loads thinking_7-10.yaml
export DEEPSEEK_MODEL="deepseek-v4-flash"    # LLM model
```

> **DashScope** provides real-time speech recognition (Qwen ASR) and will power AI classmate TTS (CosyVoice). Get a key at https://bailian.console.aliyun.com

## Step 5: Run

```bash
python teaching/agent.py
```

You should see:
```
📚 Starting Teaching Agent on port 8082
📚 Loaded course: 思维小达人 — 火眼金睛辨对错 (3 chapters)
👩‍🏫 Teacher: 小思老师 (动作和游戏主导)
👦 Classmates: 小明, 小红, 小刚, 小美
🎤 Qwen ASR connected (child mode, silence=800ms)
```

## Step 6: Open in browser

Go to **http://localhost:8082**

1. Click **🔗 连接老师** — the avatar appears, greets you, and starts teaching
2. Click **🙋 举手提问** — the mic opens, teacher pauses, ask your question
3. Click **✋ 放下手** — the mic closes, teacher transitions back to the lesson

---

## Course Configuration

Courses live in `config/courses/`. Files follow `{name}_{age-range}.yaml` naming.
The default is `thinking_4-10.yaml`.

### Minimal Course

```yaml
course:
  title: "我的第一课"
  lang: zh

chapters:
  - id: "intro"
    title: "引入"
    skeleton:
      - "第一个知识点"
      - "第二个知识点"
```

### Full Course with Persona & Classmates

```yaml
course:
  title: "思维小达人 — 火眼金睛辨对错"
  lang: zh
  default_tts_speed: 0.9

  # Teacher persona — 7 dimensions control LLM behavior
  persona:
    name: "小思老师"
    style: "亲和活泼"
    voice: "温暖甜美女声，语速稍慢"
    tts_speed: 0.9
    guardrails: |           # 安全护栏 — 绝对不能做的事
      - 只输出纯粹的口语文字，绝对不要加任何括号注释、舞台指导、动作描述或心理描写
      - 绝对不要自己回答自己提出的问题，提问后要留白等待
    personality: |          # 角色定义
      - 你是「小思老师」，一位面向4-10岁小朋友的思维课老师
      - 你总是先肯定再引导，让每个小朋友都觉得自己很棒
    environment: |          # 教室环境
      - 教室里有你（老师）、小明/小红/小刚/小美（AI同学）和一个真实的小朋友（学生）
    tone: |                 # 语气风格
      - 声音温暖甜美，语速稍慢
      - 每句话不超过15个字，用短句
      - 用小朋友熟悉的事物打比方：玩具、小动物、吃东西、玩游戏
    goal: |                 # 教学目标
      - 把给定的讲课要点扩展成3-5句生动有趣的口语讲解
    workflow: |             # 工作流
      - 1. 用生活化的例子引入要点
      - 2. 自然地讲解核心内容
      - 3. 如果要点是提问性的，只提问不回答
      - 4. 用一句鼓励的话收尾
    scope: |                # 输出边界
      - 只扩展当前这一个要点，不要讲到后面的内容
      - 输出长度：3-5句，总共不超过100字

  # 4 AI classmates — each with distinct personality and voice
  classmates:
    - name: "小明"
      style: "好奇心强、偶尔问天真问题、有时回答错误"
      voice: "活泼小男孩声音"
    - name: "小红"
      style: "乖巧懂事、喜欢帮助别人、回答通常正确、有时会小声提醒小明"
      voice: "可爱小女孩声音"
    - name: "小刚"
      style: "活泼好动、有时候走神、喜欢抢答但经常答错"
      voice: "调皮小男孩声音"
    - name: "小美"
      style: "安静内向、说话声音小、需要鼓励才敢发言、但观察很仔细"
      voice: "温柔小女孩声音"

  assets:
    cards:
      - id: "card1"
        title: "知识点卡片标题 🧐"
        content: "卡片内容"
        image: "/assets/card.png"

chapters:
  - id: "intro"
    title: "小故事引入"
    skeleton:
      - "讲一个有趣的小故事引入主题"
      - "向小朋友提问引发思考"
      - "点出今天要学的内容"
    interaction:                         # Teacher asks, waits for student
      prompt: "你会怎么做呀？"
      expect_keywords: ["会", "不会", "跑"]  # optional: expected answer keywords
    visual:
      type: card
      ref: "card1"

  - id: "quiz_chapter"
    title: "测验章节"
    skeleton:
      - "讲解知识点"
    quiz:                                # Multiple-choice quiz
      question: "小明掉进了哪个小陷阱？"
      options:
        - { key: "A", text: "「大家都这样」陷阱 🐑", correct: true }
        - { key: "B", text: "「大人说的都对」陷阱 👨‍🏫", correct: false }
        - { key: "C", text: "「只能选一个」陷阱 🎨", correct: false }
      explanation_correct: "太棒了！🌟 答对了！"
      explanation_wrong: "差一点点！再想想～"
```

### Chapter Fields

| Field | Required | Description |
|-------|:------:|-------------|
| `id` | Yes | Unique chapter identifier |
| `title` | Yes | Chapter title (shown on whiteboard) |
| `skeleton` | Yes | List of knowledge points, each 15-40 chars |
| `interaction` | No | Open-ended question. Fields: `prompt` (required), `expect_keywords` (optional) |
| `quiz` | No | Multiple-choice quiz with options and explanations |
| `visual` | No | Reference to an asset card (`type` + `ref`) |

---

## One-Click Course Generation

Generate a complete course from a topic. The system auto-adapts teaching strategy to the child's age:

| Age | Strategy | Chapters | Style |
|-----|----------|:--------:|-------|
| 4-6 | 🏃 动作和游戏主导 | 2 | 夸张语气、击掌跳舞、具象游戏 |
| 7-8 | 🕵️ 故事与线索主导 | 3 | 侦探破案、成长型鼓励、角色扮演 |
| 9-10 | ⚔️ 策略与深度互动 | 4 | PK挑战、追问"你怎么想的"、批判思维 |

`chapter_count` is optional — if omitted, auto-calculated from age.

```bash
# 4-6岁: 游戏化、夸张语气、每句≤10字
curl -X POST http://localhost:8082/api/teaching/generate \
  -H "Content-Type: application/json" \
  -d '{"topic": "认识颜色和形状", "age": "4-6"}'

# 7-8岁: 侦探破案、成长型鼓励
curl -X POST http://localhost:8082/api/teaching/generate \
  -H "Content-Type: application/json" \
  -d '{"topic": "什么是逻辑思维", "age": "7-8"}'

# 9-10岁: PK挑战、追问思维过程
curl -X POST http://localhost:8082/api/teaching/generate \
  -H "Content-Type: application/json" \
  -d '{"topic": "识别逻辑谬误", "age": "9-10"}'
```

Response:
```json
{
  "success": true,
  "course_name": "逻辑小侦探大冒险_7-8",
  "chapters": 3,
  "path": "config/courses/逻辑小侦探大冒险_7-8.yaml"
}
```

The generated course file follows `{course_title}_{age}.yaml` naming. Restart with the new course:

```bash
TEACHING_COURSE=逻辑小侦探大冒险_7-8 python teaching/agent.py
```

### Batch Generation

Generate all 30 pre-designed courses (10 per age stage) at once:

```bash
bash batch_generate.sh           # all 30 courses
bash batch_generate.sh --stage 1 # only 4-6 year olds
bash batch_generate.sh --dry-run # preview topics only
```

---

## Features

### Interactive Teaching Flow

```
Greeting → Chapter 1 → Knowledge Point 1 → Whiteboard Step 1
                                              ↓
                      Knowledge Point 2 → Whiteboard Step 2
                                              ↓
                      [AI Classmate interjects] ← 20% chance
                                              ↓
                      Chapter End → Quiz or Interaction
                                              ↓
                      Chapter 2 → ...
```

### Raise-Hand Q&A

1. Student clicks **🙋 举手提问**
2. Teacher pauses, mic opens
3. Student speaks → VAD detects speech → `control.interrupt` stops TTS
4. ASR transcribes → teacher answers → transition → resume from breakpoint

### Age-Adaptive Teaching Strategy

The teacher's persona, tone, pacing, and interaction style auto-adapt to the child's developmental stage:

| Dimension | 4-6 years | 7-8 years | 9-10 years |
|-----------|-----------|-----------|------------|
| Role | 游戏玩伴 | 侦探/队长 | 挑战对手 |
| Salutation | 宝贝、小可爱 | 小侦探、小队员 | 小挑战者、小天才 |
| Speech speed | 0.85x, very slow | 0.88x, moderate | 0.92x, faster |
| Max words/sentence | ≤10 | ≤15 | ≤20 |
| Quiz options | 2-3, obvious wrong | 3, encouraging | 4, with traps |
| Error response | "击个掌再来！" | "发现新线索！再看看？" | "你怎么想的？换个角度？" |

### AI Classmates

Four distinct AI classmates participate in the classroom:

| Classmate | Personality | Quiz Accuracy | Role |
|-----------|------------|:------------:|------|
| 小明 | 好奇心强、偶尔答错 | ~70% | Creates teachable moments |
| 小红 | 乖巧懂事、通常正确 | ~90% | Peer role model |
| 小刚 | 活泼好动、抢答出错 | ~50% | Adds energy, shows impulsivity |
| 小美 | 安静内向、观察仔细 | ~80% | Models courage to speak up |

- Spontaneously interject after knowledge points (20% probability each)
- Answer interaction questions if the student doesn't respond
- Participate in quizzes with varied accuracy matching their personality

### Whiteboard (v2)

Each knowledge point becomes a step on the in-browser whiteboard:
- **Completed** steps are greyed out with a check pattern
- **Current** step is highlighted with an amber accent bar
- **Upcoming** steps are dimmed
- Steps animate with CSS transitions

### Quiz

- Multiple choice with 3-4 options
- Emoji-animated feedback (🌟 correct, 💪 incorrect)
- AI classmate also answers (sometimes wrong)
- Correct answer explanation shown regardless

---

## HTTP API Reference

### Agent Control

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/start-session` | Start teaching session, load course |
| `POST` | `/api/stop-session` | End session |
| `POST` | `/api/teaching/start` | Start lecture |
| `POST` | `/api/teaching/pause` | Pause lecture |
| `POST` | `/api/teaching/resume` | Resume lecture |

### Student Interaction

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/teaching/raise-hand` | Student raises hand → teacher pauses |
| `POST` | `/api/teaching/cancel-hand` | Cancel raise-hand |
| `POST` | `/api/teaching/quiz-answer` | Submit quiz answer `{chapter_id, answer}` |
| `GET`  | `/api/teaching/status` | Full state: chapter, quiz, whiteboard, messages |

### Course Generation

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/teaching/generate` | Generate course `{topic, age, chapter_count?}`. Age drives strategy. |

### Frontend

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/` | Teaching classroom page |
| `GET`  | `/sdk.js` | Live Avatar JS SDK |

---

## Module Reference

```
teaching/
├── agent.py                   Entry point — HTTP server + ASR + platform integration
├── teaching_controller.py    State machine — LECTURING ↔ ANSWERING ↔ QUIZ
├── course_manager.py         YAML loading + validation + persona parsing
├── course_component.py       Component protocol — whiteboard, quiz, visual cards
├── persona_manager.py        7-dim persona config → LLM system prompts
├── classmate_engine.py       AI classmate behavior + speech generation
├── manager_agent.py          Adaptive classroom pacing decisions
├── course_generator.py       Two-stage LLM course gen + 3-tier age profiles
└── tts_client.py             DashScope CosyVoice TTS for classmates
```

### Architecture

```
Browser (teaching.html)
    │
    ├─ Live Avatar JS SDK ──→ Platform WS (RTC video + audio)
    │                              │
    ├─ POST /api/teaching/* ──→ teaching/agent.py
    │                              │
    └─ GET /api/teaching/status ←──┤ (polling: chapter, quiz, whiteboard, messages)
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
            TeachingController  QwenAsrManager  ClassmateEngine (4 classmates)
                    │              │              │
            CourseManager      DeepSeek LLM    DashScope TTS
                    │
            PersonaManager (7-dim) → Teacher prompt, Classmate prompt
            ManagerAgent        → Adaptive pacing decisions
            CourseGenerator     → Two-stage gen + 3-tier age profiles
```

---

## Troubleshooting

| Symptom | Likely Fix |
|---------|-----------|
| `Course config not found` | Set `TEACHING_COURSE` or create `config/courses/thinking_4-10.yaml` |
| `LIVEAVATAR_API_KEY not set` | Export the env var from Step 4 |
| Avatar connects but doesn't speak | Check `DEEPSEEK_API_KEY` — LLM is required for skeleton polish |
| ASR not working | Check `DASHSCOPE_API_KEY` — required for real-time speech recognition |
| Teacher waits long before starting | LLM is generating the first knowledge point; greeting plays immediately |
| Question not recognized | Speak clearly into the mic after clicking 举手提问; silence duration is 800ms for children |
| Quiz options repeating | Hard-refresh the browser (Ctrl+Shift+R) to clear cached JS |
| AI classmate not speaking | Ensure `classmates` is in course YAML; classmate speaks at ~20% probability |
| Course generation returns empty | Check `DEEPSEEK_API_KEY` and `DEEPSEEK_MODEL`; the model must support `response_format: json_object` |
| Course generation returns "LLM returned invalid JSON" | The model truncated its response; retry is automatic (up to 3x with doubling max_tokens). If persistent, try a less complex topic. |
| `append audio` log spam | Suppressed by default (dashscope logger set to WARNING) |
