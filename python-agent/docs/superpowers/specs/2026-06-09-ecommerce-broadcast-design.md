# E-commerce Broadcast Digital Human Agent — Design Spec

**Date:** 2026-06-09
**Status:** Draft
**Author:** Generated via brainstorming

---

## 1. Overview

在现有对话式数字人基础上，新增电商直播播报能力。数字人根据商品队列自动切换底板视频、播报多段脚本，支持播报中插入用户问题回复。

### 1.1 Key Decisions

| Dimension | Decision |
|-----------|----------|
| Product info | LLM directly fetches product link content |
| Script generation | Pre-generate + manual append/edit |
| Broadcast flow | Queue mode: finish one product then next |
| User interruption | Finish current sentence, reply, then resume |
| Video-script mapping | One video → N scripts; one script → exactly one video |
| Data storage | YAML config file |
| Speed control | TTS speed param + inter-segment pause |
| LLM | Same DeepSeek for scripts and replies |
| Architecture | Modular — new files, existing agent.py untouched |

### 1.2 Challenges to Address

1. **Text broadcast speed** — solved via `send_response_start(speed=...)` + inter-sentence micro-delays + inter-script configurable pauses
2. **Mid-broadcast insertion** — solved via pause/resume state machine; finishes current sentence before yielding to user reply

---

## 2. Architecture

```
broadcast_agent.py              # Entry point: HTTP server + broadcast lifecycle
├── ProductManager              # YAML config loading, script/video random selection
├── ScriptGenerator             # LLM-driven script generation for product URLs
├── BroadcastController         # Queue engine + video switching + speed/pause control
├── LlmClient (reused)          # Real-time user question replies
└── AvatarAgent (reused)        # WS communication + scene.switchVideo via send_custom_event
```

Existing `agent.py` remains unchanged.

### 2.1 Component Responsibilities

**ProductManager** — Loads `config/products.yaml` at startup. Provides methods:
- `get_products()` → list of all products
- `random_video_script(product_id)` → (video_resource_id, script_text) — picks a random video, then picks a random script from that video's script list
- `add_script(product_id, video_id, text)` / `update_script(product_id, video_id, index, text)`
- `reload()` → hot-reload config file
- Validates config at load: warns on missing scripts/videos, skips invalid products

**ScriptGenerator** — Calls LLM with product URL to generate scripts.
- Input: product URL + prompt template
- Output: list of 5-10 script segments (50-200 chars each in Chinese, suitable for oral broadcast)
- Persists generated scripts into products.yaml

**BroadcastController** — Core state machine.

```
IDLE → BROADCASTING → PAUSED(reply to user) → BROADCASTING → DONE
         ↑                  │                       │
         └─── resume ───────┘                       │
                                                    ↓
                                          Next product in queue
                                                    │
                                          Queue empty → IDLE (or loop)
```

Key behaviors:
- On broadcast start: pick a random video → then pick a random script from that video's script list
- Send `scene.switchVideo` custom event with the selected video as `onceVideos` + `loopVideos`
- Stream script text sentence-by-sentence via `send_response_chunk`
- Between sentences: micro-delay for natural pacing
- Between scripts: configurable pause (`pause_after_script_ms`), optionally switch to next video
- On user question: set `_paused` flag, finish current sentence, let LlmClient reply, resume
- On skip: cancel current broadcast, move to next product

### 2.2 Data Flow

```
   [products.yaml] ──load──→ ProductManager
                                  │
   [product URL] ──→ ScriptGenerator ──→ LLM ──→ scripts written to products.yaml
                                  │
   BroadcastController.start() ← product = ProductManager.next()
       │
       ├─ video, script = ProductManager.random_video_script(product.id)
       ├─ agent.send_custom_event("scene.switchVideo", {
       │      "onceVideos": [video],
       │      "loopVideos": [product.loop_video]
       │  })
       ├─ agent.send_response_start(speed=product.tts_speed)
       ├─ for sentence in script:
       │     if paused: await resume_event.wait()
       │     agent.send_response_chunk(...)
       │     await asyncio.sleep(chunk_delay)
       ├─ agent.send_response_done()
       └─ await asyncio.sleep(product.pause_after_script_ms)
       
   Viewer comment (mid-broadcast):
       POST /api/comment {text} → controller.pause()
                                → LlmClient.generate(text)
                                → stream reply chunks via TTS
                                → return reply in HTTP response
                                → controller.resume()
```

---

## 3. Configuration Format

```yaml
# config/products.yaml

settings:
  loop: true                    # queue finished → loop from start
  default_tts_speed: 1.0        # fallback speed
  default_pause_ms: 3000        # fallback inter-script pause
  chunk_delay_ms: 200           # micro-delay between sentences
  default_loop_video: "res_video_bg"  # shared background loop

products:
  - id: "prod_001"
    name: "XX气垫粉底"
    url: "https://www.tiktok.com/shop/pdp/1731199058452648921"
    loop_video: "res_video_bg"
    tts_speed: 1.0
    pause_after_script_ms: 3000
    video_scripts:
      - video: "res_video_prod001_a"       # 展示视频 resourceId (onceVideos)
        scripts:
          - "欢迎来到直播间！今天给大家带来的是XX气垫粉底..."
          - "这款气垫的遮瑕效果真的绝了，你们看这个对比..."
      - video: "res_video_prod001_b"
        scripts:
          - "现在下单立减50，只有100单库存，手慢无！"
          - "姐妹们这个价格真的非常划算，错过今天就没有了..."

  - id: "prod_002"
    name: "YY保湿面膜"
    # ...
```

