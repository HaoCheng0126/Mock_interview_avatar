from array import array
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from liveavatar_channel_sdk import SessionState

from interview.listener import InterviewListener
from interview.models import InterviewState


def listening_controller(exchange_id="ex_001"):
    return SimpleNamespace(
        state=InterviewState.LISTENING,
        current_answer_metadata=lambda: {
            "interviewId": "iv_test",
            "questionId": "q1",
            "exchangeId": exchange_id,
            "answerId": exchange_id.replace("ex_", "ans_"),
        },
    )


def prime_verified_voice(listener, exchange_id="ex_001"):
    assert listener.start_direct_audio_input(exchange_id) is True
    listener._record_pcm(array("h", [2400] * 4800).tobytes())  # 300 ms voiced PCM


@pytest.mark.asyncio
async def test_audio_frames_are_ignored_until_audio_input_enabled():
    asr_manager = MagicMock()
    # 16 kHz mono PCM (sample_rate=0, channel=0, codec=0) — fed straight through.
    frame = SimpleNamespace(payload=b"pcmpcm", sample_rate=0, channel=0, codec=0)
    listener = InterviewListener(asr_manager=asr_manager)
    listener.set_controller(listening_controller())

    await listener.on_audio_frame(frame)

    asr_manager.feed_audio.assert_not_called()

    listener.set_audio_input_enabled(True, "ex_001")
    await listener.on_audio_frame(frame)

    asr_manager.feed_audio.assert_called_once_with(b"pcmpcm")


def test_direct_pcm_is_fed_only_while_direct_capture_is_enabled():
    asr_manager = MagicMock()
    listener = InterviewListener(asr_manager=asr_manager)
    listener.set_controller(listening_controller())

    assert listener.feed_direct_audio(b"\x00\x00" * 320) is False
    listener.start_direct_audio_input("ex_001")
    assert listener.feed_direct_audio(b"\x00\x00" * 320) is True
    listener.stop_direct_audio_input()
    assert listener.feed_direct_audio(b"\x00\x00" * 320) is False

    asr_manager.feed_audio.assert_called_once_with(b"\x00\x00" * 320)


@pytest.mark.asyncio
async def test_non_16k_pcm_frame_is_resampled_before_feeding():
    asr_manager = MagicMock()
    # 24 kHz mono PCM: 300 samples resample to 200 at 16 kHz (2/3 ratio).
    payload = array("h", [0] * 300).tobytes()
    frame = SimpleNamespace(payload=payload, sample_rate=1, channel=0, codec=0)
    listener = InterviewListener(asr_manager=asr_manager)
    listener.set_controller(listening_controller())
    listener.set_audio_input_enabled(True, "ex_001")

    await listener.on_audio_frame(frame)

    (fed,), _ = asr_manager.feed_audio.call_args
    assert len(fed) == 200 * 2  # 200 int16 samples, not the raw 300


@pytest.mark.asyncio
async def test_opus_frame_is_skipped_not_fed():
    asr_manager = MagicMock()
    frame = SimpleNamespace(payload=b"\x00\x01\x02\x03", sample_rate=0, channel=0, codec=1)
    listener = InterviewListener(asr_manager=asr_manager)
    listener.set_controller(listening_controller())
    listener.set_audio_input_enabled(True, "ex_001")

    await listener.on_audio_frame(frame)

    asr_manager.feed_audio.assert_not_called()


def test_direct_audio_is_rejected_while_interviewer_is_speaking():
    asr_manager = MagicMock()
    listener = InterviewListener(asr_manager=asr_manager)
    listener.set_controller(SimpleNamespace(state=InterviewState.ASKING))
    assert listener.start_direct_audio_input("ex_001") is False

    assert listener.feed_direct_audio(b"\x00\x00" * 320) is False
    asr_manager.feed_audio.assert_not_called()


@pytest.mark.asyncio
async def test_speech_events_send_metadata_only_while_listening():
    controller = MagicMock()
    controller.state = InterviewState.LISTENING
    controller.current_answer_metadata.return_value = {
        "interviewId": "iv_test",
        "questionId": "q1",
        "exchangeId": "ex_001",
        "answerId": "ans_001",
    }
    controller.handle_answer = AsyncMock()

    agent = AsyncMock()
    listener = InterviewListener()
    listener.agent = agent
    listener.set_controller(controller)
    # 关掉 debounce 提交延迟，便于测试同步断言
    listener._submit_debounce_seconds = 0.0
    prime_verified_voice(listener)

    await listener._on_speech_started()
    await listener._on_asr_interim("hello")
    await listener._on_speech_stopped()
    await listener._on_asr_transcript("hello world")
    # 显式 flush 一次：当前 listener 是 debounce 提交，需要触发
    if listener._pending_submit_task is not None:
        await listener._pending_submit_task

    start_metadata = agent.send_voice_start.await_args.kwargs["metadata"]
    final_metadata = agent.send_asr_final.await_args.kwargs["metadata"]
    assert start_metadata["exchangeId"] == "ex_001"
    assert final_metadata["answerId"] == "ans_001"
    controller.handle_answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_noise_during_asking_is_ignored_until_text_is_stable():
    controller = MagicMock()
    controller.state = InterviewState.ASKING
    controller.open_floor_if_asking = AsyncMock()
    agent = AsyncMock()
    listener = InterviewListener()
    listener.agent = agent
    listener.set_controller(controller)

    await listener._on_speech_started()
    await listener._on_asr_interim("嗯")
    await listener._on_asr_transcript("啊")

    agent.send_voice_start.assert_not_called()
    controller.open_floor_if_asking.assert_not_awaited()


