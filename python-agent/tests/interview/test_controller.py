import logging
from unittest.mock import AsyncMock

import asyncio
import pytest

from interview.controller import InterviewController, PlaybackSignalMapper, PlaybackEvent
from interview.interview_manager import InterviewManager
from interview.models import Evaluation, FollowUpDecision, InterviewState
from interview.question_planner import QuestionPlanner
from interview.report_generator import ReportGenerator
from liveavatar_channel_sdk import SessionState


class StaticEvaluator:
    def __init__(self, evaluations):
        self.evaluations = list(evaluations)

    async def evaluate(self, question_text, answer_text, **kwargs):
        return self.evaluations.pop(0)


class IdleDuringEvaluator:
    def __init__(self, controller, evaluation):
        self.controller = controller
        self.evaluation = evaluation

    async def evaluate(self, question_text, answer_text, **kwargs):
        await self.controller.notify_platform_state(SessionState.PROMPT_SPEAKING)
        await self.controller.notify_platform_state(SessionState.IDLE)
        return self.evaluation


class SlowEvaluator:
    def __init__(self, evaluation):
        self.evaluation = evaluation
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def evaluate(self, question_text, answer_text, **kwargs):
        self.started.set()
        await self.release.wait()
        return self.evaluation


class NeverEvaluator:
    async def evaluate(self, question_text, answer_text, **kwargs):
        await asyncio.Event().wait()


class NoFollowUpDecider:
    def __init__(self):
        self.seen_evaluation = "unset"

    async def decide_async(self, *, question, answer_text, evaluation, transcript, probe_index):
        self.seen_evaluation = evaluation
        return FollowUpDecision(needed=False)


class StaticFollowUpDecider:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    async def decide_async(self, *, question, answer_text, evaluation, transcript, probe_index):
        if self.decisions:
            return self.decisions.pop(0)
        return FollowUpDecision(needed=False)


@pytest.fixture
def controller():
    manager = InterviewManager("config/interview.yaml")
    agent = AsyncMock()
    agent.send_prompt = AsyncMock()
    return InterviewController(
        agent=agent,
        manager=manager,
        planner=QuestionPlanner(manager.get_question_specs()),
        evaluator=StaticEvaluator(
            [
                Evaluation(
                    score=3,
                    dimensions={"depth": 3},
                    follow_up_needed=True,
                    follow_up_question="请具体讲讲幂等设计。",
                ),
                Evaluation(score=4, dimensions={"depth": 4}, follow_up_needed=False),
                Evaluation(
                    score=4,
                    dimensions={"technical_depth": 4},
                    follow_up_needed=False,
                ),
                Evaluation(
                    score=4,
                    dimensions={"collaboration": 4},
                    follow_up_needed=False,
                ),
                *[
                    Evaluation(
                        score=4,
                        dimensions={"technical_depth": 4},
                        follow_up_needed=False,
                    )
                    for _ in range(10)
                ],
            ]
        ),
        report_generator=ReportGenerator(),
        follow_up_decider=StaticFollowUpDecider(
            [
                FollowUpDecision(
                    needed=True,
                    reason="需要确认幂等设计",
                    follow_up_type="deepen",
                    suggested_question="请具体讲讲幂等设计。",
                ),
                FollowUpDecision(needed=False),
                FollowUpDecision(needed=False),
                FollowUpDecision(needed=False),
                *[FollowUpDecision(needed=False) for _ in range(10)],
            ]
        ),
        interview_id="iv_test",
        opening_to_question_delay_seconds=0,
    )


@pytest.fixture
def fast_timeout_controller():
    manager = InterviewManager("config/interview.yaml")
    agent = AsyncMock()
    agent.send_prompt = AsyncMock()
    return InterviewController(
        agent=agent,
        manager=manager,
        planner=QuestionPlanner(manager.get_question_specs()),
        evaluator=StaticEvaluator([]),
        report_generator=ReportGenerator(),
        interview_id="iv_timeout",
        thinking_checks=[(0, "你还在思考吗？")],
        hard_timeout_seconds=0,
        opening_to_question_delay_seconds=0,
    )


