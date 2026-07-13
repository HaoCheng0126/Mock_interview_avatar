# Quickstart — Live Shopping Broadcast (broadcast/agent.py)

Step-by-step guide to get the e-commerce broadcast digital human running.

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
export LIVEAVATAR_API_KEY="lk_live_xxx"       # Your Live Avatar platform API key
export LIVEAVATAR_AVATAR_ID="avatar_xxx"      # Your avatar ID
export DEEPSEEK_API_KEY="sk-xxx"              # DeepSeek or OpenAI API key

# For OpenAI (ChatGPT) instead of DeepSeek
# export DEEPSEEK_BASE_URL="https://api.openai.com/v1"
# export DEEPSEEK_MODEL="gpt-4o"

# Optional
export BROADCAST_HTTP_PORT="8081"             # Default: 8081
```

## Step 5: Configure your products

Edit `config/products.yaml`. You need at minimum:

```yaml
settings:
  loop: true
  lang: en                           # "en" for English, "zh" for Chinese
  default_loop_video: YOUR_VIDEO_ID  # Background loop video resourceId

products:
  - id: "your-product-id"
    name: "Your Product Name"
    description: "Product description — used for script generation."
    loop_video: YOUR_VIDEO_ID
    video_scripts:
      - video: YOUR_VIDEO_ID
        scripts: []                  # Leave empty, LLM will fill these
```

**Get `YOUR_VIDEO_ID`**: Upload a background video to the Live Avatar platform. The platform returns a `resourceId`. Use that ID here.

### Optional: Crypto market risk-education mode

To run the generic crypto market commentary demo instead of the shopping demo:

```bash
export PRODUCTS_CONFIG_PATH="config/crypto_market.yaml"
python broadcast/agent.py
```

This mode is for broad market commentary and risk education only. It does not fetch live prices, provide buy/sell instructions, predict short-term moves, or promise returns.

## Step 6: Generate scripts via LLM

Start the agent first, then call the generate API:

```bash
# Terminal 1: Start the agent
python broadcast/agent.py
# Wait for: Running on http://0.0.0.0:8081

# Terminal 2: Generate scripts
curl -X POST http://localhost:8081/api/product/generate \
  -H "Content-Type: application/json" \
  -d '{"productId":"your-product-id"}'
```

The LLM will generate 8 script segments and save them to `config/products.yaml`.

## Step 7: Open in browser

Go to **http://localhost:8081**

Click **Connect**. The agent auto-starts broadcasting when the scene loads.

## Optional: TikTok Live monitoring

To reply to live stream comments and welcome new viewers:

```yaml
# config/products.yaml
settings:
  live_url: "https://www.tiktok.com/@your_username/live"
  comment_cooldown_s: 10            # Min seconds between replies
  join_cooldown_s: 30               # Min seconds between welcomes

  # If you're behind a VPN/proxy (Clash, V2Ray...)
  tiktok_web_proxy: "http://127.0.0.1:7890"
  tiktok_ws_proxy: "http://127.0.0.1:7890"
```

> **Note**: TikTokLive requires the account to be actively livestreaming and may need proxy depending on your region.

## API Cheat Sheet

```bash
# Start / stop broadcast
curl -X POST http://localhost:8081/api/broadcast/start
curl -X POST http://localhost:8081/api/broadcast/stop

# Check status
curl http://localhost:8081/api/broadcast/status

# Generate scripts for a product
curl -X POST http://localhost:8081/api/product/generate \
  -H "Content-Type: application/json" \
  -d '{"productId":"your-product-id"}'

# Hot-reload config after manual YAML edits
curl -X POST http://localhost:8081/api/product/reload

# Simulate a viewer comment
curl -X POST http://localhost:8081/api/comment \
  -H "Content-Type: application/json" \
  -d '{"text":"Does this ship internationally?"}'

# Ask the host from the page input or API (no TikTok cooldown)
curl -X POST http://localhost:8081/api/comment \
  -H "Content-Type: application/json" \
  -d '{"text":"BTC 最近波动大，新手应该注意什么？"}'
```

## Troubleshooting

| Symptom | Likely Fix |
|---------|-----------|
| `LIVEAVATAR_API_KEY not set` | Export the env var from Step 4 |
| `LIVEAVATAR_AVATAR_ID` empty | Set the env var — no default value |
| Digital human doesn't speak | Generate scripts first (Step 6), then broadcast starts automatically |
| Same scripts repeat | 75% auto-regeneration replaces them each cycle |
| TikTok events not received | Account may not be live; try a different username or add proxy |
| `peer closed connection` | Platform connectivity issue — check API key and retry |
