# E-commerce Broadcast Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an e-commerce live-stream broadcast digital human agent that queues products, switches background videos via WebSocket, and streams scripted narration with pause/resume for viewer comments.

**Architecture:** Four new modules — `LlmClient` (shared LLM), `ProductManager` (YAML config + video-script selection), `ScriptGenerator` (LLM-driven script creation), `BroadcastController` (queue engine + state machine). A new `broadcast_agent.py` HTTP server composes them all. Existing `agent.py` is untouched.

**Tech Stack:** Python 3.14, aiohttp, PyYAML, openai (DeepSeek), liveavatar-channel-sdk >= 0.2.4

---

## File Map

| File | Responsibility |
|------|---------------|
| `llm_client.py` (new) | Shared async LLM client extracted from agent.py pattern |
| `product_manager.py` (new) | Load/validate `config/products.yaml`, random video+script selection |
| `script_generator.py` (new) | Call LLM to generate scripts from product URL |
| `broadcast_controller.py` (new) | Queue engine, state machine, video switching, speed control |
| `broadcast_agent.py` (new) | HTTP server entry point, composes all modules |
| `config/products.yaml` (new) | Product/script/video configuration |
| `requirements.txt` (modify) | Add pyyaml, bump SDK to >=0.2.4 |
| `agent.py` | **UNTOCUHED** |

---

### Task 1: Shared LLM Client

**Files:**
- Create: `python-agent/llm_client.py`
- Create: `python-agent/tests/test_llm_client.py`

Extract the LLM calling pattern from agent.py into a reusable module. Both ScriptGenerator and BroadcastController need it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_client.py
import pytest
from llm_client import LlmClient


def test_llm_client_init_defaults():
    client = LlmClient()
    assert client._model == "deepseek-v4-flash"
    assert client._base_url == "https://api.deepseek.com"
    assert client._system_prompt == ""


def test_llm_client_init_custom():
    client = LlmClient(
        api_key="sk-test",
        base_url="https://custom.api.com",
        model="custom-model",
        system_prompt="You are a helpful shopping assistant.",
    )
    assert client._model == "custom-model"
    assert client._base_url == "https://custom.api.com"
    assert client._system_prompt == "You are a helpful shopping assistant."


