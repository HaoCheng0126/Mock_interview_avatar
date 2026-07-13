# Quickstart — Talkshow Digital Human

Step-by-step guide to run the phase-one talk show digital human.

## Environment

```bash
export LIVEAVATAR_API_KEY="lk_live_xxx"
export LIVEAVATAR_AVATAR_ID="avatar_xxx"
export DEEPSEEK_API_KEY="sk-xxx"
export TALKSHOW_HTTP_PORT="8082"
```

Optional OpenAI-compatible overrides:

```bash
export DEEPSEEK_BASE_URL="https://api.deepseek.com"
export DEEPSEEK_MODEL="deepseek-v4-flash"
export TALKSHOW_CONFIG_PATH="config/talkshow.yaml"
```

## Run

```bash
python talkshow/agent.py
```

Open `http://localhost:8082` and click Connect. The avatar starts with
`show.opening` after the LiveAvatar scene reports ready, then continues with
generated segments and bridge lines.

## Config

Edit `config/talkshow.yaml` for:

- `persona`: name, style, and boundaries.
- `show`: title and opening.
- `voice`: Qwen TTS voice config passed to `/session/start`.
- `topics`: the topic pool used for new batches.
- `seed_batch`: cold-start content played after the opening. Each successful
  generation writes the latest batch back here for the next launch.
- `fallback_segments`: safe content used when generation fails.
- `settings.batch_size`: number of segments per generated batch.
- `settings.pause_after_opening_ms`, `settings.pause_after_segment_ms`,
  `settings.pause_after_bridge_ms`: pacing pauses that make the show feel less
  mechanically stitched together.

Generated batches are runtime content, except the latest successful batch is
persisted to `seed_batch` for cold start.

Example voice tuning:

```yaml
voice:
  voice_config:
    volume: 72
    speed: 1.08
    pitch: 1.03
    style: 0.65
```

For a stronger talk-show feel, generated `segment.text` uses line breaks as
performance beats. Each line is sent to TTS separately so setup, escalation, and
punchline do not collapse into one flat read.

## API

```bash
curl http://localhost:8082/api/talkshow/status
curl -X POST http://localhost:8082/api/talkshow/generate
curl -X POST http://localhost:8082/api/talkshow/reload
curl -X POST http://localhost:8082/api/talkshow/start
curl -X POST http://localhost:8082/api/talkshow/stop
curl -X POST http://localhost:8082/api/talkshow/pause
curl -X POST http://localhost:8082/api/talkshow/resume
curl -X POST http://localhost:8082/api/talkshow/skip
```
