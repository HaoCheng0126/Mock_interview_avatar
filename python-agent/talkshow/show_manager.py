"""Talk show configuration loader and data models."""

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
    "pause_after_opening_ms": 800,
    "pause_after_segment_ms": 1200,
    "pause_after_bridge_ms": 500,
}


class ShowManager:
    """Loads and exposes talk show configuration from YAML."""

    def __init__(self, config_path: Path | str) -> None:
        self._config_path = Path(config_path)
        self.settings: dict = {}
        self.persona = Persona(name="Talkshow Host")
        self.show = Show(title="Talkshow")
        self.voice_config: dict[str, int | float | None] | None = None
        self._topics: list[Topic] = []
        self._fallback_segments: list[Segment] = []
        self._seed_batch: ShowBatch | None = None
        self.reload()

    def reload(self) -> None:
        data = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
        self._raw_data = data
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

        voice_raw = data.get("voice") or {}
        voice_config_raw = voice_raw.get("voice_config") or {}
        self.voice_config = (
            {
                key: value
                for key, value in voice_config_raw.items()
                if key
                in {
                    "volume",
                    "speed",
                    "stability",
                    "similarityBoost",
                    "style",
                    "pitch",
                }
            }
            or None
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

        seed_raw = data.get("seed_batch") or {}
        seed_segments = [
            Segment(
                topic_id=str(item.get("topic_id") or ""),
                title=str(item.get("title") or ""),
                text=str(item.get("text") or "").strip(),
                beats=[str(beat) for beat in item.get("beats") or []],
            )
            for item in seed_raw.get("segments") or []
            if str(item.get("text") or "").strip()
        ]
        seed_bridges = [
            Bridge(
                from_title=str(item.get("from_title") or ""),
                to_title=str(item.get("to_title") or ""),
                text=str(item.get("text") or "").strip(),
            )
            for item in seed_raw.get("bridges") or []
            if str(item.get("text") or "").strip()
        ]
        self._seed_batch = (
            ShowBatch(
                batch_title=str(seed_raw.get("batch_title") or "seed"),
                segments=seed_segments,
                bridges=seed_bridges,
            )
            if seed_segments
            else None
        )

    def get_topics(self) -> list[Topic]:
        return list(self._topics)

    def get_fallback_segments(self) -> list[Segment]:
        return list(self._fallback_segments)

    def get_seed_batch(self) -> ShowBatch | None:
        if self._seed_batch is None:
            return None
        return ShowBatch(
            batch_title=self._seed_batch.batch_title,
            segments=list(self._seed_batch.segments),
            bridges=list(self._seed_batch.bridges),
        )

    def save_seed_batch(self, batch: ShowBatch) -> None:
        self._raw_data["seed_batch"] = {
            "batch_title": batch.batch_title,
            "segments": [
                {
                    "topic_id": segment.topic_id,
                    "title": segment.title,
                    "beats": list(segment.beats),
                    "text": segment.text,
                }
                for segment in batch.segments
            ],
            "bridges": [
                {
                    "from_title": bridge.from_title,
                    "to_title": bridge.to_title,
                    "text": bridge.text,
                }
                for bridge in batch.bridges
            ],
        }
        self._config_path.write_text(
            yaml.safe_dump(
                self._raw_data,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        self.reload()
