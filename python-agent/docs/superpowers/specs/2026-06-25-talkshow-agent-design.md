# Talkshow Agent Design

Date: 2026-06-25

## Goal

Build a first version of a talk show digital human under `talkshow/`.
It should be simpler than the existing e-commerce broadcast agent: after a
LiveAvatar scene is connected and ready, the avatar continuously performs a
scripted-feeling stand-up show by generating new batches of jokes from a
configured topic pool.

Phase one does not support real audience interaction, simulated audience
questions, multiple personas, product selling, TikTok monitoring, or runtime
persona switching.

## Assumptions

- The existing `broadcast/` agent is a reference, not a shared runtime for this
  phase.
- `talkshow/` should be independent and should not modify `broadcast/`.
- The show uses one default persona configured in YAML.
- Topic selection, persona style, safety boundaries, cold-start seed content,
  and fallback content are controlled by configuration.
- Segment-to-segment transitions are required. A show that plays unrelated
  segments back-to-back is not acceptable.

## Recommended Approach

Use an independent `talkshow/` implementation that borrows only the proven
mechanisms from `broadcast/`:

- LiveAvatar session lifecycle and HTTP control surface.
- Auto-start when `scene.ready` is received.
- Wait for platform TTS idle before sending the next prompt.
- Generate the next batch before the current batch is exhausted.
- Keep control APIs for start, stop, pause, resume, skip, status, reload, and
  manual generation.

This avoids dragging e-commerce concepts into the talk show agent while keeping
the implementation close to a known working pattern.

## Core Modules

### `talkshow/agent.py`

Owns the HTTP server, process startup, LiveAvatar session lifecycle, global
component wiring, JSON responses, logging, and HTTP routes.

It creates components on server startup but creates the LiveAvatar session only
when `/api/start-session` is called. When `scene.ready` arrives, it starts the
controller if the controller is idle.

Default port: `TALKSHOW_HTTP_PORT=8082`.

### `talkshow/controller.py`

Owns the show state machine and playback loop.

States:

- `idle`
- `performing`
- `paused`

Responsibilities:

- Play the configured opening once at the beginning of a session. This is the
  default phase-one behavior; it is skipped only when `opening_enabled` is
  explicitly `false` or the opening text is empty.
- Maintain the current performance queue.
- Send one item at a time to LiveAvatar via `send_prompt`.
- Wait for TTS idle before advancing.
- Trigger background regeneration around the configured threshold.
- Support pause, resume, stop, and skip.
- Track current item, queue length, next batch readiness, and last error.

The controller should pause before sending the next item. It should not
interrupt current TTS playback for normal pause/resume. Skip may interrupt.

### `talkshow/script_generator.py`

Owns LLM prompt construction, JSON-mode generation, parsing, validation, and
retry behavior for show batches.

Each generated batch includes:

- A `batch_title`.
- A list of performance `segments`.
- A list of `bridges` connecting adjacent segments.

The generator receives:

- Persona name and style.
- Persona boundaries.
- Show title.
- Topic pool.
- Recently used segment titles or short summaries.
- Desired batch size.
- Language.

### `talkshow/show_manager.py`

Owns `config/talkshow.yaml` loading, parsed data models, fallback content, and
hot reload.

It should expose simple read APIs for settings, persona, show metadata, topics,
seed batch, and fallback segments. It should not know about LiveAvatar or HTTP.

### `frontend/talkshow.html`

Owns the minimal operator page for the talk show agent.

It should follow the existing static-page pattern used by `frontend/broadcast.html`:

- Load `sdk.js`.
- Call `/api/start-session` on Connect.
- Create the LiveAvatar SDK client with direct `sfuUrl` and `userToken`.
- Render the avatar in a main video container.
- Poll `/api/talkshow/status` while connected.
- Call `/api/talkshow/*` control endpoints from buttons.
- Call `/api/stop-session` on Disconnect.

The page should be independent from `broadcast.html`. It may copy the proven
connection pattern, but labels, API paths, and status rendering should use
talk show concepts.

## Configuration

Create `config/talkshow.yaml`.

Example:

```yaml
settings:
  loop: true
  lang: zh
  batch_size: 6
  regenerate_at_ratio: 0.75
  opening_enabled: true
  idle_timeout_s: 30
  pause_after_opening_ms: 800
  pause_after_segment_ms: 1200
  pause_after_bridge_ms: 500

persona:
  name: "阿麦"
  style: "观察生活，轻微自嘲，节奏快，不攻击观众。"
  boundaries:
    - "不讲政治"
    - "不讲低俗黄色内容"
    - "不嘲笑弱势群体"

show:
  title: "今晚不加班"
  opening: "大家好，欢迎来到今晚不加班。今天咱们不解决问题，只负责把问题讲得好笑一点。"

topics:
  - id: "workplace"
    title: "职场日常"
    description: "会议、加班、摸鱼、老板画饼。"
  - id: "city_life"
    title: "城市生活"
    description: "通勤、租房、外卖、健身房。"

fallback_segments:
  - topic_id: "workplace"
    title: "备用段子"
    text: "我一直觉得，会议不是为了解决问题，会议是为了确认这个问题确实存在，而且存在得很有仪式感。进去之前，大家都知道卡在哪；出来以后，大家多知道了一件事：下周二下午三点还要再确认一次。最神奇的是会议纪要，写得特别像破案报告，嫌疑人是需求，作案工具是排期，最后凶手永远是沟通成本。"

seed_batch:
  batch_title: "冷启动节目"
  segments:
    - topic_id: "workplace"
      title: "会议室里的时间黑洞"
      beats:
        - "从会议迟迟不开始切入。"
      text: "我最近发现，会议室有一种特殊的物理规则：只要门一关，时间就开始打折..."
  bridges: []
```

`seed_batch` is normal cold-start content, not an error fallback. The controller
plays it immediately after the opening so startup does not wait for the first
LLM batch. Whenever a new batch is generated successfully, it is written back to
`seed_batch`; the next process start therefore begins with the previous
session's last generated batch.

## Generated Batch Shape

The LLM should return a JSON object:

```json
{
  "batch_title": "职场玄学观察",
  "segments": [
    {
      "topic_id": "workplace",
      "title": "会议室里的时间黑洞",
      "beats": [
        "先从会议迟迟不开始的日常观察切入。",
        "递进到大家用不同黑话重复同一个观点。",
        "最后用会议纪要像破案报告做收束包袱。"
      ],
      "text": "我最近发现，会议室有一种特殊的物理规则：只要门一关，时间就开始打折。外面十分钟，里面能过出一部连续剧。最开始大家都很积极，说我们今天高效一点，半小时结束。结果第一个人打开 PPT，说我简单过一下，大家就知道完了，简单这个词在职场里，基本等于先坐稳。更神奇的是，每个人都在表达同一个意思，只是包装不一样。有人说要拉齐认知，有人说要对焦目标，有人说要形成闭环。翻译成人话就是：这事儿谁来干？最后会议纪要发出来，特别像破案报告。嫌疑人是需求，作案工具是排期，受害者是所有人的下午。"
    },
    {
      "topic_id": "city_life",
      "title": "地铁里的社交礼仪",
      "beats": [
        "从早高峰拥挤场景进入。",
        "描写乘客如何在没有空间时保持体面。",
        "用现代人把自己活成手机支架做结尾。"
      ],
      "text": "早高峰地铁是城市里最公平的地方，不管你昨天是老板、总监，还是刚改完第十二版方案的人，进去以后大家统一变成压缩文件。最讲礼貌的不是让座的人，是那个明明被挤到灵魂出窍，还努力把胳膊收回来的乘客。他不是情绪稳定，他只是没有地方崩溃。地铁里还有一种默契，谁都不看谁，但谁都知道谁踩了谁。你低头看手机，不是因为有消息，是因为眼神一旦对上，就很难解释为什么你的包正在参与别人的人生。最后你会发现，现代人的通勤能力真的很强，强到能把自己活成一个竖着的手机支架。"
    }
  ],
  "bridges": [
    {
      "from_title": "会议室里的时间黑洞",
      "to_title": "地铁里的社交礼仪",
      "text": "说到时间被偷走，办公室偷你半小时，地铁能偷你一整个人生。"
    }
  ]
}
```

Validation rules:

- `segments` must contain at least one usable item.
- Each segment needs `topic_id`, `title`, optional `beats`, and non-empty
  `text`.
- Segment text should be a complete performable mini-routine, not a single
  punchline. It should include setup, escalation, at least one clear laugh point,
  and a closing turn.
- Segment text should be roughly 180-350 Chinese characters by default,
  suitable for about 45-90 seconds of speech depending on TTS speed.
- `beats` should summarize the segment's internal structure so future prompts can
  avoid repeating the same angle.
- `bridges` should connect adjacent segments when more than one segment exists.
- Bridge text should be a short transition routine, roughly 40-120 Chinese
  characters.
- Each bridge should include a `callback` move that refers back to the previous
  segment's last emotion or image, and a `pivot` move that naturally opens the
  next segment.
- If bridges are missing or invalid, the controller may use a configured
  fallback bridge, but generated bridges are preferred.

## Bridge Design

Bridges are first-class show content, not optional filler.

Playback rhythm:

```text
opening -> pause -> segment[0] -> beat_pause -> bridge[0] -> breath_pause -> segment[1]
```

Bridge requirements:

- Connect the previous segment and the next segment.
- Avoid template language such as "接下来我们讲下一个话题".
- Use callbacks, comparisons, escalation, self-deprecation, or topic pivots.
- If both segments share a topic, bridge by deepening the same topic.
- If segments use different topics, bridge by finding a shared feeling or image.
- Pause after each segment so the closing laugh point has room to land.
- Pause briefly after each bridge so the next segment does not feel glued on.