def test_llm_client_reset_context():
    client = LlmClient(system_prompt="System")
    client._messages = [
        {"role": "system", "content": "System"},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
    ]
    client.reset_context()
    assert len(client._messages) == 1
    assert client._messages[0] == {"role": "system", "content": "System"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python-agent && python -m pytest tests/test_llm_client.py -v`
Expected: FAIL — "No module named 'llm_client'"

- [ ] **Step 3: Write minimal implementation**

```python
# llm_client.py
"""Shared async LLM client for DeepSeek via OpenAI-compatible API."""

from __future__ import annotations

import asyncio
import os

from openai import AsyncOpenAI


class LlmClient:
    """Async LLM client. Supports both streaming and non-streaming generation."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        system_prompt: str = "",
    ) -> None:
        self._api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self._base_url = base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self._model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
        self._system_prompt = system_prompt
        self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)
        self._messages: list[dict] = [{"role": "system", "content": system_prompt}]
        self._lock = asyncio.Lock()

    async def generate(self, user_text: str) -> str:
        """Non-streaming generation. Returns full reply."""
        self._messages.append({"role": "user", "content": user_text})
        if len(self._messages) > 21:
            self._messages = [self._messages[0]] + self._messages[-20:]

        full_reply = ""
        for attempt in range(2):
            try:
                stream = await self._client.chat.completions.create(
                    model=self._model,
                    messages=self._messages,
                    stream=True,
                    max_tokens=512,
                    temperature=0.7,
                )
                async for chunk in stream:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        full_reply += delta.content
                break
            except Exception:
                if attempt == 0:
                    await asyncio.sleep(1)
                else:
                    full_reply = "抱歉，我暂时无法回答，请稍后再试。"

        if full_reply.strip():
            self._messages.append({"role": "assistant", "content": full_reply})
        return full_reply

    async def generate_streaming(self, user_text: str, on_chunk) -> str:
        """Streaming generation. Calls on_chunk(text_delta) for each fragment."""
        async with self._lock:
            self._messages.append({"role": "user", "content": user_text})
            if len(self._messages) > 21:
                self._messages = [self._messages[0]] + self._messages[-20:]

            full_reply = ""
            for attempt in range(2):
                try:
                    stream = await self._client.chat.completions.create(
                        model=self._model,
                        messages=self._messages,
                        stream=True,
                        max_tokens=512,
                        temperature=0.7,
                    )
                    async for chunk in stream:
                        delta = chunk.choices[0].delta
                        if delta.content:
                            full_reply += delta.content
                            await on_chunk(delta.content)
                    break
                except Exception:
                    if attempt == 0:
                        await asyncio.sleep(1)
                    else:
                        full_reply = "抱歉，我暂时无法回答，请稍后再试。"
                        await on_chunk(full_reply)

            if full_reply.strip():
                self._messages.append({"role": "assistant", "content": full_reply})
            return full_reply

    def reset_context(self) -> None:
        self._messages = [{"role": "system", "content": self._system_prompt}]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python-agent && python -m pytest tests/test_llm_client.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd python-agent
git add llm_client.py tests/test_llm_client.py
git commit -m "feat: add shared LlmClient module

Extracted from agent.py pattern, supports streaming and non-streaming
generation with configurable model/base_url/system_prompt.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Config File

**Files:**
- Create: `python-agent/config/products.yaml`

Create the sample configuration file that ProductManager will load.

- [ ] **Step 1: Create products.yaml**

```yaml
# config/products.yaml
# E-commerce broadcast product/script/video configuration.

settings:
  loop: true                       # queue finished → loop from start
  default_tts_speed: 1.0           # fallback TTS speed
  default_pause_ms: 3000           # fallback inter-script pause (ms)
  chunk_delay_ms: 200              # micro-delay between sentences (ms)
  default_loop_video: "res_video_bg"  # shared background loop

products:
  - id: "prod_001"
    name: "XX气垫粉底"
    url: "https://www.tiktok.com/shop/pdp/1731199058452648921"
    loop_video: "res_video_bg"
    tts_speed: 1.0
    pause_after_script_ms: 3000
    video_scripts:
      - video: "res_video_prod001_a"
        scripts:
          - "欢迎来到直播间！今天给大家带来的是XX气垫粉底，这款产品真的是我最近用到最好用的底妆产品。"
          - "这款气垫的遮瑕效果真的绝了，你们看这个对比，轻轻一拍瑕疵全都隐形了。"
      - video: "res_video_prod001_b"
        scripts:
          - "现在下单立减50元，只有100单库存，手慢无！"
          - "姐妹们这个价格真的非常划算，错过今天就没有了，赶紧点击下方链接下单吧。"
```

- [ ] **Step 2: Commit**

```bash
cd python-agent
git add config/products.yaml
git commit -m "feat: add sample products.yaml config

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: ProductManager

**Files:**
- Create: `python-agent/product_manager.py`
- Create: `python-agent/tests/test_product_manager.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_product_manager.py
import tempfile
from pathlib import Path
import pytest
from product_manager import ProductManager, Product, VideoScript


SAMPLE_YAML = """
settings:
  loop: true
  default_tts_speed: 1.0
  default_pause_ms: 3000
  chunk_delay_ms: 200
  default_loop_video: "res_video_bg"

products:
  - id: "prod_001"
    name: "Test Product"
    url: "https://example.com/product"
    loop_video: "res_video_bg"
    tts_speed: 1.2
    pause_after_script_ms: 5000
    video_scripts:
      - video: "res_video_a"
        scripts:
          - "Script A1"
          - "Script A2"
      - video: "res_video_b"
        scripts:
          - "Script B1"
  - id: "prod_002"
    name: "Minimal Product"
    video_scripts:
      - video: "res_video_c"
        scripts:
          - "Script C1"
"""


@pytest.fixture
def config_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_YAML)
        path = Path(f.name)
    yield path
    path.unlink()


def test_load_products(config_file):
    pm = ProductManager(config_file)
    products = pm.get_products()
    assert len(products) == 2
    assert products[0].id == "prod_001"
    assert products[0].name == "Test Product"
    assert products[0].tts_speed == 1.2
    assert products[0].pause_after_script_ms == 5000


def test_defaults_fallback(config_file):
    pm = ProductManager(config_file)
    prod2 = pm.get_products()[1]
    assert prod2.tts_speed == 1.0          # from settings.default_tts_speed
    assert prod2.pause_after_script_ms == 3000  # from settings.default_pause_ms
    assert prod2.loop_video == "res_video_bg"   # from settings.default_loop_video


def test_random_video_script(config_file):
    pm = ProductManager(config_file)
    video, script = pm.random_video_script("prod_001")
    assert video in ("res_video_a", "res_video_b")
    if video == "res_video_a":
        assert script in ("Script A1", "Script A2")
    else:
        assert script == "Script B1"


def test_random_video_script_unknown_product(config_file):
    pm = ProductManager(config_file)
    with pytest.raises(ValueError, match="Product not found"):
        pm.random_video_script("nonexistent")


def test_add_script(config_file):
    pm = ProductManager(config_file)
    pm.add_script("prod_002", "res_video_c", "Script C2")
    # verify: now res_video_c should have 2 scripts
    video, _ = pm.random_video_script("prod_002")
    assert video == "res_video_c"  # only one video for prod_002


def test_add_script_new_video(config_file):
    pm = ProductManager(config_file)
    pm.add_script("prod_001", "res_video_new", "New script")
    # Should be selectable
    found = False
    for _ in range(50):
        video, script = pm.random_video_script("prod_001")
        if video == "res_video_new" and script == "New script":
            found = True
            break
    assert found


def test_validate_skips_product_without_video_scripts(config_file):
    pm = ProductManager(config_file)
    # All products in SAMPLE_YAML have video_scripts, so no skips
    assert len(pm.get_products()) == 2


def test_reload(config_file):
    pm = ProductManager(config_file)
    assert len(pm.get_products()) == 2
    # Modify the file to add a product
    new_yaml = SAMPLE_YAML + """
  - id: "prod_003"
    name: "Added Later"
    video_scripts:
      - video: "res_video_d"
        scripts:
          - "Script D1"
"""
    config_file.write_text(new_yaml)
    pm.reload()
    assert len(pm.get_products()) == 3
    # Restore
    config_file.write_text(SAMPLE_YAML)
    pm.reload()
    assert len(pm.get_products()) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python-agent && python -m pytest tests/test_product_manager.py -v`
Expected: FAIL — "No module named 'product_manager'"

- [ ] **Step 3: Write minimal implementation**

```python
# product_manager.py
"""Product configuration loader and video-script random selector."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class VideoScript:
    video: str
    scripts: list[str]


@dataclass
class Product:
    id: str
    name: str
    url: str = ""
    loop_video: str = ""
    tts_speed: float = 1.0
    pause_after_script_ms: int = 3000
    video_scripts: list[VideoScript] = field(default_factory=list)


class ProductManager:
    """Loads and manages product configuration from a YAML file."""

    def __init__(self, config_path: Path | str) -> None:
        self._config_path = Path(config_path)
        self._settings: dict = {}
        self._products: list[Product] = []
        self._load()

    # -- public API ----------------------------------------------------------

    def get_products(self) -> list[Product]:
        return list(self._products)

    def get_product(self, product_id: str) -> Product | None:
        for p in self._products:
            if p.id == product_id:
                return p
        return None

    def random_video_script(self, product_id: str) -> tuple[str, str]:
        """Pick a random video, then a random script from that video."""
        product = self.get_product(product_id)
        if product is None:
            raise ValueError(f"Product not found: {product_id}")
        if not product.video_scripts:
            raise ValueError(f"Product {product_id} has no video_scripts")
        vs = random.choice(product.video_scripts)
        if not vs.scripts:
            raise ValueError(
                f"Video {vs.video} in product {product_id} has no scripts"
            )
        script = random.choice(vs.scripts)
        return vs.video, script

    def add_script(self, product_id: str, video_id: str, text: str) -> None:
        """Append a script to a specific video entry."""
        product = self.get_product(product_id)
        if product is None:
            raise ValueError(f"Product not found: {product_id}")
        for vs in product.video_scripts:
            if vs.video == video_id:
                vs.scripts.append(text)
                return
        # Video not found — create a new entry
        product.video_scripts.append(VideoScript(video=video_id, scripts=[text]))

    def update_script(
        self, product_id: str, video_id: str, index: int, text: str
    ) -> None:
        """Replace a script at the given index."""
        product = self.get_product(product_id)
        if product is None:
            raise ValueError(f"Product not found: {product_id}")
        for vs in product.video_scripts:
            if vs.video == video_id:
                if index < 0 or index >= len(vs.scripts):
                    raise IndexError(
                        f"Script index {index} out of range for video {video_id}"
                    )
                vs.scripts[index] = text
                return
        raise ValueError(f"Video {video_id} not found in product {product_id}")

    def reload(self) -> None:
        """Hot-reload configuration from the YAML file."""
        self._products.clear()
        self._load()

    # -- internal ------------------------------------------------------------

    def _load(self) -> None:
        with open(self._config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        self._settings = data.get("settings", {})

        for entry in data.get("products", []):
            product = self._parse_product(entry)
            if product is not None:
                self._products.append(product)

    def _parse_product(self, entry: dict) -> Product | None:
        pid = entry.get("id", "")
        if not pid:
            return None

        defaults = self._settings
        video_scripts_raw = entry.get("video_scripts", [])
        video_scripts: list[VideoScript] = []
        for vs_entry in video_scripts_raw:
            video = vs_entry.get("video", "")
            scripts = list(vs_entry.get("scripts", []))
            # Support external scripts_file
            scripts_file = vs_entry.get("scripts_file")
            if scripts_file:
                file_path = self._config_path.parent.parent / scripts_file
                try:
                    lines = file_path.read_text(encoding="utf-8").strip().splitlines()
                    scripts.extend(line.strip() for line in lines if line.strip())
                except FileNotFoundError:
                    import logging
                    logging.getLogger(__name__).error(
                        "Script file not found: %s (video=%s)", file_path, video
                    )
            if video and scripts:
                video_scripts.append(VideoScript(video=video, scripts=scripts))
            elif video:
                import logging
                logging.getLogger(__name__).warning(
                    "Video %s in product %s has no scripts — skipping", video, pid
                )

        if not video_scripts:
            import logging
            logging.getLogger(__name__).warning(
                "Product %s has no valid video_scripts entries — skipping", pid
            )
            return None

        return Product(
            id=pid,
            name=entry.get("name", pid),
            url=entry.get("url", ""),
            loop_video=entry.get("loop_video", defaults.get("default_loop_video", "")),
            tts_speed=float(entry.get("tts_speed", defaults.get("default_tts_speed", 1.0))),
            pause_after_script_ms=int(
                entry.get("pause_after_script_ms", defaults.get("default_pause_ms", 3000))
            ),
            video_scripts=video_scripts,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python-agent && python -m pytest tests/test_product_manager.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
cd python-agent
git add product_manager.py tests/test_product_manager.py
git commit -m "feat: add ProductManager with YAML config loading

Supports video-script hierarchy, defaults fallback, random selection,
script CRUD, hot-reload, and external script files.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: ScriptGenerator

**Files:**
- Create: `python-agent/script_generator.py`
- Create: `python-agent/tests/test_script_generator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_script_generator.py
from unittest.mock import AsyncMock, patch
import pytest
from script_generator import ScriptGenerator


SCRIPT_GEN_PROMPT = """你是一个专业的电商直播带货主播。请根据以下商品信息，生成5段口播脚本。
每段脚本50-200字，适合口播，语气热情有感染力。
分段时注意：
- 每段脚本对应一段展示视频
- 内容涵盖：开场吸引、产品卖点、使用场景、价格优惠、限时催单
- 用口语化中文，多用感叹词（姐妹们、家人们、真的太...了）

商品信息：
{product_info}

请返回JSON格式：["脚本1", "脚本2", ...]"""


@pytest.mark.asyncio
async def test_generate_scripts_parses_json_array():
    mock_llm = AsyncMock()
    mock_llm.generate.return_value = '["脚本一内容", "脚本二内容", "脚本三内容", "脚本四内容", "脚本五内容"]'

    generator = ScriptGenerator(llm_client=mock_llm, prompt_template=SCRIPT_GEN_PROMPT)
    scripts = await generator.generate(
        url="https://example.com/product",
        product_info="XX气垫粉底，价格99元，遮瑕力强",
    )

    assert len(scripts) == 5
    assert scripts[0] == "脚本一内容"
    assert scripts[4] == "脚本五内容"
    mock_llm.generate.assert_called_once()


@pytest.mark.asyncio
async def test_generate_scripts_fallback_on_bad_json():
    mock_llm = AsyncMock()
    mock_llm.generate.return_value = "脚本1\n脚本2\n脚本3"

    generator = ScriptGenerator(llm_client=mock_llm)
    scripts = await generator.generate(url="https://example.com/product")

    # Should fall back to line-by-line parsing
    assert len(scripts) == 3
    assert scripts[0] == "脚本1"


@pytest.mark.asyncio
async def test_generate_scripts_llm_failure():
    mock_llm = AsyncMock()
    mock_llm.generate.side_effect = Exception("API error")

    generator = ScriptGenerator(llm_client=mock_llm)
    scripts = await generator.generate(url="https://example.com/product")

    assert scripts == []  # graceful degradation
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python-agent && python -m pytest tests/test_script_generator.py -v`
Expected: FAIL — "No module named 'script_generator'"

- [ ] **Step 3: Write minimal implementation**

```python
# script_generator.py
"""LLM-driven product script generation."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_TEMPLATE = """你是一个专业的电商直播带货主播。请根据以下商品信息，生成5-8段口播脚本。
每段脚本50-200字，适合口播，语气热情有感染力。
分段时注意：
- 每段脚本对应一段展示视频
- 内容涵盖：开场吸引、产品卖点、使用场景、价格优惠、限时催单
- 用口语化中文，多用感叹词（姐妹们、家人们、真的太...了）

商品信息：
{product_info}

请返回JSON数组格式：["脚本1", "脚本2", ...]"""


class ScriptGenerator:
    """Generates broadcast scripts for a product using an LLM."""

    def __init__(
        self,
        llm_client,
        *,
        prompt_template: str | None = None,
    ) -> None:
        self._llm = llm_client
        self._prompt_template = prompt_template or DEFAULT_PROMPT_TEMPLATE

    async def generate(
        self,
        *,
        url: str = "",
        product_info: str = "",
        num_scripts: int | None = None,
    ) -> list[str]:
        """Generate scripts for a product.

        Args:
            url: Product page URL (LLM will fetch if supported).
            product_info: Optional product description to include in prompt.
            num_scripts: Target number of scripts (hint only, not enforced).

        Returns:
            List of script strings. Empty list on failure.
        """
        # Build prompt
        info_parts = []
        if url:
            info_parts.append(f"商品链接: {url}")
        if product_info:
            info_parts.append(product_info)
        if not info_parts:
            info_parts.append("（请根据你的知识生成通用直播脚本）")

        prompt = self._prompt_template.format(product_info="\n".join(info_parts))

        if num_scripts:
            prompt += f"\n\n请生成恰好{num_scripts}段脚本。"

        try:
            raw = await self._llm.generate(prompt)
            return self._parse_response(raw)
        except Exception as exc:
            logger.error("Script generation failed: %s", exc)
            return []

    def _parse_response(self, raw: str) -> list[str]:
        """Parse LLM response into a list of scripts.

        Tries JSON array first, falls back to line-by-line.
        """
        raw = raw.strip()

        # Try JSON array
        try:
            scripts = json.loads(raw)
            if isinstance(scripts, list) and all(isinstance(s, str) for s in scripts):
                return [s.strip() for s in scripts if s.strip()]
        except json.JSONDecodeError:
            pass

        # Try to extract JSON array from markdown code block
        if raw.startswith("```"):
            lines = raw.splitlines()
            inner = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else raw
            try:
                scripts = json.loads(inner)
                if isinstance(scripts, list):
                    return [s.strip() for s in scripts if s.strip()]
            except json.JSONDecodeError:
                pass

        # Fallback: split by newlines, filter empty
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        # Remove numbering prefixes like "1. " or "1、"
        cleaned = []
        for line in lines:
            # Skip lines that are just JSON brackets
            if line in ("[", "]", "[", "]"):
                continue
            # Strip leading number + separator
            for sep in (". ", "、", ") ", "：", ": "):
                idx = line.find(sep)
                if 0 < idx < 5 and line[:idx].isdigit():
                    line = line[idx + len(sep):]
                    break
            if line:
                cleaned.append(line)

        return cleaned
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python-agent && python -m pytest tests/test_script_generator.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd python-agent
git add script_generator.py tests/test_script_generator.py
git commit -m "feat: add ScriptGenerator for LLM-driven script creation

Supports JSON array parsing, markdown code block extraction, and
line-by-line fallback. Graceful degradation on LLM failure.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: BroadcastController

**Files:**
- Create: `python-agent/broadcast_controller.py`
- Create: `python-agent/tests/test_broadcast_controller.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_broadcast_controller.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from broadcast_controller import BroadcastController, BroadcastState
from product_manager import Product


def make_product(**overrides) -> Product:
    defaults = {
        "id": "test_prod",
        "name": "Test",
        "url": "",
        "loop_video": "res_video_bg",
        "tts_speed": 1.0,
        "pause_after_script_ms": 100,  # short for tests
        "video_scripts": [],
    }
    defaults.update(overrides)
    return Product(**defaults)


@pytest.fixture
def mock_agent():
    agent = AsyncMock()
    agent.is_running = True
    agent.send_custom_event = AsyncMock()
    agent.send_response_start = AsyncMock()
    agent.send_response_chunk = AsyncMock()
    agent.send_response_done = AsyncMock()
    agent.send_response_cancel = AsyncMock()
    return agent


@pytest.fixture
def mock_product_manager():
    pm = MagicMock()
    pm.random_video_script = MagicMock(return_value=("res_video_a", "Test script content. Second sentence."))
    return pm


@pytest.fixture
def mock_llm():
    llm = AsyncMock()
    llm.generate_streaming = AsyncMock()
    llm.reset_context = MagicMock()
    return llm


@pytest.mark.asyncio
async def test_initial_state_idle(mock_agent, mock_product_manager, mock_llm):
    controller = BroadcastController(
        agent=mock_agent,
        product_manager=mock_product_manager,
        llm_client=mock_llm,
    )
    assert controller.state == BroadcastState.IDLE


@pytest.mark.asyncio
async def test_start_starts_broadcast(mock_agent, mock_product_manager, mock_llm):
    controller = BroadcastController(
        agent=mock_agent,
        product_manager=mock_product_manager,
        llm_client=mock_llm,
    )
    products = [make_product(id="p1"), make_product(id="p2")]
    mock_product_manager.get_products.return_value = products

    # Start and let it run one cycle, then stop
    task = asyncio.create_task(controller.start())
    await asyncio.sleep(0.05)
    await controller.stop()
    await task

    # Should have sent scene.switchVideo
    mock_agent.send_custom_event.assert_called()
    call_args = mock_agent.send_custom_event.call_args
    assert call_args[1]["event"] == "scene.switchVideo"
    assert "onceVideos" in call_args[1]["data"]
    assert "loopVideos" in call_args[1]["data"]


@pytest.mark.asyncio
async def test_pause_and_resume(mock_agent, mock_product_manager, mock_llm):
    controller = BroadcastController(
        agent=mock_agent,
        product_manager=mock_product_manager,
        llm_client=mock_llm,
    )
    products = [make_product(id="p1")]
    mock_product_manager.get_products.return_value = products

    task = asyncio.create_task(controller.start())
    await asyncio.sleep(0.02)

    # Pause
    controller.pause()
    assert controller.state == BroadcastState.PAUSED

    # Resume
    controller.resume()
    await asyncio.sleep(0.02)
    assert controller.state == BroadcastState.BROADCASTING

    await controller.stop()
    await task


@pytest.mark.asyncio
async def test_stop_transitions_to_idle(mock_agent, mock_product_manager, mock_llm):
    controller = BroadcastController(
        agent=mock_agent,
        product_manager=mock_product_manager,
        llm_client=mock_llm,
    )
    products = [make_product(id="p1")]
    mock_product_manager.get_products.return_value = products

    task = asyncio.create_task(controller.start())
    await asyncio.sleep(0.02)
    await controller.stop()
    await task

    assert controller.state == BroadcastState.IDLE


@pytest.mark.asyncio
async def test_skip_current_product(mock_agent, mock_product_manager, mock_llm):
    controller = BroadcastController(
        agent=mock_agent,
        product_manager=mock_product_manager,
        llm_client=mock_llm,
    )
    products = [make_product(id="p1"), make_product(id="p2")]
    mock_product_manager.get_products.return_value = products

    task = asyncio.create_task(controller.start())
    await asyncio.sleep(0.02)

    # Cancel should be called on skip
    controller.skip()
    await asyncio.sleep(0.02)

    # Should have sent response_cancel
    mock_agent.send_response_cancel.assert_called()

    await controller.stop()
    await task


@pytest.mark.asyncio
async def test_comment_triggers_reply(mock_agent, mock_product_manager, mock_llm):
    controller = BroadcastController(
        agent=mock_agent,
        product_manager=mock_product_manager,
        llm_client=mock_llm,
    )
    products = [make_product(id="p1")]
    mock_product_manager.get_products.return_value = products

    task = asyncio.create_task(controller.start())
    await asyncio.sleep(0.02)

    mock_llm.generate_streaming.return_value = "有运费险的哦！"

    reply = await controller.handle_comment("这个有运费险吗？")
    assert reply == "有运费险的哦！"
    # Controller should pause during reply and resume after
    # (state already resumed by handle_comment)

    await controller.stop()
    await task


@pytest.mark.asyncio
async def test_status_report(mock_agent, mock_product_manager, mock_llm):
    controller = BroadcastController(
        agent=mock_agent,
        product_manager=mock_product_manager,
        llm_client=mock_llm,
    )
    products = [make_product(id="p1", name="Product 1"), make_product(id="p2", name="Product 2")]
    mock_product_manager.get_products.return_value = products

    task = asyncio.create_task(controller.start())
    await asyncio.sleep(0.02)

    status = controller.get_status()
    assert status["state"] in ("broadcasting", "idle")

    await controller.stop()
    await task
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python-agent && python -m pytest tests/test_broadcast_controller.py -v`
Expected: FAIL — "No module named 'broadcast_controller'"

- [ ] **Step 3: Write minimal implementation**

```python
# broadcast_controller.py
"""Broadcast queue engine with video switching, speed control, and pause/resume."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from enum import Enum

logger = logging.getLogger(__name__)


class BroadcastState(str, Enum):
    IDLE = "idle"
    BROADCASTING = "broadcasting"
    PAUSED = "paused"


class BroadcastController:
    """Manages the broadcast queue, state machine, and video switching.

    Lifecycle:
        start() → loops through product queue → stop()
        pause() / resume() — mid-broadcast interruption
        skip() — jump to next product
        handle_comment(text) — pause, reply, resume

    State machine:
        IDLE → BROADCASTING → PAUSED → BROADCASTING → ... → IDLE
    """

    def __init__(
        self,
        *,
        agent,
        product_manager,
        llm_client,
        chunk_delay_ms: int = 200,
        loop: bool = True,
    ) -> None:
        self._agent = agent
        self._pm = product_manager
        self._llm = llm_client
        self._chunk_delay = chunk_delay_ms / 1000.0
        self._loop = loop

        # State
        self._state = BroadcastState.IDLE
        self._task: asyncio.Task | None = None
        self._paused_event = asyncio.Event()
        self._paused_event.set()  # not paused initially
        self._stopped = False

        # Current position
        self._queue_index = 0
        self._current_product_id: str | None = None
        self._current_video: str | None = None
        self._current_script_index = 0
        self._current_response_id: str | None = None
        self._current_request_id: str | None = None

    # -- public API ----------------------------------------------------------

    @property
    def state(self) -> BroadcastState:
        return self._state

    async def start(self) -> None:
        """Start the broadcast loop. Runs until stop() is called."""
        if self._state != BroadcastState.IDLE:
            return

        self._stopped = False
        self._state = BroadcastState.BROADCASTING
        self._task = asyncio.create_task(self._run())
        logger.info("📻 Broadcast started")

    async def stop(self) -> None:
        """Stop the broadcast loop gracefully."""
        self._stopped = True
        self._paused_event.set()  # unpause if waiting
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._state = BroadcastState.IDLE
        self._task = None
        logger.info("📻 Broadcast stopped")

    def pause(self) -> None:
        """Pause after the current sentence finishes."""
        if self._state == BroadcastState.BROADCASTING:
            self._state = BroadcastState.PAUSED
            self._paused_event.clear()
            logger.info("⏸️  Broadcast paused")

    def resume(self) -> None:
        """Resume broadcasting."""
        if self._state == BroadcastState.PAUSED:
            self._state = BroadcastState.BROADCASTING
            self._paused_event.set()
            logger.info("▶️  Broadcast resumed")

    def skip(self) -> None:
        """Skip current product and move to the next one."""
        if self._current_response_id:
            asyncio.create_task(
                self._agent.send_response_cancel(self._current_response_id)
            )
        self._paused_event.set()  # wake up if paused
        logger.info("⏭️  Skipping current product")

    async def handle_comment(self, text: str) -> str:
        """Handle a viewer comment: pause broadcast, generate reply, resume.

        Returns the reply text.
        """
        logger.info("💬 Comment received: %s", text[:80])

        was_paused = self._state == BroadcastState.PAUSED
        if not was_paused:
            self.pause()

        try:
            reply = ""
            request_id = str(uuid.uuid4())
            response_id = str(uuid.uuid4())

            await self._agent.send_response_start(request_id, response_id)

            seq = 0
            async def send_chunk(delta: str) -> None:
                nonlocal seq
                ts = int(time.time() * 1000)
                await self._agent.send_response_chunk(
                    request_id, response_id, seq, ts, delta
                )
                seq += 1

            reply = await self._llm.generate_streaming(text, send_chunk)
            await self._agent.send_response_done(request_id, response_id)

            return reply
        finally:
            if not was_paused:
                self.resume()

    def get_status(self) -> dict:
        """Return current broadcast status for the HTTP API."""
        products = self._pm.get_products()
        remaining = max(0, len(products) - self._queue_index)
        return {
            "state": self._state.value,
            "currentProduct": (
                {"id": self._current_product_id}
                if self._current_product_id
                else None
            ),
            "currentVideo": self._current_video,
            "currentScriptIndex": self._current_script_index,
            "queueRemaining": remaining,
        }

    # -- internal ------------------------------------------------------------

    async def _run(self) -> None:
        """Main broadcast loop."""
        products = self._pm.get_products()
        if not products:
            logger.warning("No products in queue — stopping")
            self._state = BroadcastState.IDLE
            return

        while not self._stopped:
            product = products[self._queue_index]
            self._current_product_id = product.id
            logger.info(
                "📦 Broadcasting product: %s (%d/%d)",
                product.name, self._queue_index + 1, len(products),
            )
            await self._broadcast_product(product)

            if self._stopped:
                break

            # Advance queue
            self._queue_index += 1
            if self._queue_index >= len(products):
                if self._loop:
                    self._queue_index = 0
                    logger.info("🔁 Queue looped back to start")
                else:
                    logger.info("✅ Queue exhausted, stopping")
                    break

        self._state = BroadcastState.IDLE

    async def _broadcast_product(self, product) -> None:
        """Broadcast one product: pick video+script, switch, stream."""
        try:
            video, script = self._pm.random_video_script(product.id)
        except ValueError as exc:
            logger.warning("Skipping product %s: %s", product.id, exc)
            return

        self._current_video = video

        # Switch video
        await self._agent.send_custom_event(
            request_id=None,
            event="scene.switchVideo",
            data={
                "onceVideos": [video],
                "loopVideos": [product.loop_video],
            },
        )
        logger.info("🎬 Switched video: onceVideos=[%s] loopVideos=[%s]", video, product.loop_video)

        # Start TTS response
        request_id = str(uuid.uuid4())
        response_id = str(uuid.uuid4())
        self._current_request_id = request_id
        self._current_response_id = response_id

        await self._agent.send_response_start(
            request_id=request_id,
            response_id=response_id,
            speed=product.tts_speed,
        )

        # Stream script sentence by sentence
        sentences = self._split_sentences(script)
        for i, sentence in enumerate(sentences):
            if self._stopped:
                return

            # Wait if paused
            await self._paused_event.wait()

            self._current_script_index = i
            ts = int(time.time() * 1000)
            await self._agent.send_response_chunk(
                request_id, response_id, i, ts, sentence
            )
            await asyncio.sleep(self._chunk_delay)

        await self._agent.send_response_done(request_id, response_id)
        self._current_response_id = None

        # Inter-script pause
        pause_s = product.pause_after_script_ms / 1000.0
        for _ in range(int(pause_s / 0.1)):
            if self._stopped:
                return
            await self._paused_event.wait()
            await asyncio.sleep(0.1)

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text by Chinese/English sentence boundaries."""
        result = []
        current = ""
        for ch in text:
            current += ch
            if ch in ("。", "！", "？", "!", "?", "\n"):
                if current.strip():
                    result.append(current.strip())
                current = ""
        if current.strip():
            result.append(current.strip())
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python-agent && python -m pytest tests/test_broadcast_controller.py -v`
Expected: ALL PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
cd python-agent
git add broadcast_controller.py tests/test_broadcast_controller.py
git commit -m "feat: add BroadcastController with state machine and video switching

Implements IDLE→BROADCASTING→PAUSED state machine, scene.switchVideo
custom events, sentence-by-sentence streaming, pause/resume/skip/comment
handling, and status reporting.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Requirements Update

**Files:**
- Modify: `python-agent/requirements.txt`

- [ ] **Step 1: Update requirements.txt**

```txt
aiohttp>=3.9
dashscope>=1.25.6
openai>=1.0.0
pyyaml>=6.0
liveavatar-channel-sdk>=0.2.4
```

- [ ] **Step 2: Install and verify**

Run: `cd python-agent && pip install -r requirements.txt`
Expected: Success, SDK >= 0.2.4 installed

- [ ] **Step 3: Commit**

```bash
cd python-agent
git add requirements.txt
git commit -m "chore: add pyyaml, bump liveavatar-channel-sdk to >=0.2.4

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Broadcast Agent Entry Point

**Files:**
- Create: `python-agent/broadcast_agent.py`

- [ ] **Step 1: Write broadcast_agent.py**

```python
#!/usr/bin/env python3
"""E-commerce Live Broadcast Agent — queue-based product narration.

Architecture:
  HTTP server (aiohttp) — serves broadcast control API
  ProductManager          — YAML config loading, video/script selection
  ScriptGenerator         — LLM-driven script creation
  BroadcastController     — Queue engine + state machine
  LlmClient              — DeepSeek LLM for script generation and user replies
  AvatarAgent (SDK)      — WS communication + scene.switchVideo

Usage:
  export DEEPSEEK_API_KEY=sk-xxx
  export LIVEAVATAR_API_KEY=lk_live_xxx
  python broadcast_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import traceback
from pathlib import Path

from aiohttp import web

from liveavatar_channel_sdk import AvatarAgent, AvatarAgentConfig, AgentListener

from broadcast_controller import BroadcastController
from llm_client import LlmClient
from product_manager import ProductManager
from script_generator import ScriptGenerator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_KEY = os.getenv("LIVEAVATAR_API_KEY", "")
AVATAR_ID = os.getenv("LIVEAVATAR_AVATAR_ID", "avatar_01k56rnqaz15fz4t0ha4ja1132")
BASE_URL = os.getenv("LIVEAVATAR_BASE_URL", "https://liveavatar.aimiai.com/vih/dispatcher")
VOICE_ID = os.getenv("LIVEAVATAR_VOICE_ID", None)
HTTP_PORT = int(os.getenv("BROADCAST_HTTP_PORT", "8081"))

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

CONFIG_PATH = Path(os.getenv("PRODUCTS_CONFIG_PATH", str(Path(__file__).parent / "config" / "products.yaml")))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                            datefmt="%H:%M:%S")
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(handler)
    for name in ("httpx", "httpcore", "asyncio", "aiohttp", "websockets"):
        logging.getLogger(name).setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Global state (module-level, like agent.py)
# ---------------------------------------------------------------------------

_agent: AvatarAgent | None = None
_controller: BroadcastController | None = None
_product_manager: ProductManager | None = None
_script_generator: ScriptGenerator | None = None
_llm_client: LlmClient | None = None


async def init_broadcast() -> None:
    global _agent, _controller, _product_manager, _script_generator, _llm_client

    # Init LLM
    _llm_client = LlmClient(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        model=DEEPSEEK_MODEL,
        system_prompt="你是一个友好的电商直播助手。用简洁热情的中文回答观众问题，控制在100字以内。提到商品时引导用户下单。",
    )

    # Init ProductManager
    _product_manager = ProductManager(CONFIG_PATH)
    products = _product_manager.get_products()
    logging.getLogger(__name__).info("Loaded %d products from %s", len(products), CONFIG_PATH)

    # Init ScriptGenerator
    _script_generator = ScriptGenerator(llm_client=_llm_client)

    # Init AvatarAgent
    config = AvatarAgentConfig(
        api_key=API_KEY,
        avatar_id=AVATAR_ID,
        base_url=BASE_URL,
        developer_asr=False,  # no ASR needed for broadcast mode
        developer_tts=False,
        voice_id=VOICE_ID,
        timeout=30.0,
    )
    _agent = AvatarAgent(config, _BroadcastListener())
    result = await _agent.start()
    logger = logging.getLogger(__name__)
    logger.info("AvatarAgent connected — sessionId=%s", result.session_id)

    # Init BroadcastController
    _controller = BroadcastController(
        agent=_agent,
        product_manager=_product_manager,
        llm_client=_llm_client,
        chunk_delay_ms=_product_manager._settings.get("chunk_delay_ms", 200),
        loop=_product_manager._settings.get("loop", True),
    )


async def shutdown_broadcast() -> None:
    global _agent, _controller
    if _controller is not None:
        await _controller.stop()
        _controller = None
    if _agent is not None:
        await _agent.stop()
        _agent = None
    logging.getLogger(__name__).info("Broadcast shutdown complete")


class _BroadcastListener(AgentListener):
    """Minimal listener — broadcast mode doesn't use ASR or text input."""
    pass


# ---------------------------------------------------------------------------
# HTTP Handlers
# ---------------------------------------------------------------------------

async def handle_broadcast_start(request: web.Request) -> web.Response:
    if _controller is None:
        return web.json_response({"success": False, "error": "Not initialized"}, status=500)
    await _controller.start()
    queue_len = len(_product_manager.get_products()) if _product_manager else 0
    return web.json_response({"success": True, "queueLength": queue_len})


async def handle_broadcast_stop(request: web.Request) -> web.Response:
    if _controller is None:
        return web.json_response({"success": False, "error": "Not initialized"}, status=500)
    await _controller.stop()
    return web.json_response({"success": True})


async def handle_broadcast_pause(request: web.Request) -> web.Response:
    if _controller is None:
        return web.json_response({"success": False, "error": "Not initialized"}, status=500)
    _controller.pause()
    return web.json_response({"success": True})


async def handle_broadcast_resume(request: web.Request) -> web.Response:
    if _controller is None:
        return web.json_response({"success": False, "error": "Not initialized"}, status=500)
    _controller.resume()
    return web.json_response({"success": True})


async def handle_broadcast_skip(request: web.Request) -> web.Response:
    if _controller is None:
        return web.json_response({"success": False, "error": "Not initialized"}, status=500)
    _controller.skip()
    return web.json_response({"success": True})


async def handle_broadcast_status(request: web.Request) -> web.Response:
    if _controller is None:
        return web.json_response({"success": False, "error": "Not initialized"}, status=500)
    return web.json_response(_controller.get_status())


async def handle_comment(request: web.Request) -> web.Response:
    if _controller is None:
        return web.json_response({"success": False, "error": "Not initialized"}, status=500)
    try:
        body = await request.json()
        text = body.get("text", "").strip()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON body"}, status=400)

    if not text:
        return web.json_response({"success": False, "error": "text is required"}, status=400)

    try:
        reply = await _controller.handle_comment(text)
        return web.json_response({"success": True, "reply": reply})
    except Exception as exc:
        logging.getLogger(__name__).error("handle_comment error: %s", exc)
        return web.json_response({"success": False, "error": str(exc)}, status=500)


async def handle_product_generate(request: web.Request) -> web.Response:
    if _script_generator is None:
        return web.json_response({"success": False, "error": "Not initialized"}, status=500)
    try:
        body = await request.json()
        url = body.get("url", "")
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON body"}, status=400)

    if not url:
        return web.json_response({"success": False, "error": "url is required"}, status=400)

    try:
        scripts = await _script_generator.generate(url=url)
        return web.json_response({"success": True, "scriptsGenerated": len(scripts), "scripts": scripts})
    except Exception as exc:
        logging.getLogger(__name__).error("generate error: %s", exc)
        return web.json_response({"success": False, "error": str(exc)}, status=500)


async def handle_product_scripts(request: web.Request) -> web.Response:
    if _product_manager is None:
        return web.json_response({"success": False, "error": "Not initialized"}, status=500)
    try:
        body = await request.json()
        product_id = body.get("productId", "")
        video_id = body.get("videoId", "")
        text = body.get("text", "")
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON body"}, status=400)

    if not product_id or not video_id or not text:
        return web.json_response(
            {"success": False, "error": "productId, videoId, and text are required"}, status=400
        )

    try:
        _product_manager.add_script(product_id, video_id, text)
        return web.json_response({"success": True})
    except ValueError as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=404)


async def handle_product_reload(request: web.Request) -> web.Response:
    if _product_manager is None:
        return web.json_response({"success": False, "error": "Not initialized"}, status=500)
    try:
        _product_manager.reload()
        count = len(_product_manager.get_products())
        return web.json_response({"success": True, "productCount": count})
    except Exception as exc:
        logging.getLogger(__name__).error("reload error: %s", exc)
        return web.json_response({"success": False, "error": str(exc)}, status=500)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not DEEPSEEK_API_KEY:
        print("⚠️  DEEPSEEK_API_KEY not set — LLM features won't work")
    if not API_KEY:
        print("⚠️  LIVEAVATAR_API_KEY not set")
        sys.exit(1)

    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting E-commerce Broadcast Agent on port %d", HTTP_PORT)

    app = web.Application()
    app.router.add_post("/api/broadcast/start", handle_broadcast_start)
    app.router.add_post("/api/broadcast/stop", handle_broadcast_stop)
    app.router.add_post("/api/broadcast/pause", handle_broadcast_pause)
    app.router.add_post("/api/broadcast/resume", handle_broadcast_resume)
    app.router.add_post("/api/broadcast/skip", handle_broadcast_skip)
    app.router.add_get("/api/broadcast/status", handle_broadcast_status)
    app.router.add_post("/api/comment", handle_comment)
    app.router.add_post("/api/product/generate", handle_product_generate)
    app.router.add_post("/api/product/scripts", handle_product_scripts)
    app.router.add_post("/api/product/reload", handle_product_reload)

    app.on_startup.append(lambda _app: init_broadcast())
    app.on_shutdown.append(lambda _app: shutdown_broadcast())

    web.run_app(app, host="0.0.0.0", port=HTTP_PORT)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
cd python-agent
git add broadcast_agent.py
git commit -m "feat: add broadcast_agent.py entry point with full HTTP API

Implements all API endpoints: broadcast start/stop/pause/resume/skip/status,
comment handling, product script generate/append/reload. Auto-connects
AvatarAgent on startup.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Smoke Test

- [ ] **Step 1: Verify imports work**

Run: `cd python-agent && python -c "
from llm_client import LlmClient
from product_manager import ProductManager
from script_generator import ScriptGenerator
from broadcast_controller import BroadcastController
print('All imports OK')
"`
Expected: `All imports OK`

- [ ] **Step 2: Run all unit tests**

Run: `cd python-agent && python -m pytest tests/ -v`
Expected: ALL PASS (3 + 8 + 3 + 8 = 22 tests)

- [ ] **Step 3: Verify config loads**

Run: `cd python-agent && python -c "
from product_manager import ProductManager
pm = ProductManager('config/products.yaml')
products = pm.get_products()
print(f'Loaded {len(products)} products')
for p in products:
    video, script = pm.random_video_script(p.id)
    print(f'  {p.name}: video={video} script={script[:40]}...')
"`
Expected: Lists the sample product with random video+script selection

- [ ] **Step 4: Commit**

```bash
cd python-agent
git add -A
git commit -m "test: smoke test — all imports, unit tests, config loading pass

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Implementation Order

Run tasks sequentially: **1 → 2 → 3 → 4 → 5 → 6 → 7 → 8**

Each task builds on the previous. Files created in earlier tasks are imported by later tasks.

## Key Design Decisions in Plan

1. **LlmClient extracted as shared module** — both ScriptGenerator and BroadcastController use it. Same pattern as agent.py's LlmClient but standalone so agent.py stays untouched.
2. **Sentences split by punctuation** — `.split_sentences()` uses `。！？!?\n` as delimiters. This is what enables "finish current sentence before pausing" behavior.
3. **`_paused_event` pattern** — `asyncio.Event` that is `.clear()` on pause and `.set()` on resume. The broadcast loop `await`s it before each sentence. Same pattern handles `skip()` by setting the event.
4. **`handle_comment` is self-contained** — it manages pause/resume internally and returns the reply synchronously to the HTTP caller. The HTTP handler doesn't need to know about state transitions.
5. **Chunk delay is interleaved with pause check** — `await asyncio.sleep(0.1)` in a loop checking `_paused_event` means pause takes effect within 100ms max.
