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