Cross-batch transitions also need a bridge. When the next batch is ready, the
controller should connect the last segment of the current batch to the first
segment of the next batch. If the next batch is not ready, it may play a short
persona-consistent waiting line and continue waiting.

## Runtime Flow

1. User runs `python talkshow/agent.py`.
2. Server initializes the LLM client, show manager, script generator, and HTTP
   routes, and serves `frontend/talkshow.html` at `/`.
3. The operator opens the talk show page and clicks Connect.
4. Frontend calls `/api/start-session`.
5. The server creates a fresh LiveAvatar session and returns `userToken`,
   `sfuUrl`, and `sessionId`.
6. The frontend connects the LiveAvatar SDK and starts polling talk show status.
7. When `scene.ready` is received, the controller starts performing.
8. The controller sends the configured opening once at the start of the show.
   This happens by default and is skipped only when `opening_enabled: false` or
   no opening text is configured.
9. The controller uses `seed_batch` as the first batch when configured. If no
   seed batch exists, it generates the first batch.
10. The controller expands the batch into a performance queue of segments and
   bridges.
11. Each queue item is sent after the platform reports TTS idle.
12. When playback reaches the regeneration threshold, the controller starts
    background generation for the next batch.
13. When the current batch finishes, the controller uses the next batch if ready,
    otherwise uses fallback waiting content while generation completes.
14. Stop returns the controller to `idle`. Pause/resume affect the next item.

## Frontend Page

Create `frontend/talkshow.html` and have `talkshow/agent.py` serve it as the
default page.

The page is an operator console, not a marketing page. It should prioritize
connection, current performance state, and quick control.

Layout:

- Top bar: page title, SDK connection status, show state badge.
- Main area: large avatar container on the left.
- Right panel: current show metadata and performance queue.
- Bottom/right controls: Connect, Disconnect, Start, Stop, Pause, Resume, Skip,
  Generate, Reload.

Right panel content:

- Show title and persona name.
- Current item type: `opening`, `segment`, `bridge`, or `waiting`.
- Current item title.
- Current item text preview.
- Queue remaining count.
- Next batch readiness.
- Last error, when present.

Queue display:

- Render recent and upcoming items as compact rows.
- Use distinct labels for `segment` and `bridge` so the operator can see that
  transitions are part of the show.
- The current item should be visually highlighted.
- The page does not need editing controls in phase one.

Button behavior:

- Connect calls `/api/start-session`, initializes the SDK, and starts polling.
- Disconnect calls SDK disconnect and `/api/stop-session`.
- Start, Stop, Pause, Resume, and Skip call `/api/talkshow/{action}`.
- Generate calls `/api/talkshow/generate` to create a fresh next batch manually.
- Reload calls `/api/talkshow/reload` after manual config edits.

Frontend state should be derived from `/api/talkshow/status`; it should not
try to infer show progress locally beyond button enabled/disabled states.

## HTTP API

Keep the public surface small and close to `broadcast` naming patterns.

- `POST /api/start-session`
- `POST /api/stop-session`
- `POST /api/talkshow/start`
- `POST /api/talkshow/stop`
- `POST /api/talkshow/pause`
- `POST /api/talkshow/resume`
- `POST /api/talkshow/skip`
- `GET /api/talkshow/status`
- `POST /api/talkshow/generate`
- `POST /api/talkshow/reload`

Status response:

```json
{
  "state": "performing",
  "currentItem": {
    "type": "bridge",
    "title": "会议室里的时间黑洞 -> 地铁里的社交礼仪"
  },
  "queueRemaining": 4,
  "nextBatchReady": true,
  "lastError": null
}
```

## Error Handling

Generation failure:

1. Retry once.
2. Use configured `fallback_segments` if present.
3. If no fallback is configured, keep the service alive, set `lastError`, and
   avoid tight retry loops.

TTS idle timeout:

- Use the configured timeout, defaulting to 30 seconds.
- Log the timeout and continue to the next item.

Invalid config:

- Missing optional values should use defaults.
- Missing topics or missing fallback content should be visible in startup logs
  and status responses.
- Required LiveAvatar credentials still come from environment variables.

## Tests

Focused tests should cover:

- YAML loading and defaults in `show_manager`.
- Generated batch parsing and validation in `script_generator`.
- Queue expansion into `segment -> bridge -> segment`.
- Regeneration threshold behavior.
- Pause/resume state transitions.
- Skip interrupt behavior using a fake agent.
- Status response shape.
- Fallback behavior when generation fails.

No end-to-end LiveAvatar test is required for phase one.

## Out Of Scope

- Real viewer comments.
- TikTok or other live platform monitoring.
- Simulated audience Q&A.
- Multiple personas.
- Runtime persona switching.
- Editing UI for topics or scripts.
- Refactoring `broadcast` into a shared runtime.