async def start_first_question_listening(controller):
    await controller.start()
    await controller.mark_scene_ready()
    await finish_current_prompt(controller)
    await finish_current_prompt(controller)


async def finish_current_prompt(controller):
    await controller.notify_platform_state(SessionState.PROMPT_SPEAKING)
    await controller.notify_platform_state(SessionState.IDLE)


def test_playback_signal_mapper_requires_prompt_and_active_state():
    mapper = PlaybackSignalMapper()

    assert mapper.map_state(SessionState.IDLE) is None
    mapper.mark_prompt_sent()
    assert mapper.map_state(SessionState.IDLE) is None
    assert mapper.map_state(SessionState.PROMPT_SPEAKING) is None
    assert mapper.map_state(SessionState.IDLE) == PlaybackEvent.PROMPT_FINISHED
    assert mapper.map_state(SessionState.IDLE) is None


@pytest.mark.asyncio
async def test_prompt_watchdog_advances_when_platform_idle_is_missing():
    manager = InterviewManager("config/interview.yaml")
    agent = AsyncMock()
    agent.send_prompt = AsyncMock()
    controller = InterviewController(
        agent=agent,
        manager=manager,
        planner=QuestionPlanner(manager.get_question_specs()),
        evaluator=StaticEvaluator([]),
        report_generator=ReportGenerator(),
        interview_id="iv_prompt_watchdog",
        opening_to_question_delay_seconds=0,
        prompt_playback_timeout_seconds=0.01,
    )

    await controller.start()
    await controller.mark_scene_ready()
    await asyncio.sleep(0.02)

    assert controller.state == InterviewState.ASKING
    assert len(agent.send_prompt.await_args_list) == 2


@pytest.mark.asyncio
async def test_start_waits_for_scene_ready_before_speaking(controller):
    await controller.start()

    controller._agent.send_prompt.assert_not_awaited()
    assert controller.state == InterviewState.WAITING_SCENE_READY


@pytest.mark.asyncio
async def test_scene_ready_sends_opening_then_first_question_with_metadata(controller):
    await controller.start()
    await controller.mark_scene_ready()

    prompts = controller._agent.send_prompt.await_args_list
    assert prompts[0].args[0].startswith("你好")
    assert prompts[0].kwargs["metadata"]["promptType"] == "opening"
    assert len(prompts) == 1
    assert controller.state == InterviewState.OPENING

    await finish_current_prompt(controller)

    prompts = controller._agent.send_prompt.await_args_list
    assert prompts[1].args[0].startswith("我们先从第一个问题开始。请介绍")
    metadata = prompts[1].kwargs["metadata"]
    assert metadata["interviewId"] == "iv_test"
    assert metadata["questionId"] == "q_project_deep_dive_001"
    assert metadata["exchangeId"] == "ex_001"
    assert metadata["promptType"] == "main_question"
    assert controller.state == InterviewState.ASKING

    await finish_current_prompt(controller)

    assert controller.state == InterviewState.LISTENING


@pytest.mark.asyncio
async def test_stale_idle_does_not_advance_before_prompt_becomes_active(controller):
    await controller.start()
    await controller.mark_scene_ready()

    await controller.notify_platform_state(SessionState.IDLE)

    assert controller.state == InterviewState.OPENING
    assert len(controller._agent.send_prompt.await_args_list) == 1

    await finish_current_prompt(controller)

    assert controller.state == InterviewState.ASKING
    assert len(controller._agent.send_prompt.await_args_list) == 2


