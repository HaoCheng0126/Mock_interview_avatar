"""Product configuration loader and video-script random selector."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class VideoScript:
    video: str
    scripts: list[str]


@dataclass
class Product:
    id: str
    name: str
    url: str = ""
    description: str = ""
    loop_video: str = ""
    tts_speed: float = 1.0
    pause_after_script_ms: int = 3000
    video_scripts: list[VideoScript] = field(default_factory=list)


class ProductManager:
    """Loads and manages product configuration from a YAML file."""

    def __init__(self, config_path: Path | str) -> None:
        self._config_path = Path(config_path)
        self._settings: dict = {}
        self._persona: dict = {}
        self._prompts: dict = {}
        self._products: list[Product] = []
        self._raw_data: dict = {}
        self._load()

    # -- public API ----------------------------------------------------------

    def get_products(self) -> list[Product]:
        return list(self._products)

    def get_product(self, product_id: str) -> Product | None:
        for p in self._products:
            if p.id == product_id:
                return p
        return None

    def get_persona_system_prompt(self, default: str = "") -> str:
        return str(self._persona.get("system_prompt", default))

    def get_prompt(self, name: str, default: str = "") -> str:
        return str(self._prompts.get(name, default))

    def format_prompt(self, name: str, default: str = "", **kwargs) -> str:
        template = self.get_prompt(name, default)
        if not template:
            return ""
        return template.format(**kwargs)

    def random_video_script(self, product_id: str) -> tuple[str, str]:
        """Pick a random video, then a random script from that video."""
        product = self.get_product(product_id)
        if product is None:
            raise ValueError(f"Product not found: {product_id}")
        populated = [vs for vs in product.video_scripts if vs.scripts]
        if not populated:
            raise ValueError(f"Product {product_id} has no video_scripts with scripts")
        vs = random.choice(populated)
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

    def save_scripts(
        self,
        product_id: str,
        scripts: list[str],
        *,
        video_id: str = "",
        name: str = "",
    ) -> None:
        """Update product scripts/name and persist to YAML.

        Args:
            product_id: Target product ID.
            scripts: Script texts to assign.
            video_id: If set, only update this video's scripts.
                      If empty (default), distribute across all videos round-robin.
            name: If set, update the product name.
        """
        product = self.get_product(product_id)
        if product is None:
            raise ValueError(f"Product not found: {product_id}")
        videos = product.video_scripts
        if not videos:
            raise ValueError(f"Product {product_id} has no video_scripts entries")

        if video_id:
            # Target specific video only
            target = None
            for vs in videos:
                if vs.video == video_id:
                    target = vs
                    break
            if target is None:
                raise ValueError(
                    f"Video {video_id} not found in product {product_id}"
                )
            target.scripts.clear()
            target.scripts.extend(scripts)
        else:
            # Distribute round-robin across all videos (default)
            for vs in videos:
                vs.scripts.clear()
            for i, script in enumerate(scripts):
                videos[i % len(videos)].scripts.append(script)

        # Persist to YAML via raw data
        for entry in self._raw_items():
            if str(entry.get("id", "")) == product_id:
                if name:
                    entry["name" if "products" in self._raw_data else "title"] = name
                if video_id:
                    for vs_entry in entry.get("video_scripts", []):
                        if vs_entry.get("video") == video_id:
                            vs_entry["scripts"] = list(scripts)
                            break
                else:
                    video_entries = entry.get("video_scripts", [])
                    for j, vs_entry in enumerate(video_entries):
                        if j < len(videos):
                            vs_entry["scripts"] = list(videos[j].scripts)
                break

        with open(self._config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                self._raw_data,
                f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
        logger.info(
            "Saved %d scripts for product %s (video=%s, name=%s) to %s",
            len(scripts), product_id, video_id or "all", name, self._config_path,
        )

    # -- internal ------------------------------------------------------------

    def _load(self) -> None:
        with open(self._config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        self._raw_data = data
        self._settings = data.get("settings", {})
        self._persona = data.get("persona", {})
        self._prompts = data.get("prompts", {})

        for entry in self._raw_items():
            product = self._parse_product(entry)
            if product is not None:
                self._products.append(product)

    def _raw_items(self) -> list[dict]:
        return self._raw_data.get("products") or self._raw_data.get("items", [])

    def _parse_product(self, entry: dict) -> Product | None:
        pid = str(entry.get("id", ""))
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
                    logger.error(
                        "Script file not found: %s (video=%s)", file_path, video
                    )
            if video:
                video_scripts.append(VideoScript(video=video, scripts=scripts))

        if not video_scripts:
            logger.warning(
                "Product %s has no valid video_scripts entries — skipping", pid
            )
            return None

        return Product(
            id=pid,
            name=entry.get("name") or entry.get("title", pid),
            url=entry.get("url", ""),
            description=entry.get("description", ""),
            loop_video=entry.get(
                "loop_video", defaults.get("default_loop_video", "")
            ),
            tts_speed=float(
                entry.get("tts_speed", defaults.get("default_tts_speed", 1.0))
            ),
            pause_after_script_ms=int(
                entry.get(
                    "pause_after_script_ms", defaults.get("default_pause_ms", 3000)
                )
            ),
            video_scripts=video_scripts,
        )