Per-product overrides (`tts_speed`, `pause_after_script_ms`, `loop_video`) fall back to `settings` defaults.

Scripts can also reference external files per video entry:
```yaml
    video_scripts:
      - video: "res_video_prod001_a"
        scripts_file: "scripts/prod_001_video_a.txt"  # one script per line
```

---

## 4. HTTP API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/broadcast/start` | Start broadcast queue |
| `POST` | `/api/broadcast/stop` | Stop broadcast |
| `POST` | `/api/broadcast/pause` | Pause after current sentence |
| `POST` | `/api/broadcast/resume` | Resume broadcast |
| `POST` | `/api/broadcast/skip` | Skip current product |
| `GET` | `/api/broadcast/status` | Current state (product, segment, queue remaining) |
| `POST` | `/api/product/scripts` | Append/update scripts for a product |
| `POST` | `/api/product/generate` | Generate scripts from product URL |
| `POST` | `/api/product/reload` | Hot-reload config file |
| `POST` | `/api/comment` | Receive viewer comment from external system → trigger reply |

### 4.1 Request/Response Examples

**POST /api/broadcast/start** — no body needed (uses loaded config queue)
Response: `{"success": true, "queueLength": 3}`

**GET /api/broadcast/status**
Response:
```json
{
  "state": "broadcasting",
  "currentProduct": {"id": "prod_001", "name": "XX气垫粉底"},
  "currentVideo": "res_video_prod001_a",
  "currentScriptIndex": 2,
  "totalScriptsForVideo": 3,
  "queueRemaining": 2
}
```

**POST /api/product/generate**
```json
{"url": "https://www.tiktok.com/shop/pdp/1731199058452648921"}
```
Response: `{"success": true, "productId": "prod_001", "scriptsGenerated": 8}`

**POST /api/comment**
```json
{"text": "这个有运费险吗？", "userId": "user_123"}
```
Response: `{"success": true, "reply": "有的！全场包邮，七天无理由退换..."}`
- Agent 收到后：播完当前句子 → 暂停播报 → LLM 生成回复 → TTS 播出 → 恢复播报
- 回复内容同时通过 HTTP response 返回，方便外部系统同步展示

---

## 5. Error Handling

### 5.1 Broadcast Errors

| Scenario | Behavior |
|----------|----------|
| WS disconnect during broadcast | Auto-reconnect; resume from current product start |
| LLM reply timeout/failure | Return fallback text ("抱歉，请稍后再试"), resume broadcast |
| Video resourceId not found | Skip, try next video; log warning |
| `scene.switchVideo` rejected | Log error, continue broadcast with current video |

### 5.2 Config Errors

| Scenario | Behavior |
|----------|----------|
| YAML parse error | Fail on startup with clear message |
| Product has no `video_scripts` entries | Skip product, log warning |
| A video entry has no scripts | Skip that video entry; log warning |
| `video_scripts` has videos but all without scripts | Skip product, log warning |
| Script file not found | Log error, skip that video entry |

### 5.3 Queue Edge Cases

| Scenario | Behavior |
|----------|----------|
| Queue exhausted, `loop: true` | Restart from first product |
| Queue exhausted, `loop: false` | Transition to IDLE, keep WS connected |
| Single product in queue | Loop through its video_scripts entries, random script per video |
| `skip` called on last product | Move to first product (if loop) or IDLE |

---

## 6. File Structure

```
python-agent/
├── agent.py                     # Existing dialog agent (UNCHANGED)
├── broadcast_agent.py           # E-commerce broadcast entry point
├── product_manager.py           # Config loading / script management
├── script_generator.py          # LLM script generation
├── broadcast_controller.py      # Broadcast queue + speed + video switching
├── config/
│   └── products.yaml            # Product/script/video configuration
├── scripts/                     # Optional: external script files
├── requirements.txt             # Updated: liveavatar-channel-sdk>=0.2.4
└── docs/
    └── superpowers/specs/
        └── 2026-06-09-ecommerce-broadcast-design.md
```

---

## 7. Implementation Phases

| Phase | Scope | Verification |
|-------|-------|-------------|
| 1 | `ProductManager` + `products.yaml` loading | Unit tests for load/validate/random-select |
| 2 | `ScriptGenerator` LLM script generation | Manual test with real product URL |
| 3 | `BroadcastController` core engine | Unit tests with mocked AvatarAgent |
| 4 | `broadcast_agent.py` + HTTP API | Integration test with real WS |
| 5 | User interruption (pause/resume on question) | Integration test |
| 6 | (Optional) Frontend management page | Manual test |

---

## 8. Dependencies

- **Upgrade**: `liveavatar-channel-sdk` from 0.2.0 → 0.2.4 (for `scene.switchVideo` support)
- **Reuse**: `openai` (DeepSeek LLM), `aiohttp` (HTTP server)
- **New**: `pyyaml` (YAML config parsing)

---

## 9. Resolved Questions

1. **Video resourceId** — Platform-uploaded resource IDs. Config references these IDs directly.
2. **WS reconnect** — SDK v0.2.4 handles reconnection + state recovery automatically.
3. **Video-script binding** — One video maps to one or more scripts; each script belongs to exactly one video. Config uses `video_scripts` hierarchy to enforce this.
