"""Talk show playback controller."""

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
    """Manages the talk show queue, TTS pacing, and generation lifecycle."""

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
        self._seed_batch_used = False
        self._sleep = asyncio.sleep

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
                next_segment = batch.segments[index + 1]
                bridge = self._bridge_for(batch.bridges, segment, next_segment)
                items.append(
                    PlaybackItem(
                        type="bridge",
                        title=f"{segment.title} -> {next_segment.title}",
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
            if hasattr(self._show_manager, "save_seed_batch"):
                self._show_manager.save_seed_batch(batch)
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
                    batch = (
                        self._take_seed_batch()
                        or self._next_batch
                        or await self.generate_next_batch()
                    )
                    self._next_batch = None
                    self._queue.extend(self.expand_batch(batch))
                    self._recent_segments.extend(batch.segments)
                    self._recent_segments = self._recent_segments[-12:]
                    if self._seed_batch_used:
                        self._maybe_start_background_generation()

                await self._paused_event.wait()
                item = self._queue.pop(0)
                await self._play_item(item)
                self._maybe_start_background_generation()
        except asyncio.CancelledError:
            raise
        finally:
            if self._stopped:
                self._state = TalkshowState.IDLE

    async def _play_item(self, item: PlaybackItem) -> None:
        await self._paused_event.wait()
        timeout = float(self._show_manager.settings.get("idle_timeout_s", 30))
        try:
            await asyncio.wait_for(self._tts_idle.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("TTS idle timeout before %s", item.title)
        self._tts_idle.clear()
        self._current_item = item
        parts = self._split_spoken_text(item.text, item.type)
        for idx, part in enumerate(parts):
            if idx > 0:
                try:
                    await asyncio.wait_for(self._tts_idle.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    logger.warning("TTS idle timeout within %s", item.title)
                self._tts_idle.clear()
            await self._agent.send_prompt(part)
        await self._sleep_after_item(item.type)

    def _maybe_start_background_generation(self) -> None:
        if self._next_batch is not None:
            return
        if self._generation_task and not self._generation_task.done():
            return

        batch_size = int(self._show_manager.settings.get("batch_size", 6))
        threshold = max(
            int(
                batch_size
                * float(self._show_manager.settings.get("regenerate_at_ratio", 0.75))
            ),
            1,
        )
        remaining_segments = len([item for item in self._queue if item.type == "segment"])
        played_into_batch = batch_size - remaining_segments
        if played_into_batch >= threshold:
            self._generation_task = asyncio.create_task(self._fill_next_batch())

    async def _fill_next_batch(self) -> None:
        self._next_batch = await self.generate_next_batch()

    def _take_seed_batch(self) -> ShowBatch | None:
        if self._seed_batch_used:
            return None
        self._seed_batch_used = True
        get_seed_batch = getattr(self._show_manager, "get_seed_batch", None)
        if not get_seed_batch:
            return None
        batch = get_seed_batch()
        return batch if isinstance(batch, ShowBatch) else None

    def _should_play_opening(self) -> bool:
        return bool(
            self._show_manager.settings.get("opening_enabled", True)
            and self._show_manager.show.opening.strip()
        )

    async def _sleep_after_item(self, item_type: str) -> None:
        setting_by_type = {
            "opening": "pause_after_opening_ms",
            "segment": "pause_after_segment_ms",
            "bridge": "pause_after_bridge_ms",
        }
        setting = setting_by_type.get(item_type)
        if not setting:
            return
        pause_ms = int(self._show_manager.settings.get(setting, 0))
        if pause_ms > 0:
            await self._sleep(pause_ms / 1000.0)

    @staticmethod
    def _split_spoken_text(text: str, item_type: str) -> list[str]:
        if item_type != "segment":
            return [text.strip()] if text.strip() else []
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines or ([text.strip()] if text.strip() else [])

    @staticmethod
    def _bridge_for(
        bridges: list[Bridge], current: Segment, next_segment: Segment
    ) -> Bridge:
        for bridge in bridges:
            if (
                bridge.from_title == current.title
                and bridge.to_title == next_segment.title
            ):
                return bridge
        return Bridge(
            from_title=current.title,
            to_title=next_segment.title,
            text=f"说到{current.title}，这事儿还能拐到{next_segment.title}。",
        )
