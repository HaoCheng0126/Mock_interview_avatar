"""Monkey-patch LiveAvatar WS client — scene.ready auto-start + raw message logging."""

from __future__ import annotations

import json
import logging

_ws_patched = False
_scene_ready_hook: callable | None = None


def patch_ws_client() -> None:
    """Patch _AvatarWsClient to detect scene.ready and log raw messages."""
    global _ws_patched
    if _ws_patched:
        return
    _ws_patched = True
    from liveavatar_channel_sdk._ws_client import _AvatarWsClient as _Cls

    _orig_handle_text = _Cls._handle_text
    _orig_send_json = _Cls.send_json

    async def _patched_handle_text(self, raw: str) -> None:
        logger = logging.getLogger(__name__)
        logger.debug("�� RAW RECV: %s", raw[:600])
        if _scene_ready_hook and '"event":"scene.ready"' in raw:
            logger.info("������ scene.ready -> starting teaching")
            try:
                await _scene_ready_hook()
            except Exception as exc:
                logger.error("scene.ready hook failed: %s", exc)
        await _orig_handle_text(self, raw)

    async def _patched_send_json(self, message: dict) -> None:
        logger = logging.getLogger(__name__)
        logger.debug(
            "�� RAW SEND: %s", json.dumps(message, ensure_ascii=False)[:600]
        )
        raw = json.dumps(message, ensure_ascii=False)
        await self._ws.send(raw)

    _Cls._handle_text = _patched_handle_text
    _Cls.send_json = _patched_send_json
