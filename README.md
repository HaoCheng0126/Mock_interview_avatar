# Live Avatar WebSocket Agent — Integration Demo

基于 [liveavatar-channel SDK](https://github.com/newportAI-lab/liveavatar-channel) 的 WebSocket Agent 模式集成示例，提供 **Python** 和 **Java** 两套 agent 实现，共享同一套前端页面。

## 项目结构

```
liveavatar-ws-integration-demo/
├── frontend/                  # 共享前端页面
│   ├── index.html             # 演示页面（发送消息、查看 Avatar 视频）
│   ├── package.json           # 依赖 @sanseng/liveavatar-js-sdk
│   └── node_modules/
├── python-agent/              # Python agent 实现
│   ├── agent.py               # aiohttp + AvatarAgent + Qwen ASR + DeepSeek LLM
│   └── requirements.txt
├── java-agent/                # Java agent 实现
│   ├── pom.xml                # Spring Boot 2.7 + liveavatar-channel-sdk
│   └── src/main/java/com/example/agent/
│       ├── AgentApplication.java
│       ├── controller/AgentController.java    # REST API
│       ├── service/AgentService.java          # AgentListener 实现
│       └── llm/MockLlmService.java            # Mock LLM 回复
└── README.md
```

## 架构

```
浏览器 (index.html + JS SDK)
   │  POST /api/start-session
   ▼
Agent (Python 或 Java)
   │  ① POST /v1/session/start → 平台返回 {agentWsUrl, userToken, sfuUrl}
   │  ② WSS connect agentWsUrl → 平台发送 session.init
   │  ③ Agent 回复 session.ready → 握手完成
   ▼
平台 (WebSocket Server + SFU)
   │  userToken + sfuUrl 返回浏览器
   ▼
浏览器加入 LiveKit 房间 → 数字人画面渲染
   │  用户输入文本
   ▼
平台 → Agent WS: input.text
   │  Agent 生成回复
   ▼
Agent → 平台 WS: response.chunk + response.done
   │  平台 TTS 合成
   ▼
数字人说话
```

## 快速开始

### 方式一：Hub 控制台（推荐）

统一的配置页面 + agent 管理器，无需手动导出环境变量：

```bash
cd python-agent
python hub/hub.py
# 浏览器打开 http://localhost:8000
```

在控制台页面上可以：

- 配置平台接入（API Key / Avatar ID / 音色）、大模型 LLM（Key / Base URL / 模型 / 系统提示词）、语音识别 ASR
- 一键启动 / 停止 / 切换 5 个 agent（聊天、直播带货、教学、面试、脱口秀），并查看实时日志
- 配置保存在 `python-agent/config/hub_settings.json`（本地文件，权限 600，已 gitignore），agent 下次启动时生效
- 模拟面试参数配置页（`/interview-config`）：面试官人设、口播话术、4 个 LLM 提示词模板、工作流超时阈值、题库与评分维度，保存写入 `config/interview.yaml`，下一场面试自动生效（占位符说明见 `python-agent/interview/prompts.py`）

### 方式二：环境变量 + 手动启动

### 环境变量

| 变量                     | 必填  | 默认值                                            | 说明                    |
| ---------------------- | --- | ---------------------------------------------- | --------------------- |
| `LIVEAVATAR_API_KEY`   | 是   | —                                              | 平台 API Key            |
| `LIVEAVATAR_AVATAR_ID` | 否   | `avatar_01k56rnqaz15fz4t0ha4ja1132`            | Avatar ID             |
| `LIVEAVATAR_VOICE_ID`  | 否   | —                                              | 音色 ID（覆盖 avatar 默认）   |
| `LIVEAVATAR_BASE_URL`  | 否   | `https://liveavatar.aimiai.com/vih/dispatcher` | 平台地址                  |
| `DASHSCOPE_API_KEY`    | 是   | —                                              | 百炼 API Key（Qwen ASR）  |
| `DEEPSEEK_API_KEY`     | 是   | —                                              | DeepSeek API Key（LLM） |
| `DEEPSEEK_BASE_URL`    | 否   | `https://api.deepseek.com`                     | DeepSeek 兼容接口地址       |
| `DEEPSEEK_MODEL`       | 否   | `deepseek-v4-flash`                            | LLM 模型名               |
| `SYSTEM_PROMPT`        | 否   | —                                              | LLM 系统提示词             |

### Python Agent

```bash
cd python-agent

# 创建虚拟环境（首次）
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

# 首次需要先安装 Python SDK（从本地仓库）
pip install -e ../../liveavatar-channel-python
export DASHSCOPE_API_KEY=sk-xxx
export DEEPSEEK_API_KEY=sk-xxx
export LIVEAVATAR_API_KEY=your_key
python agent.py
# 浏览器打开 http://localhost:8080
```

### Java Agent

```bash
cd java-agent
LIVEAVATAR_API_KEY=your_key mvn spring-boot:run
# 浏览器打开 http://localhost:8081
```

## API

两个 agent 提供完全相同的 REST API，前端无需修改。

| 方法     | 路径                   | 说明                                               |
| ------ | -------------------- | ------------------------------------------------ |
| `GET`  | `/`                  | 演示页面                                             |
| `GET`  | `/sdk.js`            | LiveAvatar JS SDK                                |
| `POST` | `/api/start-session` | 创建会话 → `{success, userToken, sfuUrl, sessionId}` |
| `POST` | `/api/stop-session`  | 结束会话 → `{success}`                               |
| `GET`  | `/api/logs`          | 获取 Agent 日志                                      |

## Agent 模式

当前使用 **Developer ASR + Platform TTS** 模式：

- 平台转发原始 PCM 音频 → Agent 调用 Qwen3-ASR-Flash-Realtime 实时转写为文本
- Agent 调用 DeepSeek LLM 生成回复
- Agent 发送 `response.chunk` + `response.done`
- 平台 TTS 合成语音 → 数字人说话

如需切换为 Platform ASR，修改配置：

```python
developer_asr=False,
```

## LLM & ASR

Python agent 已集成真实 ASR 和 LLM：

**ASR** — Qwen3-ASR-Flash-Realtime via DashScope WebSocket

- SDK：`dashscope` (`OmniRealtimeConversation`)
- 模型：`qwen3-asr-flash-realtime`
- 音频：PCM 16kHz mono，Base64 编码实时流式发送
- VAD：服务端自动断句

**LLM** — DeepSeek V4 Flash via OpenAI 兼容 API

- SDK：`openai` (`AsyncOpenAI`)
- 流式输出，delta → `response.chunk`
- 对话上下文自动管理（最近 20 轮）

如需更换 LLM 提供商，修改 `LlmClient` 的 `base_url` 和 `model`：

```python
# agent.py
DEEPSEEK_BASE_URL="https://your-llm-endpoint/v1"
DEEPSEEK_MODEL="your-model"
```

Java agent 仍使用 Mock LLM，参考实现即可。
