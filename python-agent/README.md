# Live Avatar Python Agents

Python-based digital human agents for the Live Avatar platform.

---

## Project Structure

```
python-agent/
├── chat/                     # Agent A: Conversational digital human (interactive)
│   ├── agent.py                  # Entry point — HTTP server + ASR + LLM (port 8080)
│   └── QUICKSTART.md
├── broadcast/                # Agent B: E-commerce live broadcast (autonomous)
│   ├── agent.py                  # Entry point — HTTP server + config + LLM (port 8081)
│   ├── controller.py             # Broadcast queue engine + state machine
│   ├── product_manager.py        # YAML config loader + video/script management
│   ├── script_generator.py       # LLM-driven product script generation
│   ├── tiktok_monitor.py         # TikTok Live chat listener (comments, joins)
│   └── QUICKSTART.md
├── teaching/                 # Agent C: Teaching digital human (multi-agent classroom)
│   ├── agent.py                   # Entry point — HTTP server + ASR (port 8082)
│   ├── teaching_controller.py    # 8-state state machine + lecture loop
│   ├── course_manager.py         # YAML course loading + validation
│   ├── course_component.py       # UI component protocol (quiz, whiteboard, cards)
│   ├── persona_manager.py        # Teacher & classmate persona → LLM prompts
│   ├── classmate_engine.py       # AI classmate behavior + speech generation
│   ├── manager_agent.py          # Adaptive classroom pacing decisions
│   ├── course_generator.py       # One-click LLM course generation from topic
│   └── QUICKSTART.md
├── interview/                # Interview digital human (structured mock interview)
│   ├── agent.py                  # Entry point — HTTP server + ASR + interview controller (port 8083)
│   ├── controller.py             # Question/exchange loop + metadata tracking
│   ├── interview_manager.py      # YAML interview config loader
│   ├── answer_evaluator.py       # LLM JSON evaluation + fallback
│   ├── question_planner.py       # Next-question and follow-up selection
│   ├── report_generator.py       # Final report generation
│   ├── listener.py               # LiveAvatar / ASR event bridge
│   └── QUICKSTART.md
├── llm_client.py             # Shared async LLM client (OpenAI-compatible)
├── config/
│   ├── products.yaml         # Product/script/video configuration
│   ├── interview.yaml        # Interview persona, questions, rubric
│   └── courses/
│       └── thinking_4-10.yaml     # Kids thinking course (3 chapters, age 4-10)
├── tests/
│   ├── teaching/             # Teaching agent tests
│   │   ├── test_classmate_engine.py
│   │   ├── test_course_component.py
│   │   ├── test_course_manager.py
│   │   ├── test_manager_agent.py
│   │   ├── test_speech_gating.py
│   │   ├── test_teaching_agent_session.py
│   │   └── test_teaching_controller.py
│   ├── broadcast/            # Broadcast agent tests
│   │   ├── test_controller.py
│   │   ├── test_product_manager.py
│   │   └── test_script_generator.py
│   └── test_llm_client.py
├── requirements.txt
├── pytest.ini
└── README.md
```

---

## Setup

### 1. Install dependencies

```bash
cd python-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Environment variables

```bash
# Required
export LIVEAVATAR_API_KEY="lk_live_xxx"       # Live Avatar platform key
export LIVEAVATAR_AVATAR_ID="avatar_xxx"      # Avatar ID (required)
export DEEPSEEK_API_KEY="sk-xxx"              # LLM API key (DeepSeek or OpenAI)

