import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from broadcast.controller import BroadcastController, BroadcastState
from broadcast.product_manager import Product, VideoScript


def make_product(**overrides) -> Product:
    defaults = {
        "id": "test_prod",
        "name": "Test",
        "url": "",
        "loop_video": "res_video_bg",
        "tts_speed": 1.0,
        "pause_after_script_ms": 10,  # very short for tests
        "video_scripts": [
            VideoScript(video="res_video_a", scripts=["Script A1.", "Script A2."]),
        ],
    }
    defaults.update(overrides)
    return Product(**defaults)


@pytest.fixture
def mock_agent():
    agent = AsyncMock()
    agent.is_running = True
    agent.send_custom_event = AsyncMock()
    agent.send_prompt = AsyncMock()
    agent.send_response_start = AsyncMock()
    agent.send_response_chunk = AsyncMock()
    agent.send_response_done = AsyncMock()
    agent.send_interrupt = AsyncMock()
    agent.send_response_cancel = AsyncMock()
    return agent


@pytest.fixture
def mock_product_manager():
    pm = MagicMock()
    return pm


@pytest.fixture
def mock_llm():
    llm = AsyncMock()
    llm.generate = AsyncMock()
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

    task = asyncio.ensure_future(controller.start())
    await asyncio.sleep(0.05)
    await controller.stop()
    await task

    mock_agent.send_custom_event.assert_called()
    call_kwargs = mock_agent.send_custom_event.call_args.kwargs
    assert call_kwargs["event"] == "scene.switchVideo"
    assert "onceVideos" in call_kwargs["data"]
    assert "loopVideos" in call_kwargs["data"]


@pytest.mark.asyncio
async def test_pause_and_resume(mock_agent, mock_product_manager, mock_llm):
    controller = BroadcastController(
        agent=mock_agent,
        product_manager=mock_product_manager,
        llm_client=mock_llm,
    )
    products = [make_product(id="p1")]
    mock_product_manager.get_products.return_value = products

    task = asyncio.ensure_future(controller.start())
    await asyncio.sleep(0.02)

    controller.pause()
    assert controller.state == BroadcastState.PAUSED

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

    task = asyncio.ensure_future(controller.start())
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

    task = asyncio.ensure_future(controller.start())
    await asyncio.sleep(0.02)

    controller.skip()
    await asyncio.sleep(0.02)

    mock_agent.send_interrupt.assert_called()

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

    task = asyncio.ensure_future(controller.start())
    await asyncio.sleep(0.02)

    mock_llm.generate.return_value = "有运费险的哦！"

    reply = await controller.handle_comment("这个有运费险吗？")
    assert reply == "有运费险的哦！"
    mock_agent.send_interrupt.assert_called()
    mock_llm.generate.assert_awaited_once_with("这个有运费险吗？")
    mock_agent.send_prompt.assert_any_await("有运费险的哦！")
    assert controller._message_queue == []

    await controller.stop()
    await task


@pytest.mark.asyncio
async def test_status_report(mock_agent, mock_product_manager, mock_llm):
    controller = BroadcastController(
        agent=mock_agent,
        product_manager=mock_product_manager,
        llm_client=mock_llm,
    )
    products = [
        make_product(id="p1", name="Product 1"),
        make_product(id="p2", name="Product 2"),
    ]
    mock_product_manager.get_products.return_value = products

    task = asyncio.ensure_future(controller.start())
    await asyncio.sleep(0.02)

    status = controller.get_status()
    assert status["state"] in ("broadcasting", "idle")

    await controller.stop()
    await task