@pytest.mark.asyncio
async def test_question_timeout_starts_only_after_question_prompt_finishes(controller):
    await controller.start()
    await controller.mark_scene_ready()
    await finish_current_prompt(controller)

    assert controller.state == InterviewState.ASKING
    assert controller._timeout_task is None

    await controller.notify_platform_state(SessionState.IDLE)

    assert controller.state == InterviewState.ASKING
    assert controller._timeout_task is None

    await controller.notify_platform_state(SessionState.PROMPT_SPEAKING)
    await controller.notify_platform_state(SessionState.IDLE)

    assert controller.state == InterviewState.LISTENING
    assert controller._timeout_task is not None


@pytest.mark.asyncio
async def test_answer_can_trigger_one_follow_up(controller):
    await start_first_question_listening(controller)

    await controller.handle_answer("req_a1", "我做了订单系统")

    acknowledgement = controller._agent.send_prompt.await_args_list[-1]
    assert acknowledgement.args[0] in controller._ANSWER_ACKNOWLEDGEMENTS
    assert acknowledgement.args[0] != "明白。我稍微整理一下，我们马上继续。"
    assert controller.state == InterviewState.PLANNING_FOLLOWUP

    await finish_current_prompt(controller)

    follow_up = controller._agent.send_prompt.await_args_list[-1]
    assert any(
        follow_up.args[0] == f"{prefix}请具体讲讲幂等设计。"
        for prefix in controller._FOLLOW_UP_PREFIXES
    )
    assert not follow_up.args[0].startswith("我们基于你刚才的回答继续深入探讨一下。")
    assert follow_up.kwargs["metadata"]["promptType"] == "follow_up"
    assert controller.state == InterviewState.ASKING

    await finish_current_prompt(controller)

    assert controller.state == InterviewState.LISTENING


@pytest.mark.asyncio
async def test_answer_logs_follow_up_decision_timing(controller, caplog):
    await start_first_question_listening(controller)

    with caplog.at_level(logging.INFO, logger="interview.controller"):
        await controller.handle_answer("req_a1", "我做了订单系统")

    assert "interview answer timing:" in caplog.text
    assert "follow_up_needed=True" in caplog.text
    assert "evaluation_ms=" in caplog.text
    assert "followup_decision_ms=" in caplog.text
    assert "followup_planning_ms=" in caplog.text
    assert "total_ms=" in caplog.text


@pytest.mark.asyncio
async def test_second_answer_closes_question_and_moves_next(controller):
    await start_first_question_listening(controller)
    await controller.handle_answer("req_a1", "我做了订单系统")
    await finish_current_prompt(controller)
    await finish_current_prompt(controller)
    await controller.handle_answer("req_a2", "用业务唯一键保证幂等")

    status = controller.get_status()
    assert status["state"] == "transitioning"
    assert status["questionsCompleted"] == 1

    await finish_current_prompt(controller)
    status = controller.get_status()

    assert status["currentQuestion"]["questionId"] == "q_python_backend_001"
    assert status["state"] == "asking"
    assert status["questionsCompleted"] == 1
    assert status["candidateMessage"]
    assert 0 <= status["progressPercent"] <= 100


@pytest.mark.asyncio
async def test_answer_does_not_stall_when_ack_idle_arrives_during_analysis():
    manager = InterviewManager("config/interview.yaml")
    agent = AsyncMock()
    agent.send_prompt = AsyncMock()
    controller = InterviewController(
        agent=agent,
        manager=manager,
        planner=QuestionPlanner(manager.get_question_specs()),
        evaluator=None,
        report_generator=ReportGenerator(),
        follow_up_decider=NoFollowUpDecider(),
        interview_id="iv_ack_idle",
        opening_to_question_delay_seconds=0,
    )
    controller._evaluator = IdleDuringEvaluator(
        controller,
        Evaluation(score=4, dimensions={"technical_depth": 4}, follow_up_needed=False),
    )

    await start_first_question_listening(controller)
    await controller.handle_answer("req_a1", "我做了订单系统")
    await asyncio.sleep(0)

    status = controller.get_status()
    assert status["state"] == "asking"
    assert status["currentQuestion"]["questionId"] == "q_python_backend_001"