@pytest.mark.asyncio
async def test_speech_during_asking_cannot_open_the_answer_floor():
    controller = MagicMock()
    controller.state = InterviewState.ASKING
    controller.current_answer_metadata.return_value = {
        "interviewId": "iv_test",
        "questionId": "q1",
        "exchangeId": "ex_001",
        "answerId": "ans_001",
    }

    controller.open_floor_if_asking = AsyncMock()
    controller.mark_candidate_speaking = AsyncMock()
    controller.handle_answer = AsyncMock()

    agent = AsyncMock()
    listener = InterviewListener()
    listener.agent = agent
    listener.set_controller(controller)

    await listener._on_speech_started()
    await listener._on_asr_interim("我先补充")

    controller.open_floor_if_asking.assert_not_awaited()
    controller.mark_candidate_speaking.assert_not_awaited()
    agent.send_voice_start.assert_not_awaited()


@pytest.mark.asyncio
async def test_interim_and_final_are_published_to_direct_caption_sink():
    controller = MagicMock()
    controller.state = InterviewState.LISTENING
    controller.current_answer_metadata.return_value = {
        "interviewId": "iv_test",
        "exchangeId": "ex_001",
    }
    controller.mark_candidate_speaking = AsyncMock()
    controller.handle_answer = AsyncMock()

    agent = AsyncMock()
    sink = AsyncMock()
    listener = InterviewListener()
    listener.agent = agent
    listener.set_controller(controller)
    listener.add_caption_sink(sink)
    listener._submit_debounce_seconds = 0.0
    prime_verified_voice(listener)

    await listener._on_speech_started()
    await listener._on_asr_interim("这是实时字幕")
    await listener._on_asr_transcript("这是最终字幕")
    if listener._pending_submit_task is not None:
        await listener._pending_submit_task

    assert sink.await_args_list[0].args[0]["type"] == "interim"
    assert sink.await_args_list[0].args[0]["text"] == "这是实时字幕"
    assert sink.await_args_list[1].args[0]["type"] == "final"
    assert sink.await_args_list[1].args[0]["text"] == "这是最终字幕"


@pytest.mark.asyncio
async def test_rejects_speech_while_interviewer_thinking_check_is_speaking():
    controller = MagicMock()
    controller.state = InterviewState.THINKING_CHECK
    controller.current_answer_metadata.return_value = {
        "interviewId": "iv_test",
        "questionId": "q1",
        "exchangeId": "ex_001",
        "answerId": "ans_001",
    }
    controller.handle_answer = AsyncMock()

    agent = AsyncMock()
    listener = InterviewListener()
    listener.agent = agent
    listener.set_controller(controller)

    await listener._on_speech_started()
    await listener._on_asr_transcript("我刚才还在回答")

    agent.send_voice_start.assert_not_awaited()
    controller.handle_answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_speech_stopped_without_final_notifies_controller():
    controller = MagicMock()
    controller.state = InterviewState.LISTENING
    controller.current_answer_metadata.return_value = {
        "interviewId": "iv_test",
        "questionId": "q1",
        "exchangeId": "ex_001",
        "answerId": "ans_001",
    }
    controller.mark_candidate_speech_stopped = AsyncMock()

    agent = AsyncMock()
    listener = InterviewListener()
    listener.agent = agent
    listener.set_controller(controller)
    listener._submit_debounce_seconds = 0.0
    listener._min_accept_chars = 0
    listener._accept_threshold_chars = 0
    prime_verified_voice(listener)

    await listener._on_speech_started()
    # 喂一段 ASR interim 文本，触发 _maybe_accept_pending_speech → mark_candidate_speaking
    await listener._on_asr_interim("你好")
    # 再触发 speech_stopped，期望走到 mark_candidate_speech_stopped
    await listener._on_speech_stopped()
    if listener._pending_submit_task is not None:
        await listener._pending_submit_task

    # 当前产品行为：
    # - mark_candidate_speaking 会在 accept 阶段触发
    # - mark_candidate_speech_stopped 会在 speech_stopped 真正 flush 时触发
    controller.mark_candidate_speech_stopped.assert_awaited_once()


