# Quickstart — Chat Digital Human (chat/agent.py)

Step-by-step guide to get the conversational digital human running.

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
export LIVEAVATAR_API_KEY="lk_live_xxx"      # Your Live Avatar platform API key
export LIVEAVATAR_AVATAR_ID="avatar_xxx"     # Your avatar ID
export DEEPSEEK_API_KEY="sk-xxx"             # DeepSeek or OpenAI API key
export DASHSCOPE_API_KEY="sk-xxx"            # Alibaba DashScope key (for Chinese ASR)

# Optional — for English or other LLM providers
export DEEPSEEK_BASE_URL="https://api.openai.com/v1"   # if using OpenAI
export DEEPSEEK_MODEL="gpt-4o"                         # model name
```

> **DashScope API key** is required for real-time Chinese speech recognition.
> Get one at https://bailian.console.aliyun.com
> If not set, the agent falls back to platform ASR (less accurate).

## Step 5: Run

```bash
python chat/agent.py
```

You should see:
```
🌐 Demo server at http://localhost:8080
```

## Step 6: Open in browser

Go to **http://localhost:8080**

Click **Connect** → the digital human appears. Click **Start Mic** to speak to it.

## Troubleshooting

| Symptom | Likely Fix |
|---------|-----------|
| `LIVEAVATAR_API_KEY not set` | Export the env var from Step 4 |
| `Missing credentials` | `DEEPSEEK_API_KEY` is empty |
| Avatar connects but doesn't speak | Check `DASHSCOPE_API_KEY` or platform ASR fallback |
| Microphone not working | Browser needs HTTPS or `localhost` for mic access |