@pytest.mark.asyncio
async def test_follow_up_decision_does_not_wait_for_slow_evaluation():
    manager = InterviewManager("config/interview.yaml")
    agent = AsyncMock()
    agent.send_prompt = AsyncMock()
    evaluator = SlowEvaluator(
        Evaluation(score=4, dimensions={"technical_depth": 4}, follow_up_needed=False)
    )
    decider = NoFollowUpDecider()
    controller = InterviewController(
        agent=agent,
        manager=manager,
        planner=QuestionPlanner(manager.get_question_specs()),
        evaluator=evaluator,
        report_generator=ReportGenerator(),
        follow_up_decider=decider,
        interview_id="iv_async_eval",
        opening_to_question_delay_seconds=0,
    )

    await start_first_question_listening(controller)

    await asyncio.wait_for(controller.handle_answer("req_a1", "我做了订单系统"), timeout=0.2)
    await asyncio.sleep(0)

    assert evaluator.started.is_set()
    assert decider.seen_evaluation is None
    assert controller.get_status()["state"] == "transitioning"
    assert controller._exchanges[0].evaluation is None

    evaluator.release.set()
    await asyncio.wait_for(controller._await_pending_evaluations(), timeout=0.2)
    assert controller._exchanges[0].evaluation is not None


@pytest.mark.asyncio
async def test_stop_does_not_hang_forever_waiting_for_background_evaluation():
    manager = InterviewManager("config/interview.yaml")
    agent = AsyncMock()
    agent.send_prompt = AsyncMock()
    controller = InterviewController(
        agent=agent,
        manager=manager,
        planner=QuestionPlanner(manager.get_question_specs()),
        evaluator=NeverEvaluator(),
        report_generator=ReportGenerator(),
        follow_up_decider=NoFollowUpDecider(),
        interview_id="iv_eval_timeout",
        opening_to_question_delay_seconds=0,
        evaluation_join_timeout_seconds=0,
    )

    await start_first_question_listening(controller)
    await controller.handle_answer("req_a1", "我做了订单系统")

    await asyncio.wait_for(controller.stop(), timeout=0.2)

    assert controller.state == InterviewState.TERMINATED
    assert controller.get_status()["finalReport"] is not None


@pytest.mark.asyncio
async def test_stop_waits_for_background_evaluation_before_report():
    manager = InterviewManager("config/interview.yaml")
    agent = AsyncMock()
    agent.send_prompt = AsyncMock()
    evaluator = SlowEvaluator(
        Evaluation(score=4, dimensions={"technical_depth": 4}, follow_up_needed=False)
    )
    controller = InterviewController(
        agent=agent,
        manager=manager,
        planner=QuestionPlanner(manager.get_question_specs()),
        evaluator=evaluator,
        report_generator=ReportGenerator(),
        follow_up_decider=NoFollowUpDecider(),
        interview_id="iv_stop_eval",
        opening_to_question_delay_seconds=0,
    )

    await start_first_question_listening(controller)
    await controller.handle_answer("req_a1", "我做了订单系统")

    stop_task = asyncio.create_task(controller.stop())
    await asyncio.sleep(0)

    assert not stop_task.done()

    evaluator.release.set()
    await asyncio.wait_for(stop_task, timeout=0.2)

    assert controller._exchanges[0].evaluation is not None
    assert controller.get_status()["finalReport"] is not None


@pytest.mark.asyncio
async def test_last_answer_queues_closing_until_ack_prompt_finishes():
    manager = InterviewManager("config/interview.yaml")
    agent = AsyncMock()
    agent.send_prompt = AsyncMock()
    controller = InterviewController(
        agent=agent,
        manager=manager,
        planner=QuestionPlanner(manager.get_question_specs()[:1]),
        evaluator=StaticEvaluator(
            [Evaluation(score=4, dimensions={"technical_depth": 4}, follow_up_needed=False)]
        ),
        report_generator=ReportGenerator(),
        follow_up_decider=NoFollowUpDecider(),
        interview_id="iv_last_ack",
        opening_to_question_delay_seconds=0,
    )

    await start_first_question_listening(controller)
    await controller.handle_answer("req_a1", "我做了订单系统")

    prompts = agent.send_prompt.await_args_list
    assert prompts[-1].args[0] in controller._FINAL_ANSWER_ACKNOWLEDGEMENTS
    assert controller.state == InterviewState.TRANSITIONING

    await finish_current_prompt(controller)

    assert agent.send_prompt.await_args_list[-1].args[0].startswith("今天的模拟面试")
    assert controller.state == InterviewState.CLOSING


