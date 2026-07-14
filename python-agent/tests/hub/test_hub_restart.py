"""Tests for hub._apply_config_to_running_agents — save-then-restart behavior."""

import pytest

from hub import hub


class FakeAgent:
    def __init__(self, running: bool) -> None:
        self._running = running
        self.stopped = False
        self.started_with = None

    @property
    def is_running(self) -> bool:
        return self._running

    async def stop(self) -> None:
        self.stopped = True
        self._running = False

    async def start(self, settings) -> None:
        self.started_with = settings
        self._running = True


SETTINGS = {"agents": {"interview": {"port": 8083}}}


@pytest.mark.asyncio
async def test_running_agent_is_restarted_with_new_settings(monkeypatch):
    fake = FakeAgent(running=True)
    monkeypatch.setattr(hub, "_managed", {"interview": fake})

    async def _never_in_use(_port):
        return False

    monkeypatch.setattr(hub, "_port_in_use", _never_in_use)

    effect = await hub._apply_config_to_running_agents(SETTINGS)

    assert fake.stopped is True
    assert fake.started_with is SETTINGS
    assert effect == {"restarted": ["interview"], "external": []}


@pytest.mark.asyncio
async def test_external_agent_is_flagged_not_restarted(monkeypatch):
    fake = FakeAgent(running=False)
    monkeypatch.setattr(hub, "_managed", {"interview": fake})

    async def _in_use(_port):
        return True

    monkeypatch.setattr(hub, "_port_in_use", _in_use)

    effect = await hub._apply_config_to_running_agents(SETTINGS)

    assert fake.started_with is None  # hub can't restart an external process
    assert effect == {"restarted": [], "external": ["interview"]}


@pytest.mark.asyncio
async def test_stopped_agent_untouched(monkeypatch):
    fake = FakeAgent(running=False)
    monkeypatch.setattr(hub, "_managed", {"interview": fake})

    async def _never_in_use(_port):
        return False

    monkeypatch.setattr(hub, "_port_in_use", _never_in_use)

    effect = await hub._apply_config_to_running_agents(SETTINGS)

    assert fake.stopped is False
    assert effect == {"restarted": [], "external": []}
