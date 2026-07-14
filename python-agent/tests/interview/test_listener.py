from unittest.mock import AsyncMock, MagicMock

import pytest
from liveavatar_channel_sdk import SessionState

from interview.listener import InterviewListener
from interview.models import InterviewState


@pytest.mark.asyncio
async def test_audio_frames_are_ignored_until_audio_input_enabled():
    asr_manager = MagicMock()
    frame = MagicMock()
    frame.payload = b"pcm"
    listener = InterviewListener(asr_manager=asr_manager)

    await listener.on_audio_frame(frame)

    asr_manager.feed_audio.assert_not_called()

    listener.set_audio_input_enabled(True)
    await listener.on_audio_frame(frame)

    asr_manager.feed_audio.assert_called_once_with(b"pcm")


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

    await listener._on_speech_started()
    await listener._on_asr_interim("hello")
    await listener._on_speech_stopped()
    await listener._on_asr_transcript("hello world")

    start_metadata = agent.send_voice_start.await_args.kwargs["metadata"]
    final_metadata = agent.send_asr_final.await_args.kwargs["metadata"]
    assert start_metadata["exchangeId"] == "ex_001"
    assert final_metadata["answerId"] == "ans_001"
    controller.handle_answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_ignores_speech_when_not_listening():
    controller = MagicMock()
    controller.state = InterviewState.ASKING
    agent = AsyncMock()
    listener = InterviewListener()
    listener.agent = agent
    listener.set_controller(controller)

    await listener._on_speech_started()

    agent.send_voice_start.assert_not_called()


@pytest.mark.asyncio
async def test_accepts_speech_during_thinking_check():
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

    agent.send_voice_start.assert_awaited_once()
    controller.handle_answer.assert_awaited_once()


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
    controller.mark_candidate_speaking = AsyncMock()
    controller.mark_candidate_speech_stopped = AsyncMock()

    agent = AsyncMock()
    listener = InterviewListener()
    listener.agent = agent
    listener.set_controller(controller)

    await listener._on_speech_started()
    await listener._on_speech_stopped()

    controller.mark_candidate_speaking.assert_awaited_once()
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
async def test_text_input_accepted_during_thinking_check():
    controller = MagicMock()
    controller.state = InterviewState.THINKING_CHECK
    controller.handle_answer = AsyncMock()
    listener = InterviewListener()
    listener.set_controller(controller)

    await listener.on_text_input("补充一下我的思路", "req_text_2")

    controller.handle_answer.assert_awaited_once()


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
