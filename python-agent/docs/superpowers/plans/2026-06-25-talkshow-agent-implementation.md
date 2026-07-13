# Talkshow Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a phase-one talk show digital human agent that continuously performs generated stand-up segments with natural bridges.

**Architecture:** Add an independent `talkshow/` package that mirrors only the useful runtime patterns from `broadcast/`: LiveAvatar session lifecycle, `scene.ready` auto-start, TTS idle pacing, HTTP controls, and background generation. Keep generated show content in memory and keep long-lived show settings in `config/talkshow.yaml`. Add a standalone `frontend/talkshow.html` operator console served by `talkshow/agent.py`.

**Tech Stack:** Python 3.11+, aiohttp, pytest, pytest-asyncio, PyYAML, OpenAI-compatible async client via existing `llm_client.py`, LiveAvatar Python SDK, static HTML/CSS/JS with existing `frontend/sdk.js`.

## Global Constraints

- Do not modify `broadcast/`.
- Do not add real viewer comments, TikTok monitoring, simulated audience Q&A, multiple personas, or runtime persona switching.
- `show.opening` plays once at the start by default; skip only when `opening_enabled: false` or opening text is empty.
- `config/talkshow.yaml` does not contain `default_loop_video`.
- Generated batches are runtime content and are not written back to YAML.
- Each segment is a complete mini-routine, roughly 180-350 Chinese characters by default.
- Bridges are first-class playback items and remain short, roughly 10-40 Chinese characters.
- The frontend page is an operator console, not a marketing page.
- This workspace currently does not appear to be inside a git repository, so execution should skip commit commands unless run from a valid repository root.

---

## File Structure

- Create `talkshow/__init__.py`: marks the package.
- Create `talkshow/show_manager.py`: YAML loading, dataclasses, defaults, reload, fallback batch helpers.
- Create `talkshow/script_generator.py`: prompt construction, LLM JSON call, parsing, validation, fallback conversion.
- Create `talkshow/controller.py`: show state machine, playback queue, TTS idle pacing, regeneration.
- Create `talkshow/agent.py`: aiohttp server, LiveAvatar session lifecycle, routes, static frontend serving.
- Create `config/talkshow.yaml`: default show configuration.
- Create `frontend/talkshow.html`: operator console.
- Create `talkshow/QUICKSTART.md`: run instructions and API cheat sheet.
- Create `tests/talkshow/__init__.py`.
- Create `tests/talkshow/test_show_manager.py`.
- Create `tests/talkshow/test_script_generator.py`.
- Create `tests/talkshow/test_controller.py`.

---

### Task 1: Show Configuration Loader

**Files:**
- Create: `talkshow/__init__.py`
- Create: `talkshow/show_manager.py`
- Create: `config/talkshow.yaml`
- Test: `tests/talkshow/__init__.py`
- Test: `tests/talkshow/test_show_manager.py`

**Interfaces:**
- Produces: `ShowManager(config_path: Path | str)`
- Produces: `ShowManager.settings: dict`
- Produces: `ShowManager.persona: Persona`
- Produces: `ShowManager.show: Show`
- Produces: `ShowManager.get_topics() -> list[Topic]`
- Produces: `ShowManager.get_fallback_segments() -> list[Segment]`
- Produces: `ShowManager.reload() -> None`
- Produces dataclasses: `Persona`, `Show`, `Topic`, `Segment`, `Bridge`, `ShowBatch`, `PlaybackItem`

- [ ] **Step 1: Write failing tests for YAML parsing and defaults**

Create `tests/talkshow/test_show_manager.py`:

```python
from pathlib import Path

from talkshow.show_manager import ShowManager


SAMPLE_YAML = """
settings:
  loop: true
  lang: zh
  batch_size: 6
  regenerate_at_ratio: 0.75
  opening_enabled: true
  idle_timeout_s: 30

persona:
  name: "阿麦"
  style: "观察生活，轻微自嘲，节奏快，不攻击观众。"
  boundaries:
    - "不讲政治"
    - "不讲低俗黄色内容"

show:
  title: "今晚不加班"
  opening: "大家好，欢迎来到今晚不加班。"

topics:
  - id: "workplace"
    title: "职场日常"
    description: "会议、加班、摸鱼、老板画饼。"

fallback_segments:
  - topic_id: "workplace"
    title: "备用段子"
    text: "我一直觉得，会议不是为了解决问题，会议是为了确认这个问题确实存在。"
"""


def test_loads_talkshow_config(tmp_path: Path):
    config_path = tmp_path / "talkshow.yaml"
    config_path.write_text(SAMPLE_YAML, encoding="utf-8")

    manager = ShowManager(config_path)

    assert manager.settings["batch_size"] == 6
    assert manager.settings["opening_enabled"] is True
    assert manager.persona.name == "阿麦"
    assert manager.persona.boundaries == ["不讲政治", "不讲低俗黄色内容"]
    assert manager.show.title == "今晚不加班"
    assert manager.show.opening == "大家好，欢迎来到今晚不加班。"
    assert manager.get_topics()[0].id == "workplace"
    assert manager.get_fallback_segments()[0].title == "备用段子"


def test_defaults_do_not_require_video(tmp_path: Path):
    config_path = tmp_path / "talkshow.yaml"
    config_path.write_text(
        '''
persona:
  name: "阿麦"
show:
  title: "今晚不加班"
topics:
  - id: "workplace"
    title: "职场日常"
''',
        encoding="utf-8",
    )

    manager = ShowManager(config_path)

    assert manager.settings["loop"] is True
    assert manager.settings["lang"] == "zh"
    assert manager.settings["batch_size"] == 6
    assert manager.settings["regenerate_at_ratio"] == 0.75
    assert manager.settings["opening_enabled"] is True
    assert "default_loop_video" not in manager.settings


def test_reload_updates_topics(tmp_path: Path):
    config_path = tmp_path / "talkshow.yaml"
    config_path.write_text(SAMPLE_YAML, encoding="utf-8")
    manager = ShowManager(config_path)
    assert len(manager.get_topics()) == 1

    config_path.write_text(
        SAMPLE_YAML
        + '''
  - id: "city_life"
    title: "城市生活"
    description: "通勤、租房、外卖。"
''',
        encoding="utf-8",
    )

    manager.reload()
    assert [topic.id for topic in manager.get_topics()] == ["workplace", "city_life"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/talkshow/test_show_manager.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'talkshow.show_manager'`.

- [ ] **Step 3: Implement `talkshow/show_manager.py`**

Create `talkshow/__init__.py` as an empty package file.

Implement:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


@dataclass
class Persona:
    name: str
    style: str = ""
    boundaries: list[str] = field(default_factory=list)


@dataclass
class Show:
    title: str
    opening: str = ""


@dataclass
class Topic:
    id: str
    title: str
    description: str = ""


@dataclass
class Segment:
    topic_id: str
    title: str
    text: str
    beats: list[str] = field(default_factory=list)


@dataclass
class Bridge:
    from_title: str
    to_title: str
    text: str


@dataclass
class ShowBatch:
    batch_title: str
    segments: list[Segment]
    bridges: list[Bridge] = field(default_factory=list)


@dataclass
class PlaybackItem:
    type: Literal["opening", "segment", "bridge", "waiting"]
    title: str
    text: str
    topic_id: str = ""


DEFAULT_SETTINGS = {
    "loop": True,
    "lang": "zh",
    "batch_size": 6,
    "regenerate_at_ratio": 0.75,
    "opening_enabled": True,
    "idle_timeout_s": 30,
}


class ShowManager:
    def __init__(self, config_path: Path | str) -> None:
        self._config_path = Path(config_path)
        self.settings: dict = {}
        self.persona = Persona(name="Talkshow Host")
        self.show = Show(title="Talkshow")
        self._topics: list[Topic] = []
        self._fallback_segments: list[Segment] = []
        self.reload()

    def reload(self) -> None:
        data = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
        self.settings = {**DEFAULT_SETTINGS, **(data.get("settings") or {})}
        self.settings.pop("default_loop_video", None)

        persona_raw = data.get("persona") or {}
        self.persona = Persona(
            name=str(persona_raw.get("name") or "Talkshow Host"),
            style=str(persona_raw.get("style") or ""),
            boundaries=[str(item) for item in persona_raw.get("boundaries") or []],
        )

        show_raw = data.get("show") or {}
        self.show = Show(
            title=str(show_raw.get("title") or "Talkshow"),
            opening=str(show_raw.get("opening") or ""),
        )

        self._topics = [
            Topic(
                id=str(item.get("id") or ""),
                title=str(item.get("title") or item.get("id") or ""),
                description=str(item.get("description") or ""),
            )
            for item in data.get("topics") or []
            if item.get("id")
        ]

        self._fallback_segments = [
            Segment(
                topic_id=str(item.get("topic_id") or ""),
                title=str(item.get("title") or "Fallback"),
                text=str(item.get("text") or ""),
                beats=[str(beat) for beat in item.get("beats") or []],
            )
            for item in data.get("fallback_segments") or []
            if str(item.get("text") or "").strip()
        ]

    def get_topics(self) -> list[Topic]:
        return list(self._topics)

    def get_fallback_segments(self) -> list[Segment]:
        return list(self._fallback_segments)
