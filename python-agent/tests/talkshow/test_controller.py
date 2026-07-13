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
        "pause_after_opening_ms": 0,
        "pause_after_segment_ms": 0,
        "pause_after_bridge_ms": 0,
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


def test_split_segment_text_keeps_performance_lines(
    mock_agent, show_manager, script_generator
):
    controller = TalkshowController(mock_agent, show_manager, script_generator)

    parts = controller._split_spoken_text(
        "铺垫第一句。\n递进第二句。\n\n包袱句单独一行！",
        "segment",
    )

    assert parts == ["铺垫第一句。", "递进第二句。", "包袱句单独一行！"]


def test_split_bridge_text_keeps_single_transition(
    mock_agent, show_manager, script_generator
):
    controller = TalkshowController(mock_agent, show_manager, script_generator)

    parts = controller._split_spoken_text("回扣上一段。自然打开下一段。", "bridge")

    assert parts == ["回扣上一段。自然打开下一段。"]


@pytest.mark.asyncio
async def test_sleep_after_item_uses_item_specific_pause(
    mock_agent, show_manager, script_generator
):
    show_manager.settings.update(
        {
            "pause_after_opening_ms": 800,
            "pause_after_segment_ms": 1200,
            "pause_after_bridge_ms": 500,
        }
    )
    controller = TalkshowController(mock_agent, show_manager, script_generator)
    controller._sleep = AsyncMock()

    await controller._sleep_after_item("opening")
    await controller._sleep_after_item("segment")
    await controller._sleep_after_item("bridge")

    assert [call.args[0] for call in controller._sleep.await_args_list] == [
        0.8,
        1.2,
        0.5,
    ]


@pytest.mark.asyncio
async def test_start_sends_opening_first(mock_agent, show_manager, script_generator):
    controller = TalkshowController(mock_agent, show_manager, script_generator)

    task = asyncio.create_task(controller.start())
    await asyncio.sleep(0.12)
    await controller.stop()
    await task

    assert mock_agent.send_prompt.await_args_list[0].args[0] == "开场白"


@pytest.mark.asyncio
async def test_start_uses_seed_batch_before_generating(
    mock_agent, show_manager, script_generator
):
    show_manager.get_seed_batch.return_value = ShowBatch(
        batch_title="冷启动节目",
        segments=[
            Segment(topic_id="workplace", title="冷段一", text="冷段一正文"),
            Segment(topic_id="city", title="冷段二", text="冷段二正文"),
        ],
        bridges=[Bridge(from_title="冷段一", to_title="冷段二", text="冷桥正文")],
    )
    controller = TalkshowController(mock_agent, show_manager, script_generator)

    task = asyncio.create_task(controller.start())
    await asyncio.sleep(0.12)
    await controller.stop()
    await task

    prompts = [call.args[0] for call in mock_agent.send_prompt.await_args_list]
    assert prompts[:3] == ["开场白", "冷段一正文", "冷桥正文"]


@pytest.mark.asyncio
async def test_successful_generation_saves_seed_batch(
    mock_agent, show_manager, script_generator
):
    show_manager.save_seed_batch = MagicMock()
    controller = TalkshowController(mock_agent, show_manager, script_generator)

    batch = await controller.generate_next_batch()

    show_manager.save_seed_batch.assert_called_once_with(batch)


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