# Optional — defaults shown
export DEEPSEEK_BASE_URL="https://api.deepseek.com"  # LLM base URL
export DEEPSEEK_MODEL="deepseek-v4-flash"     # LLM model name
export BROADCAST_HTTP_PORT="8081"             # Port for broadcast agent (agent.py uses 8080)
export INTERVIEW_HTTP_PORT="8083"             # Port for interview agent
```

For OpenAI (ChatGPT):

```bash
export DEEPSEEK_API_KEY="sk-your-openai-key"
export DEEPSEEK_BASE_URL="https://api.openai.com/v1"
export DEEPSEEK_MODEL="gpt-4o"
```

---

## Agent A: Conversational Digital Human

**File:** `chat/agent.py` | **Port:** 8080

An interactive digital human that listens via ASR and responds with LLM-generated answers.

```
User speaks → Qwen ASR → DeepSeek LLM → Platform TTS → Avatar speaks
```

### Run

```bash
python chat/agent.py
# Open http://localhost:8080
```

### Features
- Real-time speech recognition (DashScope Qwen ASR)
- Streamed LLM responses with typewriter effect
- Voice interruption (speak while avatar is talking to interrupt)
- Conversation history management

### API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/start-session` | Start a new session → returns `userToken`, `sfuUrl` |
| `POST` | `/api/stop-session` | Stop current session |
| `POST` | `/api/interrupt` | Interrupt current response |
| `GET`  | `/api/logs` | Fetch server logs |
| `POST` | `/api/clear-logs` | Clear server logs |

---

## Agent B: E-commerce Live Broadcast

**File:** `broadcast/agent.py` | **Port:** 8081

An autonomous digital human for live shopping. Plays pre-written product scripts in a queue, switches background videos, listens to TikTok live comments, and auto-regenerates fresh scripts.

```
Product YAML → Broadcast queue → scene.switchVideo → system.prompt (TTS)
                                    ↑
                              TikTok comments/joins → LLM reply → enqueue
```

### Run

```bash
python broadcast/agent.py
# Open http://localhost:8081
```

### Configuration (`config/products.yaml`)

```yaml
settings:
  loop: true               # Queue loops after all products played
  lang: en                 # "zh" for Chinese, "en" for English
  default_tts_speed: 1.0
  default_pause_ms: 300    # Pause between scripts (ms)
  default_loop_video: 01KTT909B2M3PABS719FN7Z3WA

  # TikTok Live monitoring (optional)
  live_url: "https://www.tiktok.com/@username/live"
  comment_cooldown_s: 10   # Min seconds between comment replies
  join_cooldown_s: 30      # Min seconds between join welcomes

  # Proxy for TikTok (if needed)
  tiktok_web_proxy: "http://127.0.0.1:7890"
  tiktok_ws_proxy: "http://127.0.0.1:7890"

products:
  - id: "1731199058452648921"
    name: "Product Title"
    description: "Product description text for script generation"
    url: "https://www.tiktok.com/shop/pdp/1731199058452648921"
    loop_video: 01KTT909B2M3PABS719FN7Z3WA
    tts_speed: 1.0
    pause_after_script_ms: 800
    video_scripts:
      - video: 01KTT909B2M3PABS719FN7Z3WA    # onceVideos (showcase)
        scripts:
          - "Script segment 1..."
          - "Script segment 2..."
      - video: 01KTT92T3XEJRMEYSMWT3B3CYK
        scripts:
          - "Script segment 3..."
```

### Features
- **Queue-based broadcast** — products play in order, all video-script pairs sequentially
- **scene.switchVideo** — automatic video switching with `onceVideos` + `loopVideos`
- **Platform-driven pacing** — waits for `session.state=IDLE` before next script (zero gaps)
- **75% auto-regeneration** — background LLM generates fresh scripts before current batch ends
- **TikTok Live monitoring** — rate-limited comment replies & join welcomes inserted into queue
- **Multi-language** — Chinese (`zh`) and English (`en`) prompt templates

### API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/broadcast/start` | Start broadcast queue |
| `POST` | `/api/broadcast/stop` | Stop broadcast |
| `POST` | `/api/broadcast/pause` | Pause after current script |
| `POST` | `/api/broadcast/resume` | Resume broadcast |
| `POST` | `/api/broadcast/skip` | Skip current product |
| `GET`  | `/api/broadcast/status` | Current state, queue position |
| `POST` | `/api/comment` | External comment → insert reply |
| `POST` | `/api/product/generate` | Generate scripts from product info |
| `POST` | `/api/product/scripts` | Manually append/update scripts |
| `POST` | `/api/product/reload` | Hot-reload `products.yaml` |
| `POST` | `/api/start-session` | Compatibility with frontend |
| `GET`  | `/api/session-info` | Get session details |