@pytest.mark.asyncio
async def test_text_input_forwards_to_controller_when_listening():
    controller = MagicMock()
    controller.state = InterviewState.LISTENING
    controller.handle_answer = AsyncMock()
    listener = InterviewListener()
    listener.set_controller(controller)

    await listener.on_text_input("  我用文字回答这道题  ", "req_text_1")

    controller.handle_answer.assert_awaited_once_with("req_text_1", "我用文字回答这道题")


@pytest.mark.asyncio
async def test_text_input_rejected_during_thinking_check():
    controller = MagicMock()
    controller.state = InterviewState.THINKING_CHECK
    controller.handle_answer = AsyncMock()
    listener = InterviewListener()
    listener.set_controller(controller)

    await listener.on_text_input("补充一下我的思路", "req_text_2")

    controller.handle_answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_input_ignored_when_not_listening():
    controller = MagicMock()
    controller.state = InterviewState.ASKING
    controller.handle_answer = AsyncMock()
    listener = InterviewListener()
    listener.set_controller(controller)

    await listener.on_text_input("现在还不能回答", "req_text_3")

    controller.handle_answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_input_ignored_when_blank():
    controller = MagicMock()
    controller.state = InterviewState.LISTENING
    controller.handle_answer = AsyncMock()
    listener = InterviewListener()
    listener.set_controller(controller)

    await listener.on_text_input("   ", "req_text_4")

    controller.handle_answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_input_clears_pending_voice_state():
    controller = MagicMock()
    controller.state = InterviewState.LISTENING
    controller.current_answer_metadata.return_value = {"interviewId": "iv_test"}
    controller.handle_answer = AsyncMock()

    agent = AsyncMock()
    listener = InterviewListener()
    listener.agent = agent
    listener.set_controller(controller)

    # Voice capture in flight, then the candidate types instead.
    await listener._on_speech_started()
    await listener.on_text_input("改用打字回答", "req_text_5")
    # A late ASR final must not produce a second answer.
    await listener._on_asr_transcript("迟到的语音识别结果")

    controller.handle_answer.assert_awaited_once_with("req_text_5", "改用打字回答")
    agent.send_asr_final.assert_not_called()


@pytest.mark.asyncio
async def test_short_noise_final_is_not_submitted_while_listening():
    controller = MagicMock()
    controller.state = InterviewState.LISTENING
    controller.current_answer_metadata.return_value = {"interviewId": "iv_test"}
    controller.handle_answer = AsyncMock()

    agent = AsyncMock()
    listener = InterviewListener()
    listener.agent = agent
    listener.set_controller(controller)

    await listener._on_speech_started()
    await listener._on_asr_transcript("嗯")

    agent.send_voice_start.assert_not_called()
    controller.handle_answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_silence_pcm_cannot_create_candidate_answer():
    controller = MagicMock()
    controller.state = InterviewState.LISTENING
    controller.current_answer_metadata.return_value = {
        "interviewId": "iv_test",
        "exchangeId": "ex_001",
    }
    controller.handle_answer = AsyncMock()
    listener = InterviewListener(asr_manager=MagicMock())
    listener.agent = AsyncMock()
    listener.set_controller(controller)
    listener.start_direct_audio_input("ex_001")
    for _ in range(20):
        listener.feed_direct_audio(b"\x00\x00" * 320)

    await listener._on_speech_started()
    await listener._on_asr_transcript("这段文字不是候选人说的")

    listener.agent.send_voice_start.assert_not_awaited()
    controller.handle_answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_asr_final_is_rejected_after_exchange_changes():
    controller = MagicMock()
    controller.state = InterviewState.LISTENING
    controller.current_answer_metadata.return_value = {
        "interviewId": "iv_test",
        "exchangeId": "ex_001",
    }
    controller.handle_answer = AsyncMock()
    listener = InterviewListener(asr_manager=MagicMock())
    listener.agent = AsyncMock()
    listener.set_controller(controller)
    prime_verified_voice(listener, "ex_001")
    await listener._on_speech_started()

    controller.current_answer_metadata.return_value = {
        "interviewId": "iv_test",
        "exchangeId": "ex_002",
    }
    await listener._on_asr_transcript("这是上一题迟到的识别结果")

    listener.agent.send_asr_final.assert_not_awaited()
    controller.handle_answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_scene_ready_notifies_controller():
    controller = MagicMock()
    controller.mark_scene_ready = AsyncMock()
    listener = InterviewListener()
    listener.set_controller(controller)

    await listener.on_scene_ready()

    controller.mark_scene_ready.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_state_forwards_non_idle_states_to_controller():
    controller = MagicMock()
    controller.notify_platform_state = AsyncMock()
    listener = InterviewListener()
    listener.set_controller(controller)

    await listener.on_session_state(SessionState.PROMPT_SPEAKING)

    controller.notify_platform_state.assert_awaited_once_with(SessionState.PROMPT_SPEAKING)