@pytest.mark.asyncio
async def test_completed_interview_notifies_terminal_callback_after_closing_prompt():
    manager = InterviewManager("config/interview.yaml")
    agent = AsyncMock()
    agent.send_prompt = AsyncMock()
    terminal_events = []

    async def on_terminal(state):
        terminal_events.append(state)

    controller = InterviewController(
        agent=agent,
        manager=manager,
        planner=QuestionPlanner(manager.get_question_specs()[:1]),
        evaluator=StaticEvaluator(
            [Evaluation(score=4, dimensions={"technical_depth": 4}, follow_up_needed=False)]
        ),
        report_generator=ReportGenerator(),
        follow_up_decider=NoFollowUpDecider(),
        interview_id="iv_auto_close",
        opening_to_question_delay_seconds=0,
        on_terminal=on_terminal,
    )

    await start_first_question_listening(controller)
    await controller.handle_answer("req_a1", "我做了订单系统")
    await finish_current_prompt(controller)

    assert controller.state == InterviewState.CLOSING
    assert terminal_events == []

    await finish_current_prompt(controller)

    assert controller.state == InterviewState.COMPLETED
    assert terminal_events == [InterviewState.COMPLETED]


@pytest.mark.asyncio
async def test_transcript_records_opening_question_and_answer(controller):
    await start_first_question_listening(controller)
    await controller.handle_answer("req_a1", "我做了订单系统")

    status = controller.get_status()
    turn_types = [turn["type"] for turn in status["transcript"]]

    assert turn_types[:3] == ["opening", "main_question", "answer"]
    assert status["transcript"][1]["questionId"] == "q_project_deep_dive_001"
    assert status["transcript"][2]["text"] == "我做了订单系统"


@pytest.mark.asyncio
async def test_hard_timeout_skips_question_when_more_questions_remain(controller):
    await start_first_question_listening(controller)

    await controller.handle_exchange_timeout()

    status = controller.get_status()
    assert status["state"] == "skipping_question"
    assert status["currentQuestion"]["questionId"] == "q_project_deep_dive_001"
    assert status["skippedQuestions"] == 1
    assert any(turn["type"] == "question_skipped" for turn in status["transcript"])

    await finish_current_prompt(controller)

    status = controller.get_status()
    assert status["state"] == "asking"
    assert status["currentQuestion"]["questionId"] == "q_python_backend_001"
    assert status["skippedQuestions"] == 1


@pytest.mark.asyncio
async def test_timeout_task_sends_thinking_check_then_skips(fast_timeout_controller):
    await start_first_question_listening(fast_timeout_controller)

    for _ in range(4):
        await asyncio.sleep(0)

    status = fast_timeout_controller.get_status()
    turn_types = [turn["type"] for turn in status["transcript"]]
    assert "thinking_check" in turn_types
    assert "question_skipped" not in turn_types
    assert status["state"] == "thinking_check"

    await finish_current_prompt(fast_timeout_controller)
    for _ in range(4):
        await asyncio.sleep(0)

    status = fast_timeout_controller.get_status()
    turn_types = [turn["type"] for turn in status["transcript"]]
    assert "question_skipped" in turn_types