### Quick Start

```bash
# 1. Write product name + description in products.yaml

# 2. Generate scripts via LLM
curl -X POST http://localhost:8081/api/product/generate \
  -H "Content-Type: application/json" \
  -d '{"productId":"1731199058452648921"}'

# 3. Start broadcast (auto-starts on scene.ready)
# Or manually: curl -X POST http://localhost:8081/api/broadcast/start

# 4. Simulate viewer comment
curl -X POST http://localhost:8081/api/comment \
  -H "Content-Type: application/json" \
  -d '{"text":"Does this ship internationally?"}'
```

---

## Agent C: Teaching Digital Human (Multi-Agent Classroom)

**Package:** `teaching/` | **Entry:** `teaching/agent.py` | **Port:** 8082

A multi-agent interactive classroom for children aged 4-10. Features an AI teacher with configurable persona, AI classmates that participate in discussions, adaptive pacing, and one-click course generation.

```
Course YAML → TeachingController → LLM-polished lecture → Platform TTS
                    ↑                       ↓
              Student raise-hand    AI Classmate interjects
                    ↓                       ↓
              Qwen ASR → QA → Transition → Resume from breakpoint
```

### Run

```bash
python teaching/agent.py
# Open http://localhost:8082
```

### Features
- **Chapter-based lectures** — skeleton points polished by LLM into child-friendly speech
- **Raise-hand Q&A** — student interrupts via button + voice, teacher answers and resumes
- **AI Classmates** — configurable AI students that ask questions and participate in quizzes
- **Adaptive Scheduling** — Manager Agent adjusts pacing based on student engagement
- **Interactive Quiz** — multiple choice with emoji feedback and encouragement animations
- **Whiteboard** — step-by-step knowledge point display with CSS animations
- **One-Click Generation** — full course generated from a topic description via LLM
- **Child-Optimized** — slower TTS (0.9x), longer ASR silence (800ms), encouraging tone

### Course Configuration

```yaml
# config/courses/thinking_4-10.yaml
course:
  title: "思维小达人"
  lang: zh

  persona:                             # Teacher persona (v2)
    name: "小思老师"
    style: "亲和活泼"
    rules:
      - "每句话不超过15个字"
      - "先用小朋友熟悉的事物打比方"

  classmates:                          # AI classmates (v2)
    - name: "小明"
      style: "好奇心强、偶尔问天真问题"

chapters:
  - id: "intro"
    title: "引入"
    skeleton:
      - "讲一个有趣的小故事"
      - "向小朋友提问"
    interaction:
      prompt: "你会怎么做呀？"

  - id: "quiz_chapter"
    title: "测验"
    skeleton:
      - "讲解知识点"
    quiz:
      question: "小明掉进了哪个小陷阱？"
      options:
        - { key: "A", text: "大家都这样", correct: true }
        - { key: "B", text: "大人说的都对", correct: false }
      explanation_correct: "太棒了！🌟"
      explanation_wrong: "差一点点！💪"
```

### API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/start-session` | Start teaching session |
| `POST` | `/api/stop-session` | End session |
| `POST` | `/api/teaching/start` | Start lecture |
| `POST` | `/api/teaching/pause` | Pause lecture |
| `POST` | `/api/teaching/resume` | Resume lecture |
| `POST` | `/api/teaching/raise-hand` | Student raises hand → pause + open mic |
| `POST` | `/api/teaching/cancel-hand` | Cancel raise-hand |
| `POST` | `/api/teaching/quiz-answer` | Submit quiz answer `{chapter_id, answer}` |
| `POST` | `/api/teaching/generate`  | Generate course from `{topic, age}` |
| `GET`  | `/api/teaching/status` | Full state: chapter, quiz, whiteboard, messages |

