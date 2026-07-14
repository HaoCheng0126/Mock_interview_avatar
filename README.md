# Mock Interview Avatar — 数字人模拟面试

基于 [LiveAvatar 平台](https://github.com/newportAI-lab/liveavatar-channel) 的数字人模拟面试系统：数字人面试官按题库结构化提问，候选人语音作答，LLM 实时评估并决定追问，面试结束生成多维度评估报告。

## 功能

- **数字人面试官** — LiveAvatar 视频渲染 + 平台 TTS 播报，支持 9:16 竖屏
- **实时语音识别** — DashScope Qwen ASR 流式转写（WebSocket Agent 模式下 ASR 由开发者侧提供；未配置时仅支持文本作答）
- **结构化面试流程** — 题库顺序出题、答后 LLM 判定追问（每题可设上限）、思考提醒、超时跳题、连续无效作答提前终止
- **LLM 评估管线** — 回答后台评估、追问判定、终局多维度报告（评分/证据/建议/置信度）
- **面试准备入口** — 面试页直接填岗位名称、粘贴 JD、上传简历（PDF / Word / 文本），面试官提问、追问、评估、报告全程参考
- **知识库** — JD/简历/领域资料以条目形式注入 LLM 系统提示词，带字数预算与截断保护
- **Hub 控制台** — 平台/LLM/ASR 凭证配置、agent 启停与日志、面试参数全量在线编辑（人设、话术、工作流、提示词模板、题库），带**最终提示词实时预览**

## 架构

```
浏览器 interview.html (JS SDK)
   │ POST /api/start-session          ┌── DeepSeek 兼容 LLM（评估/追问/报告）
   ▼                                  │
面试 Agent (aiohttp :8083) ──────────┤
   │ WSS 协议握手 / response.chunk    └── DashScope Qwen ASR（语音转写）
   ▼
LiveAvatar 平台 (WebSocket + SFU) → TTS 合成 → 数字人说话
   ▲
Hub 控制台 (:8000) — 保存配置并以环境变量拉起 agent
```

配置流：`config/interview.yaml` 每场面试重新加载 —— 控制台或面试页保存后，下一场面试即生效，无需重启。

## 目录结构

```
frontend/
├── interview.html        # 面试页（含「面试准备」：岗位/JD/简历上传）
├── hub.html              # Hub 控制台（凭证配置 + agent 启停）
├── hub-interview.html/.js # 面试配置页（5 个 Tab + 提示词实时预览）
└── package.json          # @sanseng/liveavatar-js-sdk
python-agent/
├── hub/                  # 控制台服务：配置存储、进程管理、面试配置 API
├── interview/            # 面试 agent：状态机、评估、追问、报告、档案接入
├── config/interview.yaml # 面试全量配置（题库/人设/话术/工作流/提示词/知识库）
├── llm_client.py         # OpenAI 兼容异步 LLM 客户端
└── tests/                # pytest 单元测试
```

## 快速开始

```bash
# 1. Python 依赖
cd python-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 前端 JS SDK
cd ../frontend && npm install

# 3. 启动 Hub 控制台
cd ../python-agent && python hub/hub.py
# 打开 http://localhost:8000
```

在控制台页面：填入下方三个 Key 并保存 → 点「启动」拉起面试 agent → 「打开页面」进入 http://localhost:8083。

面试页是候选人视角的完整流程：**打开页面自动预热面试间**（3 分钟未开始自动释放）→ 填写岗位/JD/上传简历 → 点「开始面试」→ 数字人提问、右侧实时对话转写，**语音 / 文字作答随时切换** → 结束后自动生成评估报告（总分环、能力维度、亮点/待改进、对话回放）。开发调试请在地址后加 `?debug=1`（原始状态、示例数据注入）。

也可跳过控制台直接启动：`export` 好环境变量后 `python interview/agent.py`。

### 环境变量

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `LIVEAVATAR_API_KEY` | 是 | LiveAvatar 平台 Key（数字人视频/TTS） |
| `LIVEAVATAR_AVATAR_ID` | 是 | 数字人形象 ID |
| `DEEPSEEK_API_KEY` | 是 | LLM Key（评估/追问/报告；OpenAI 兼容接口均可） |
| `DASHSCOPE_API_KEY` | 语音必需 | Qwen 实时 ASR；WebSocket Agent 模式无平台 ASR 兜底，留空则仅能文本作答 |
| `LIVEAVATAR_BASE_URL` | 否 | 默认 `https://facemarket.ai/vih/dispatcher`（与官方指南一致） |
| `LIVEAVATAR_SANDBOX` | 否 | `true` 时路由到沙箱环境（每月 30 分钟免费额度） |
| `LIVEAVATAR_VOICE_ID` | 否 | 覆盖形象默认音色 |
| `LIVEAVATAR_VOICE_SPEED` | 否 | 平台 TTS 语速 0.5–2.0，留空用默认 |
| `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` | 否 | 默认 `https://api.deepseek.com` / `deepseek-v4-flash` |
| `INTERVIEW_HTTP_PORT` / `HUB_PORT` | 否 | 默认 8083 / 8000 |

> ⚠️ **平台 TTS 前提**：本项目走「回传文本、平台合成语音」链路（`ttsProvider=platform`）。数字人需在 LiveAvatar 控制台开启平台 TTS 并配置音色（ttsProviderId / voiceId / fallbackVoiceId），否则文本回复不会驱动数字人说话（控制台默认为 `developer`，即开发者自行合成音频）。

完整模板见 [python-agent/.env.example](python-agent/.env.example)。通过 Hub 控制台配置时无需手动导出，凭证保存在本地 `python-agent/config/hub_settings.json`（权限 600，已 gitignore）。

## 配置面试内容

三个入口，同一份 `config/interview.yaml`：

1. **面试页「面试准备」**（候选人视角）— 岗位名称、JD、简历上传，写入候选人画像与知识库
2. **面试配置页** `http://localhost:8000/interview-config`（面试官视角）— 5 个 Tab：
   - 面试设定：标题/时长/难度、面试官人设（姓名/风格/规则）、候选人画像 + **最终系统提示词实时预览**
   - 知识库：多条资料的增删启停、注入字数预算
   - 题库：YAML 编辑（题面/考察点/期望信号/红旗/追问上限），保存前完整校验
   - 话术与节奏：开场白、转场、跳题、结束语、思考提醒、各超时阈值
   - 提示词模板（高级）：4 个 LLM 模板，占位符注入人设与知识库，可一键恢复默认
3. **直接编辑 YAML** — 所有省略字段回退内置默认值

## 测试

```bash
cd python-agent
pytest tests/ -q
```

## API 摘要

面试 agent（:8083）：`POST /api/start-session`、`POST /api/interview/start|stop`、`GET /api/interview/status`、`GET/POST /api/interview/profile`（岗位/JD/简历，multipart 上传 10MB 上限）。

Hub（:8000，仅监听 127.0.0.1）：`GET/POST /api/config`（凭证，响应掩码）、`GET /api/agents`、`POST /api/agents/start|stop`、`GET/POST /api/interview-config`、`POST /api/interview-config/preview`。
