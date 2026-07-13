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

        # Platform TTS idle signal — set when session.state=IDLE is received
        self._tts_idle = asyncio.Event()
        self._tts_idle.set()  # initially idle
        self._idle_count = 0
        self._timeout_count = 0

        # Regenerate hook — called when queue loops, to generate fresh scripts
        self._regenerate_hook: callable | None = None

        # Message queue — external events (comments, welcomes) are inserted here
        # and played between scripts without interrupting the current one.
        self._message_queue: list[str] = []

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
        asyncio.create_task(self._agent.send_interrupt())
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
            # Interrupt current TTS playback
            await self._agent.send_interrupt()

            reply = await self._llm.generate(text)
            await self._agent.send_prompt(reply)
            logger.info("💬 Reply: %s", reply[:60])

            # Clear idle flag so broadcast waits for reply TTS to finish
            # before continuing with the next script
            self._tts_idle.clear()

            return reply
        finally:
            if not was_paused:
                self.resume()

    def notify_platform_idle(self) -> None:
        """Called by the AgentListener when platform reports session.state=IDLE."""
        self._idle_count += 1
        self._tts_idle.set()

    def enqueue_message(self, text: str) -> None:
        """Insert a message into the broadcast queue.

        It will be played after the current script finishes, before the next
        scheduled script. No interruption needed.
        """
        if not text or not text.strip():
            return
        self._message_queue.append(text.strip())
        logger.info("📨 Queued message: %s", text[:60])

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
                product.name,
                self._queue_index + 1,
                len(products),
            )
            await self._broadcast_product(product)

            if self._stopped:
                break

            # Advance queue
            self._queue_index += 1
            if self._queue_index >= len(products):
                if self._loop:
                    # No need to reload — save_scripts() already
                    # updated vs.scripts in memory.
                    self._queue_index = 0
                    logger.info("🔁 Queue looped back to start")
                else:
                    logger.info("✅ Queue exhausted, stopping")
                    break

        self._state = BroadcastState.IDLE

    async def _broadcast_product(self, product) -> None:
        """Broadcast all video-script pairs for one product in order."""
        if not product.video_scripts:
            logger.warning("Product %s has no video_scripts — skipping", product.id)
            return

        # Flatten all (video, script) pairs in order
        pairs: list[tuple[str, str]] = []
        for vs in product.video_scripts:
            for script in vs.scripts:
                pairs.append((vs.video, script))

        total = len(pairs)
        logger.info("📦 Product %s: %d scripts across %d videos",
                     product.name, total, len(product.video_scripts))

        # Trigger background regeneration at 75% mark so new scripts
        # are ready before the current batch finishes.
        regen_triggered = False
        regen_threshold = max(int(total * 0.75), 1)

        for idx, (video, script) in enumerate(pairs):
            if self._stopped:
                return
            await self._paused_event.wait()

            # Fire-and-forget regeneration in background (per-product)
            if not regen_triggered and idx >= regen_threshold and self._regenerate_hook:
                regen_triggered = True
                logger.info("🔄 Triggering regeneration for %s...", product.id)
                asyncio.create_task(self._regenerate_hook(product.id))

            # Switch video only when it changes
            if self._current_video != video:
                self._current_video = video
                await self._agent.send_custom_event(
                    request_id=None,
                    event="scene.switchVideo",
                    data={
                        "onceVideos": [video],
                        "loopVideos": [product.loop_video],
                    },
                )
                logger.info("🎬 Video → %s", video)

            # Wait for platform TTS to be idle
            logger.debug("⏳ Waiting for TTS idle (seq=%d)...", self._idle_count)
            try:
                await asyncio.wait_for(self._tts_idle.wait(), timeout=30.0)
                logger.debug("✅ TTS idle received (seq=%d)", self._idle_count)
            except asyncio.TimeoutError:
                self._timeout_count += 1
                logger.warning("⏰ TTS idle timeout #%d (sent=%d recv=%d) — continuing",
                               self._timeout_count, self._idle_count + self._timeout_count, self._idle_count)
            self._tts_idle.clear()

            if self._stopped:
                return

            # Drain any queued messages (comments, welcomes) before next script
            while self._message_queue:
                msg = self._message_queue.pop(0)
                # Skip empty messages — they break platform TTS
                if not msg or not msg.strip():
                    logger.debug("📨 Skipping empty queued message")
                    continue
                logger.info("📨 Playing queued (q=%d): %s", len(self._message_queue), msg[:60])
                await self._agent.send_prompt(msg)
                try:
                    await asyncio.wait_for(self._tts_idle.wait(), timeout=15.0)
                    logger.debug("✅ Queued msg idle received (seq=%d)", self._idle_count)
                except asyncio.TimeoutError:
                    self._timeout_count += 1
                    logger.warning("⏰ Queued msg timeout #%d (sent=%d recv=%d) — continuing",
                                   self._timeout_count, self._idle_count + self._timeout_count, self._idle_count)
                self._tts_idle.clear()

            await self._paused_event.wait()

            self._current_script_index = idx
            await self._agent.send_prompt(script)
            logger.info("📢 [%d/%d] %s", idx + 1, total, script[:60])

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