### Quick Start

```bash
# 1. Use default course or generate a new one
curl -X POST http://localhost:8082/api/teaching/generate \
  -H "Content-Type: application/json" \
  -d '{"topic": "什么是逻辑思维", "age": "4-10"}'

# 2. Open http://localhost:8082 → Connect → Start learning
```

---

## Agent D: Interview Digital Human

**Package:** `interview/` | **Entry:** `interview/agent.py` | **Port:** 8083

A structured mock interview avatar. The avatar asks one question at a time,
waits for the candidate's voice answer, evaluates the response, asks bounded
follow-ups, then produces a final report.

```
Interview YAML → InterviewController → system.prompt + metadata
                         ↑                         ↓
                   Qwen ASR ← candidate voice ← input.voice/asr + metadata
                         ↓
                 evaluator → follow-up or next question → report
```

### Run

```bash
python interview/agent.py
# Open http://localhost:8083
```

### API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/start-session` | Start LiveAvatar session |
| `POST` | `/api/stop-session` | Stop LiveAvatar session |
| `POST` | `/api/interview/start` | Start interview loop |
| `POST` | `/api/interview/stop` | Stop interview loop |
| `GET`  | `/api/interview/status` | Current state, question, exchange, report |
| `GET`  | `/api/session-info` | Get session details |

---

## Tests

```bash
pytest tests/ -v
```

---

## Architecture Notes

| Module | Responsibility |
|--------|---------------|
| `llm_client.py` | Async LLM client (DeepSeek/OpenAI compatible). Stateless per request. |
| `broadcast/product_manager.py` | Loads `config/products.yaml`, manages video-script random selection, CRUD. |
| `broadcast/script_generator.py` | Fetches product info + generates 8-segment broadcast scripts via LLM. |
| `broadcast/controller.py` | Queue engine with IDLE-driven pacing, video switching, pause/resume. |
| `broadcast/tiktok_monitor.py` | Connects to TikTok Live WS, rate-limited comment/join callbacks. |
| `chat/agent.py` | Original interactive agent (ASR → LLM → TTS). |
| `teaching/teaching_controller.py` | 8-state machine for LECTURING ↔ ANSWERING ↔ QUIZZING. |
| `teaching/persona_manager.py` | Generates role-specific system prompts from persona config. |
| `teaching/classmate_engine.py` | AI classmate behavior decision + speech generation. |
| `teaching/manager_agent.py` | Lightweight LLM decisions for adaptive pacing. |
| `teaching/course_generator.py` | Two-stage LLM generation: outline → full course YAML. |
| `interview/controller.py` | Structured interview state machine with question/exchange metadata. |
| `interview/listener.py` | Developer ASR bridge that sends `input.voice.*` and `input.asr.*` metadata. |
| `interview/answer_evaluator.py` | Parses LLM JSON evaluations and supplies fallback scoring. |

### TTS Pacing

Teaching Controller uses platform `session.state=IDLE` events to know when TTS finishes. After each prompt: `await tts_idle.wait()` → `tts_idle.clear()` → send next prompt. This eliminates gaps. A 30s timeout prevents deadlock.

### Interrupt Flow

```
Student clicks 举手提问 → raise_hand() saves breakpoint → state=ANSWERING
Student speaks → VAD speech_started → control.interrupt + voice.start
ASR transcript → QA flow → answer → transition → resume from breakpoint
```

---

## Quickstart Guides

- [Chat Digital Human](chat/QUICKSTART.md) — `chat/agent.py`, interactive conversation
- [Live Shopping Broadcast](broadcast/QUICKSTART.md) — `broadcast/agent.py`, autonomous product narration
- [Teaching Classroom](teaching/QUICKSTART.md) — `teaching/agent.py`, multi-agent interactive classroom
- [Interview Avatar](interview/QUICKSTART.md) — `interview/agent.py`, structured mock interview
