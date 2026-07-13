"""TikTok Live chat monitor with rate-limited comment/join callbacks."""

from __future__ import annotations

import asyncio
import logging
import re
import time

from TikTokLive import TikTokLiveClient
from TikTokLive.events import (
    CommentEvent,
    ConnectEvent,
    DisconnectEvent,
    JoinEvent,
)

logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple cooldown-based rate limiter."""

    def __init__(self, cooldown_seconds: float) -> None:
        self._cooldown = cooldown_seconds
        self._last_triggered: float = 0.0
        self._blocked_count = 0

    def try_acquire(self) -> bool:
        now = time.time()
        if self._cooldown == 0 or now - self._last_triggered >= self._cooldown:
            self._last_triggered = now
            return True
        self._blocked_count += 1
        if self._blocked_count % 10 == 1:
            logger.debug("RateLimiter blocked %d events", self._blocked_count)
        return False


class TikTokMonitor:

    def __init__(
        self,
        *,
        live_url: str,
        on_comment: callable | None = None,
        on_join: callable | None = None,
        comment_cooldown_s: float = 10.0,
        join_cooldown_s: float = 30.0,
        settings: dict | None = None,
    ) -> None:
        self._live_url = live_url
        self._on_comment = on_comment
        self._on_join = on_join
        self._settings = settings or {}
        self._client: TikTokLiveClient | None = None
        self._task: asyncio.Task | None = None

        self._comment_limiter = RateLimiter(comment_cooldown_s)
        self._join_limiter = RateLimiter(join_cooldown_s)

    async def start(self) -> None:
        username = self._extract_username(self._live_url)
        if not username:
            logger.error("Cannot extract username from URL: %s", self._live_url)
            return

        logger.info("🎵 Connecting to TikTok live: @%s", username)
        logger.info("🎵 Comment cooldown=%ss  Join cooldown=%ss",
                     self._comment_limiter._cooldown,
                     self._join_limiter._cooldown)

        web_proxy = self._settings.get("tiktok_web_proxy", None)
        ws_proxy = self._settings.get("tiktok_ws_proxy", None)
        client_kwargs = {"unique_id": f"@{username}"}
        if web_proxy:
            client_kwargs["web_proxy"] = web_proxy
        if ws_proxy:
            client_kwargs["ws_proxy"] = ws_proxy

        self._client = TikTokLiveClient(**client_kwargs)

        # --- connect / disconnect (with auto-reconnect) ---
        self._username = username
        self._client_kwargs = client_kwargs
        self._reconnecting = False

        async def _on_disconnect(_) -> None:
            logger.warning("🎵 TikTok DISCONNECTED — @%s", username)
            if not self._reconnecting:
                self._reconnecting = True
                await asyncio.sleep(5)
                logger.info("🎵 TikTok reconnecting — @%s", username)
                self._task = asyncio.create_task(self._start_safe(username))

        self._client.add_listener(ConnectEvent, lambda _: logger.info(
            "🎵 TikTok CONNECTED — @%s", username))
        self._client.add_listener(DisconnectEvent, _on_disconnect)

        # --- debug: catch ALL events via pyee base class ---
        try:
            from pyee.asyncio import AsyncIOEventEmitter as _Base
            _Base.add_listener(self._client, "*",
                lambda e: logger.debug("🎵 RAW-EVENT: %s", type(e).__name__))
            logger.info("🎵 Catch-all listener registered (event debug ON)")
        except Exception as exc:
            logger.warning("🎵 Could not register catch-all: %s", exc)

        # --- comment ---
        if self._on_comment:
            logger.info("🎵 Registering comment handler")
            _on_cb = self._on_comment
            _lim = self._comment_limiter

            async def _on_comment(event: CommentEvent) -> None:
                logger.debug("🎵 CommentEvent received, rate-checking...")
                if not _lim.try_acquire():
                    logger.debug("🎵 Comment blocked by rate limiter")
                    return
                user = _get_user_name(event)
                text = getattr(event, "comment", "")
                logger.info("💬 TikTok comment [%s]: %s", user, text[:80])
                try:
                    await _on_cb(user, text)
                except Exception as exc:
                    logger.error("on_comment error: %s", exc)

            self._client.add_listener(CommentEvent, _on_comment)

        # --- join ---
        if self._on_join:
            logger.info("🎵 Registering join handler")
            _on_jb = self._on_join
            _jlim = self._join_limiter

            async def _on_join(event: JoinEvent) -> None:
                logger.debug("🎵 JoinEvent received, rate-checking...")
                if not _jlim.try_acquire():
                    logger.debug("🎵 Join blocked by rate limiter")
                    return
                user = _get_user_name(event)
                logger.info("👋 TikTok join: %s", user)
                try:
                    await _on_jb(user)
                except Exception as exc:
                    logger.error("on_join error: %s", exc)

            self._client.add_listener(JoinEvent, _on_join)

        # --- start ---
        logger.info("🎵 Creating start task...")
        self._task = asyncio.create_task(self._start_safe(username))

    async def _start_safe(self, username: str) -> None:
        try:
            await asyncio.wait_for(
                self._client.start(fetch_live_check=False), timeout=15.0)
            self._reconnecting = False
            logger.info("🎵 TikTok WS loop RUNNING — @%s", username)
        except asyncio.TimeoutError:
            self._reconnecting = False
            logger.error("🎵 TikTok TIMEOUT after 15s for @%s", username)
            self._client = None
        except Exception as exc:
            self._reconnecting = False
            logger.error("🎵 TikTok FAILED for @%s: %s (%s)", username, exc, type(exc).__name__)
            self._client = None

    async def stop(self) -> None:
        if self._client:
            try:
                await self._client.close()
            except Exception as exc:
                logger.debug("TikTok close error (ignored): %s", exc)
            self._client = None
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        logger.info("🎵 TikTok monitor stopped")

    @staticmethod
    def _extract_username(url: str) -> str:
        match = re.search(r"tiktok\.com/@([^/]+)", url)
        if match:
            return match.group(1)
        match = re.search(r"@(\w+)", url)
        if match:
            return match.group(1)
        return ""


def _get_user_name(event) -> str:
    user = getattr(event, "user", None)
    if user is None:
        return "unknown"
    return getattr(user, "nickname", None) or getattr(user, "unique_id", "unknown")