```

Create `config/talkshow.yaml` with the YAML from the design spec, without `default_loop_video`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/talkshow/test_show_manager.py -v`

Expected: PASS, 3 tests.

---

### Task 2: Batch Generation and Validation

**Files:**
- Create: `talkshow/script_generator.py`
- Test: `tests/talkshow/test_script_generator.py`

**Interfaces:**
- Consumes: `Persona`, `Topic`, `Segment`, `Bridge`, `ShowBatch`
- Produces: `TalkshowScriptGenerator(llm_client)`
- Produces: `await TalkshowScriptGenerator.generate_batch(persona: Persona, show: Show, topics: list[Topic], recent_segments: list[Segment], batch_size: int, lang: str) -> ShowBatch`
- Produces: `TalkshowScriptGenerator.parse_batch(raw: str) -> ShowBatch`
- Produces: `TalkshowScriptGenerator.build_fallback_batch(segments: list[Segment]) -> ShowBatch`

- [ ] **Step 1: Write failing tests for parse, validation, and fallback**

Create `tests/talkshow/test_script_generator.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from talkshow.script_generator import TalkshowScriptGenerator
from talkshow.show_manager import Persona, Segment, Show, Topic


def _make_llm_response(content: str, finish_reason: str = "stop"):
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = finish_reason
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_mock_llm(content: str):
    llm = MagicMock()
    llm._model = "deepseek-v4-flash"
    llm._client.chat.completions.create = AsyncMock(
        return_value=_make_llm_response(content)
    )
    return llm


def test_parse_batch_accepts_segments_and_bridges():
    raw = '''
    {
      "batch_title": "职场玄学观察",
      "segments": [
        {
          "topic_id": "workplace",
          "title": "会议室里的时间黑洞",
          "beats": ["开场观察", "黑话递进", "纪要包袱"],
          "text": "会议室有一种特殊的物理规则，只要门一关，时间就开始打折。"
        },
        {
          "topic_id": "city_life",
          "title": "地铁里的社交礼仪",
          "text": "早高峰地铁是城市里最公平的地方，进去以后大家统一变成压缩文件。"
        }
      ],
      "bridges": [
        {
          "from_title": "会议室里的时间黑洞",
          "to_title": "地铁里的社交礼仪",
          "text": "说到时间被偷走，地铁也不甘示弱。"
        }
      ]
    }
    '''

    batch = TalkshowScriptGenerator.parse_batch(raw)

    assert batch.batch_title == "职场玄学观察"
    assert len(batch.segments) == 2
    assert batch.segments[0].beats == ["开场观察", "黑话递进", "纪要包袱"]
    assert batch.bridges[0].text == "说到时间被偷走，地铁也不甘示弱。"


def test_parse_batch_strips_markdown_fence():
    raw = '''```json
    {"batch_title":"一批","segments":[{"topic_id":"workplace","title":"标题","text":"正文内容"}],"bridges":[]}
    ```'''

    batch = TalkshowScriptGenerator.parse_batch(raw)

    assert batch.batch_title == "一批"
    assert batch.segments[0].title == "标题"


def test_parse_batch_rejects_empty_segments():
    with pytest.raises(ValueError, match="segments"):
        TalkshowScriptGenerator.parse_batch('{"batch_title":"空","segments":[]}')


def test_build_fallback_batch_uses_config_segments():
    fallback = [
        Segment(topic_id="workplace", title="备用", text="备用段子正文"),
    ]

    batch = TalkshowScriptGenerator.build_fallback_batch(fallback)

    assert batch.batch_title == "fallback"
    assert batch.segments == fallback
    assert batch.bridges == []


@pytest.mark.asyncio
async def test_generate_batch_calls_llm_with_json_mode():
    llm = _make_mock_llm(
        '{"batch_title":"一批","segments":[{"topic_id":"workplace","title":"标题","text":"正文内容"}],"bridges":[]}'
    )
    generator = TalkshowScriptGenerator(llm)

    batch = await generator.generate_batch(
        persona=Persona(name="阿麦", style="轻微自嘲"),
        show=Show(title="今晚不加班", opening="开场"),
        topics=[Topic(id="workplace", title="职场日常", description="会议")],
        recent_segments=[],
        batch_size=1,
        lang="zh",
    )

    assert batch.segments[0].title == "标题"
    kwargs = llm._client.chat.completions.create.call_args.kwargs
    assert kwargs["response_format"] == {"type": "json_object"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/talkshow/test_script_generator.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'talkshow.script_generator'`.

- [ ] **Step 3: Implement `talkshow/script_generator.py`**

Implement JSON parsing, prompt building, and one retry:

