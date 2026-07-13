from unittest.mock import AsyncMock, MagicMock

import pytest

from talkshow import agent
from talkshow.show_manager import ShowManager


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

    response = await agent.handle_talkshow_status(MagicMock())

    assert response.status == 200
    assert b'"state": "performing"' in response.body


@pytest.mark.asyncio
async def test_start_requires_controller():
    agent._controller = None

    response = await agent.handle_talkshow_start(MagicMock())

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


def test_build_avatar_config_includes_voice_config(tmp_path):
    config_path = tmp_path / "talkshow.yaml"
    config_path.write_text(
        """
voice:
  voice_config:
    volume: 70
    speed: 1.08
    pitch: 1.03
""",
        encoding="utf-8",
    )
    manager = ShowManager(config_path)

    config = agent._build_avatar_config(manager)

    assert config.voice_id == agent.VOICE_ID
    assert config.voice_config == {"volume": 70, "speed": 1.08, "pitch": 1.03}