@pytest.mark.asyncio
async def test_repeated_timeouts_terminate_interview(controller):
    await start_first_question_listening(controller)

    await controller.handle_exchange_timeout()
    await finish_current_prompt(controller)
    await finish_current_prompt(controller)
    await controller.handle_exchange_timeout()

    status = controller.get_status()
    assert status["state"] == "closing"
    assert status["terminationReason"] == "too_many_no_answer_timeouts"

    await finish_current_prompt(controller)

    status = controller.get_status()
    assert status["state"] == "terminated"


@pytest.mark.asyncio
async def test_completed_report_contains_dimension_scores(controller):
    await start_first_question_listening(controller)
    for index in range(12):
        if controller.get_status()["state"] == "completed":
            break
        await controller.handle_answer(
            f"req_a{index}",
            "我会结合项目背景、技术难点、取舍和最终结果来回答。",
        )
        if controller.get_status()["state"] == "transitioning":
            await finish_current_prompt(controller)
        if controller.get_status()["state"] == "planning_followup":
            await finish_current_prompt(controller)
        if controller.get_status()["state"] == "asking":
            await finish_current_prompt(controller)

    status = controller.get_status()

    assert status["state"] == "closing"
    assert "technical_depth" in status["finalReport"]["dimensions"]

    await finish_current_prompt(controller)

    assert controller.get_status()["state"] == "completed"


@pytest.mark.asyncio
async def test_stop_generates_partial_report_and_clears_active_timer(controller):
    await start_first_question_listening(controller)

    await controller.stop()

    status = controller.get_status()
    assert status["state"] == "terminated"
    assert status["terminationReason"] == "user_stopped"
    assert status["finalReport"] is not None


@pytest.mark.asyncio
async def test_answer_during_thinking_check_is_accepted_and_cancels_timeout(controller):
    await start_first_question_listening(controller)
    exchange_id = controller.get_status()["currentExchange"]["exchangeId"]
    controller._state = InterviewState.THINKING_CHECK

    await controller.handle_answer("req_during_check", "我正在回答这个问题")

    status = controller.get_status()
    assert any(
        turn["type"] == "answer"
        and turn["exchangeId"] == exchange_id
        and turn["text"] == "我正在回答这个问题"
        for turn in status["transcript"]
    )
    assert status["state"] == "planning_followup"
    await finish_current_prompt(controller)
    assert controller.get_status()["state"] == "asking"
    assert all(
        turn["type"] != "question_skipped" or turn["exchangeId"] != exchange_id
        for turn in status["transcript"]
    )


@pytest.mark.asyncio
async def test_candidate_speech_without_final_resumes_timeout():
    manager = InterviewManager("config/interview.yaml")
    agent = AsyncMock()
    agent.send_prompt = AsyncMock()
    controller = InterviewController(
        agent=agent,
        manager=manager,
        planner=QuestionPlanner(manager.get_question_specs()),
        evaluator=StaticEvaluator([]),
        report_generator=ReportGenerator(),
        interview_id="iv_speech_no_final",
        thinking_checks=[(0, "你还在思考吗？")],
        hard_timeout_seconds=0,
        candidate_speech_grace_seconds=0,
        opening_to_question_delay_seconds=0,
    )

    await start_first_question_listening(controller)
    await controller.mark_candidate_speaking()
    for _ in range(5):
        await asyncio.sleep(0)

    assert controller.state == InterviewState.THINKING_CHECK


@pytest.mark.asyncio
async def test_opening_idle_waits_before_first_question(monkeypatch):
    manager = InterviewManager("config/interview.yaml")
    agent = AsyncMock()
    agent.send_prompt = AsyncMock()
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("interview.controller.asyncio.sleep", fake_sleep)
    controller = InterviewController(
        agent=agent,
        manager=manager,
        planner=QuestionPlanner(manager.get_question_specs()),
        evaluator=StaticEvaluator([]),
        report_generator=ReportGenerator(),
        interview_id="iv_delay",
        opening_to_question_delay_seconds=1.2,
    )

    await controller.start()
    await controller.mark_scene_ready()
    await finish_current_prompt(controller)

    assert sleeps == [1.2]