```python
from __future__ import annotations

import asyncio
import json
import logging

from talkshow.show_manager import Bridge, Persona, Segment, Show, ShowBatch, Topic

logger = logging.getLogger(__name__)


class TalkshowScriptGenerator:
    def __init__(self, llm_client) -> None:
        self._llm = llm_client

    async def generate_batch(
        self,
        *,
        persona: Persona,
        show: Show,
        topics: list[Topic],
        recent_segments: list[Segment],
        batch_size: int,
        lang: str,
    ) -> ShowBatch:
        prompt = self._build_prompt(
            persona=persona,
            show=show,
            topics=topics,
            recent_segments=recent_segments,
            batch_size=batch_size,
            lang=lang,
        )
        for attempt in range(2):
            try:
                response = await self._llm._client.chat.completions.create(
                    model=self._llm._model,
                    messages=[
                        {
                            "role": "system",
                            "content": "你是一个专业脱口秀编剧。只输出JSON对象，不要解释。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=4096,
                    temperature=0.8,
                    response_format={"type": "json_object"},
                )
                raw = response.choices[0].message.content or ""
                return self.parse_batch(raw)
            except Exception:
                if attempt == 0:
                    await asyncio.sleep(1)
                    continue
                raise
        raise RuntimeError("unreachable")

    @staticmethod
    def parse_batch(raw: str) -> ShowBatch:
        clean = raw.strip()
        if clean.startswith("```"):
            lines = clean.splitlines()
            clean = "\n".join(lines[1:])
        if clean.endswith("```"):
            clean = clean[:-3].strip()
        data = json.loads(clean)
        segments = [
            Segment(
                topic_id=str(item.get("topic_id") or ""),
                title=str(item.get("title") or ""),
                text=str(item.get("text") or "").strip(),
                beats=[str(beat) for beat in item.get("beats") or []],
            )
            for item in data.get("segments") or []
            if str(item.get("text") or "").strip()
        ]
        if not segments:
            raise ValueError("Generated batch must include non-empty segments")
        bridges = [
            Bridge(
                from_title=str(item.get("from_title") or ""),
                to_title=str(item.get("to_title") or ""),
                text=str(item.get("text") or "").strip(),
            )
            for item in data.get("bridges") or []
            if str(item.get("text") or "").strip()
        ]
        return ShowBatch(
            batch_title=str(data.get("batch_title") or "untitled"),
            segments=segments,
            bridges=bridges,
        )

    @staticmethod
    def build_fallback_batch(segments: list[Segment]) -> ShowBatch:
        usable = [segment for segment in segments if segment.text.strip()]
        if not usable:
            raise ValueError("fallback_segments is empty")
        return ShowBatch(batch_title="fallback", segments=usable, bridges=[])

    def _build_prompt(
        self,
        *,
        persona: Persona,
        show: Show,
        topics: list[Topic],
        recent_segments: list[Segment],
        batch_size: int,
        lang: str,
    ) -> str:
        topic_lines = "\n".join(
            f"- {topic.id}: {topic.title}。{topic.description}" for topic in topics
        )
        boundary_lines = "\n".join(f"- {item}" for item in persona.boundaries)
        recent_lines = "\n".join(
            f"- {segment.title}: {' / '.join(segment.beats)}"
            for segment in recent_segments[-8:]
        )
        return f"""语言: {lang}
节目: {show.title}
演员: {persona.name}
风格: {persona.style}
禁区:
{boundary_lines or "- 无"}

主题池:
{topic_lines or "- workplace: 职场日常"}

最近讲过，避免重复:
{recent_lines or "- 无"}

生成恰好 {batch_size} 个脱口秀 segment，并生成相邻 segment 之间的 bridge。
每个 segment 是完整可表演小段子，约180-350个中文字符，包含铺垫、递进、至少一个清晰笑点和收束。
每个 bridge 10-40个中文字符，必须自然连接前后两个标题，不能写“接下来我们讲下一个话题”。

只返回JSON对象，字段和这个示例一致:
{{"batch_title":"职场玄学观察","segments":[{{"topic_id":"workplace","title":"会议室里的时间黑洞","beats":["迟迟不开始的会议观察","黑话重复同一个观点","会议纪要像破案报告"],"text":"会议室有一种特殊的物理规则，只要门一关，时间就开始打折。"}}],"bridges":[{{"from_title":"会议室里的时间黑洞","to_title":"地铁里的社交礼仪","text":"说到时间被偷走，地铁也不甘示弱。"}}]}}
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/talkshow/test_script_generator.py -v`

Expected: PASS, 5 tests.

---

### Task 3: Show Controller

**Files:**
- Create: `talkshow/controller.py`
- Test: `tests/talkshow/test_controller.py`

**Interfaces:**
- Consumes: `ShowManager`, `TalkshowScriptGenerator`, `ShowBatch`, `PlaybackItem`
- Produces: `TalkshowController(agent, show_manager, script_generator)`
- Produces: `TalkshowState.IDLE`, `TalkshowState.PERFORMING`, `TalkshowState.PAUSED`
- Produces: `await start()`, `await stop()`, `pause()`, `resume()`, `skip()`
- Produces: `notify_platform_idle() -> None`
- Produces: `get_status() -> dict`
- Produces: `await generate_next_batch() -> ShowBatch`

- [ ] **Step 1: Write failing tests for queue expansion, opening, and state**

Create `tests/talkshow/test_controller.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from talkshow.controller import TalkshowController, TalkshowState
from talkshow.show_manager import Bridge, Persona, Segment, Show, ShowBatch


@pytest.fixture
def mock_agent():
    agent = AsyncMock()
    agent.send_prompt = AsyncMock()
    agent.send_interrupt = AsyncMock()
    return agent


@pytest.fixture
def show_manager():
    manager = MagicMock()
    manager.settings = {
        "loop": True,
        "lang": "zh",
        "batch_size": 2,
        "regenerate_at_ratio": 0.75,
        "opening_enabled": True,
        "idle_timeout_s": 0.05,
    }
    manager.persona = Persona(name="阿麦")
    manager.show = Show(title="今晚不加班", opening="开场白")
    manager.get_topics.return_value = []
    manager.get_fallback_segments.return_value = [
        Segment(topic_id="workplace", title="备用", text="备用正文")
    ]
    return manager


@pytest.fixture
def script_generator():
    generator = AsyncMock()
    generator.generate_batch = AsyncMock(
        return_value=ShowBatch(
            batch_title="一批",
            segments=[
                Segment(topic_id="workplace", title="段一", text="段一正文"),
                Segment(topic_id="city", title="段二", text="段二正文"),
            ],
            bridges=[
                Bridge(from_title="段一", to_title="段二", text="桥一正文"),
            ],
        )
    )
    return generator


def test_expand_batch_inserts_bridge(mock_agent, show_manager, script_generator):
    controller = TalkshowController(mock_agent, show_manager, script_generator)
    batch = ShowBatch(
        batch_title="一批",
        segments=[
            Segment(topic_id="workplace", title="段一", text="段一正文"),
            Segment(topic_id="city", title="段二", text="段二正文"),
        ],
        bridges=[Bridge(from_title="段一", to_title="段二", text="桥一正文")],
    )

    items = controller.expand_batch(batch)

    assert [item.type for item in items] == ["segment", "bridge", "segment"]
    assert items[1].title == "段一 -> 段二"
    assert items[1].text == "桥一正文"


@pytest.mark.asyncio
async def test_start_sends_opening_first(mock_agent, show_manager, script_generator):
    controller = TalkshowController(mock_agent, show_manager, script_generator)

    task = asyncio.create_task(controller.start())
    await asyncio.sleep(0.12)
    await controller.stop()
    await task

    assert mock_agent.send_prompt.await_args_list[0].args[0] == "开场白"


@pytest.mark.asyncio
async def test_pause_resume_and_status(mock_agent, show_manager, script_generator):
    controller = TalkshowController(mock_agent, show_manager, script_generator)
    assert controller.state == TalkshowState.IDLE

    task = asyncio.create_task(controller.start())
    await asyncio.sleep(0.02)
    controller.pause()
    assert controller.state == TalkshowState.PAUSED

    status = controller.get_status()
    assert status["state"] == "paused"

    controller.resume()
    assert controller.state == TalkshowState.PERFORMING
    await controller.stop()
    await task
    assert controller.state == TalkshowState.IDLE


@pytest.mark.asyncio
async def test_skip_interrupts_current_item(mock_agent, show_manager, script_generator):
    controller = TalkshowController(mock_agent, show_manager, script_generator)

    task = asyncio.create_task(controller.start())
    await asyncio.sleep(0.02)
    controller.skip()
    await asyncio.sleep(0.02)
    await controller.stop()
    await task

    mock_agent.send_interrupt.assert_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/talkshow/test_controller.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'talkshow.controller'`.

- [ ] **Step 3: Implement `talkshow/controller.py`**

Implement:

```python
from __future__ import annotations

import asyncio
import logging
from enum import Enum

from talkshow.show_manager import Bridge, PlaybackItem, Segment, ShowBatch

logger = logging.getLogger(__name__)


class TalkshowState(str, Enum):
    IDLE = "idle"
    PERFORMING = "performing"
    PAUSED = "paused"


class TalkshowController:
    def __init__(self, agent, show_manager, script_generator) -> None:
        self._agent = agent
        self._show_manager = show_manager
        self._script_generator = script_generator
        self._state = TalkshowState.IDLE
        self._task: asyncio.Task | None = None
        self._paused_event = asyncio.Event()
        self._paused_event.set()
        self._tts_idle = asyncio.Event()
        self._tts_idle.set()
        self._stopped = False
        self._queue: list[PlaybackItem] = []
        self._next_batch: ShowBatch | None = None
        self._recent_segments: list[Segment] = []
        self._current_item: PlaybackItem | None = None
        self._last_error: str | None = None
        self._generation_task: asyncio.Task | None = None

    @property
    def state(self) -> TalkshowState:
        return self._state

    async def start(self) -> None:
        if self._state != TalkshowState.IDLE:
            return
        self._stopped = False
        self._state = TalkshowState.PERFORMING
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopped = True
        self._paused_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._state = TalkshowState.IDLE

    def pause(self) -> None:
        if self._state == TalkshowState.PERFORMING:
            self._state = TalkshowState.PAUSED
            self._paused_event.clear()

    def resume(self) -> None:
        if self._state == TalkshowState.PAUSED:
            self._state = TalkshowState.PERFORMING
            self._paused_event.set()

    def skip(self) -> None:
        asyncio.create_task(self._agent.send_interrupt())
        self._tts_idle.set()
        self._paused_event.set()

    def notify_platform_idle(self) -> None:
        self._tts_idle.set()

    def get_status(self) -> dict:
        return {
            "state": self._state.value,
            "currentItem": (
                {
                    "type": self._current_item.type,
                    "title": self._current_item.title,
                    "text": self._current_item.text,
                }
                if self._current_item
                else None
            ),
            "queueRemaining": len(self._queue),
            "nextBatchReady": self._next_batch is not None,
            "lastError": self._last_error,
        }

    def expand_batch(self, batch: ShowBatch) -> list[PlaybackItem]:
        items: list[PlaybackItem] = []
        for index, segment in enumerate(batch.segments):
            items.append(
                PlaybackItem(
                    type="segment",
                    title=segment.title,
                    text=segment.text,
                    topic_id=segment.topic_id,
                )
            )
            if index < len(batch.segments) - 1:
                bridge = self._bridge_for(batch.bridges, segment, batch.segments[index + 1])
                items.append(
                    PlaybackItem(
                        type="bridge",
                        title=f"{segment.title} -> {batch.segments[index + 1].title}",
                        text=bridge.text,
                    )
                )
        return items

    async def generate_next_batch(self) -> ShowBatch:
        try:
            batch = await self._script_generator.generate_batch(
                persona=self._show_manager.persona,
                show=self._show_manager.show,
                topics=self._show_manager.get_topics(),
                recent_segments=self._recent_segments,
                batch_size=int(self._show_manager.settings.get("batch_size", 6)),
                lang=str(self._show_manager.settings.get("lang", "zh")),
            )
            self._last_error = None
            return batch
        except Exception as exc:
            self._last_error = str(exc)
            fallback = self._show_manager.get_fallback_segments()
            return self._script_generator.build_fallback_batch(fallback)

    async def _run(self) -> None:
        try:
            if self._should_play_opening():
                await self._play_item(
                    PlaybackItem(
                        type="opening",
                        title=self._show_manager.show.title,
                        text=self._show_manager.show.opening,
                    )
                )
            while not self._stopped:
                if not self._queue:
                    batch = self._next_batch or await self.generate_next_batch()
                    self._next_batch = None
                    self._queue.extend(self.expand_batch(batch))
                    self._recent_segments.extend(batch.segments)
                    self._recent_segments = self._recent_segments[-12:]
                await self._paused_event.wait()
                item = self._queue.pop(0)
                await self._play_item(item)
                self._maybe_start_background_generation()
        except asyncio.CancelledError:
            raise
        finally:
            self._state = TalkshowState.IDLE if self._stopped else self._state

    async def _play_item(self, item: PlaybackItem) -> None:
        await self._paused_event.wait()
        timeout = float(self._show_manager.settings.get("idle_timeout_s", 30))
        try:
            await asyncio.wait_for(self._tts_idle.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("TTS idle timeout before %s", item.title)
        self._tts_idle.clear()
        self._current_item = item
        await self._agent.send_prompt(item.text)

    def _maybe_start_background_generation(self) -> None:
        if self._next_batch is not None:
            return
        if self._generation_task and not self._generation_task.done():
            return
        batch_size = int(self._show_manager.settings.get("batch_size", 6))
        threshold = max(int(batch_size * float(self._show_manager.settings.get("regenerate_at_ratio", 0.75))), 1)
        played_into_batch = batch_size - max(len([item for item in self._queue if item.type == "segment"]), 0)
        if played_into_batch >= threshold:
            self._generation_task = asyncio.create_task(self._fill_next_batch())

    async def _fill_next_batch(self) -> None:
        self._next_batch = await self.generate_next_batch()

    def _should_play_opening(self) -> bool:
        return bool(
            self._show_manager.settings.get("opening_enabled", True)
            and self._show_manager.show.opening.strip()
        )

    @staticmethod
    def _bridge_for(bridges: list[Bridge], current: Segment, next_segment: Segment) -> Bridge:
        for bridge in bridges:
            if bridge.from_title == current.title and bridge.to_title == next_segment.title:
                return bridge
        return Bridge(
            from_title=current.title,
            to_title=next_segment.title,
            text=f"说到{current.title}，这事儿还能拐到{next_segment.title}。",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/talkshow/test_controller.py -v`

Expected: PASS, 4 tests.

---

### Task 4: HTTP Agent and LiveAvatar Session Lifecycle

**Files:**
- Create: `talkshow/agent.py`
- Test: `tests/talkshow/test_agent_routes.py`

**Interfaces:**
- Consumes: `ShowManager`, `TalkshowScriptGenerator`, `TalkshowController`
- Produces aiohttp app routes:
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

- [ ] **Step 1: Write route tests with mocked globals**

Create `tests/talkshow/test_agent_routes.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from talkshow import agent


@pytest.mark.asyncio
async def test_status_returns_controller_status():
    controller = MagicMock()
    controller.get_status.return_value = {
        "state": "performing",
        "currentItem": None,
        "queueRemaining": 0,
        "nextBatchReady": False,
        "lastError": None,
    }
    agent._controller = controller

    request = MagicMock()
    response = await agent.handle_talkshow_status(request)

    assert response.status == 200
    assert b'"state": "performing"' in response.body


@pytest.mark.asyncio
async def test_start_requires_controller():
    agent._controller = None
    request = MagicMock()

    response = await agent.handle_talkshow_start(request)

    assert response.status == 500
    assert b"Not initialized" in response.body


@pytest.mark.asyncio
async def test_talkshow_action_calls_controller():
    controller = MagicMock()
    controller.start = AsyncMock()
    agent._controller = controller

    response = await agent.handle_talkshow_start(MagicMock())

    assert response.status == 200
    controller.start.assert_awaited_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/talkshow/test_agent_routes.py -v`

Expected: FAIL with missing `talkshow.agent`.

- [ ] **Step 3: Implement `talkshow/agent.py`**

Use `broadcast/agent.py` as a reference for:

- `_patch_ws_client()` with Chinese-safe `ensure_ascii=False`.
- `_scene_ready_hook`.
- `json_response`.
- `AvatarAgentConfig`.
- `_TalkshowListener.on_session_state()` forwarding `IDLE`.
- Static serving from `../frontend/talkshow.html`.

Implement route handlers with these exact names used by tests:

The route handlers must use these exact signatures:

```python
async def handle_talkshow_start(request: web.Request) -> web.Response:
    pass
async def handle_talkshow_stop(request: web.Request) -> web.Response:
    pass
async def handle_talkshow_pause(request: web.Request) -> web.Response:
    pass
async def handle_talkshow_resume(request: web.Request) -> web.Response:
    pass
async def handle_talkshow_skip(request: web.Request) -> web.Response:
    pass
async def handle_talkshow_status(request: web.Request) -> web.Response:
    pass
async def handle_talkshow_generate(request: web.Request) -> web.Response:
    pass
async def handle_talkshow_reload(request: web.Request) -> web.Response:
    pass
```

`handle_talkshow_generate` calls `await _controller.generate_next_batch()` and returns `segmentsGenerated`.

`handle_talkshow_reload` calls `_show_manager.reload()` and returns `topicCount`.

Environment variables:

```python
LIVEAVATAR_API_KEY
LIVEAVATAR_AVATAR_ID
LIVEAVATAR_BASE_URL
LIVEAVATAR_VOICE_ID
TALKSHOW_HTTP_PORT
DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL
DEEPSEEK_MODEL
TALKSHOW_CONFIG_PATH
```

- [ ] **Step 4: Run route tests**

Run: `pytest tests/talkshow/test_agent_routes.py -v`

Expected: PASS.

---

### Task 5: Frontend Operator Console

**Files:**
- Create: `../frontend/talkshow.html`

**Interfaces:**
- Consumes: `/api/start-session`, `/api/stop-session`, `/api/talkshow/status`, `/api/talkshow/{action}`
- Produces: static operator console for connection and talk show control

- [ ] **Step 1: Create `frontend/talkshow.html`**

Base it on `frontend/broadcast.html` connection code, with these replacements:

```javascript
async function talkshowAction(action) {
  const res = await fetch('/api/talkshow/' + action, { method: 'POST' });
  const data = await res.json();
  if (!data.success) console.error(data.error);
  await pollTalkshowStatus();
}

async function pollTalkshowStatus() {
  if (!isConnected) return;
  const res = await fetch('/api/talkshow/status');
  const status = await res.json();
  showRunning = status.state === 'performing' || status.state === 'paused';
  renderStatus(status);
  updateButtons();
}
```

Required UI elements:

- `#avatar-container`
- `#btn-connect`
- `#btn-disconnect`
- `#btn-start`
- `#btn-stop`
- `#btn-pause`
- `#btn-resume`
- `#btn-skip`
- `#btn-generate`
- `#btn-reload`
- `#show-title`
- `#show-meta`
- `#current-type`
- `#current-title`
- `#current-text`
- `#queue-remaining`
- `#next-batch`
- `#last-error`

Keep the visual design restrained and operational: dark background, large avatar panel, compact right panel, no landing-page hero.

- [ ] **Step 2: Verify static references**

Run: `python - <<'PY'
from pathlib import Path
html = Path('../frontend/talkshow.html').read_text(encoding='utf-8')
required = [
    './sdk.js',
    '/api/start-session',
    '/api/stop-session',
    '/api/talkshow/status',
    '/api/talkshow/start',
    'btn-generate',
    'current-type',
]
missing = [item for item in required if item not in html]
assert not missing, missing
PY`

Expected: command exits with status 0.

---

### Task 6: Quickstart and End-to-End Verification

**Files:**
- Create: `talkshow/QUICKSTART.md`
- Modify: `talkshow/agent.py` only if manual run reveals route serving issues.

**Interfaces:**
- Consumes all previous tasks.
- Produces documented run path for the talk show agent.

- [ ] **Step 1: Write `talkshow/QUICKSTART.md`**

Include:

```markdown
# Quickstart — Talkshow Digital Human

## Environment

export LIVEAVATAR_API_KEY="lk_live_xxx"
export LIVEAVATAR_AVATAR_ID="avatar_xxx"
export DEEPSEEK_API_KEY="sk-xxx"
export TALKSHOW_HTTP_PORT="8082"

## Run

python talkshow/agent.py

Open http://localhost:8082 and click Connect.

## Config

Edit config/talkshow.yaml for persona, topics, opening, boundaries, and fallback_segments.

## API

curl http://localhost:8082/api/talkshow/status
curl -X POST http://localhost:8082/api/talkshow/generate
curl -X POST http://localhost:8082/api/talkshow/reload
curl -X POST http://localhost:8082/api/talkshow/start
curl -X POST http://localhost:8082/api/talkshow/stop
```

- [ ] **Step 2: Run focused talkshow tests**

Run: `pytest tests/talkshow -v`

Expected: PASS.

- [ ] **Step 3: Run existing broadcast tests to catch accidental regressions**

Run: `pytest tests/broadcast -v`

Expected: PASS.

- [ ] **Step 4: Run import smoke test**

Run: `python - <<'PY'
from talkshow.show_manager import ShowManager
from talkshow.script_generator import TalkshowScriptGenerator
from talkshow.controller import TalkshowController, TalkshowState
import talkshow.agent
print(TalkshowState.IDLE.value)
PY`

Expected output:

```text
idle
```

- [ ] **Step 5: Manual server smoke test without external connection**

Run: `TALKSHOW_HTTP_PORT=8082 python talkshow/agent.py`

Expected log: aiohttp server running on `http://0.0.0.0:8082`.

In a second terminal, run: `curl http://localhost:8082/api/talkshow/status`

Expected: JSON response. If no LiveAvatar session has been started, response may be a controlled `Not initialized` error.

Stop the server with Ctrl-C.

---

## Self-Review

- Spec coverage: configuration, default opening behavior, generated segment shape, bridges, runtime flow, HTTP API, frontend page, and test expectations are covered by Tasks 1-6.
- Placeholder scan: this plan avoids unresolved placeholders and open-ended "handle later" instructions.
- Type consistency: `Segment`, `Bridge`, `ShowBatch`, and `PlaybackItem` are defined in Task 1 and consumed by Tasks 2-4 with the same names.
- Scope check: no task modifies `broadcast/`, adds viewer interaction, writes generated batches to YAML, or adds video configuration.
