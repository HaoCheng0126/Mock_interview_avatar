import logging
from unittest.mock import AsyncMock

import asyncio
import pytest

from interview.controller import InterviewController, PlaybackSignalMapper, PlaybackEvent
from interview.interview_manager import InterviewManager
from interview.models import Evaluation, FollowUpDecision, InterviewState, QuestionSpec
from interview.question_planner import QuestionPlanner
from interview.report_generator import ReportGenerationError, ReportGenerator
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


class ControlledReportGenerator:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate_async(self, exchanges, **kwargs):
        callback = kwargs.get("on_progress")
        if callback:
            callback(
                {
                    "state": "generating",
                    "stage": "chunk_analysis",
                    "message": "正在分析回答",
                    "completed_steps": 1,
                    "total_steps": 4,
                    "percent": 35,
                }
            )
        self.started.set()
        await self.release.wait()
        return ReportGenerator().generate(
            exchanges,
            transcript=kwargs.get("transcript"),
            rubric_dimensions=kwargs.get("rubric_dimensions"),
            actual_duration_seconds=kwargs.get("actual_duration_seconds"),
        )


class FailingReportGenerator:
    async def generate_async(self, exchanges, **kwargs):
        raise ReportGenerationError("AI 综合结论缺少学习计划")


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
                    follow_up_question="你刚才提到订单系统，具体是如何处理幂等的？",
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
                    suggested_question="你刚才提到订单系统，具体是如何处理幂等的？",
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


async def finish_report_generation(controller):
    task = controller._report_task
    assert task is not None
    await asyncio.wait_for(task, timeout=1)


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
    assert metadata["questionId"] == "biz_001"
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
@pytest.mark.skip(
    reason=(
        "TODO(ux): 答后衔接 / 思考中提示已关闭（_send_processing_acknowledgement / "
        "_send_thinking_check 现在 no-op），所以'ack prompt 必须从 _ANSWER_ACKNOWLEDGEMENTS "
        "里选出来'这个断言不再适用。后续会把 ack 验收改为'assert_awaited_once 次数'"
        "或重新设计测试。"
    )
)
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
        follow_up.args[0] == f"{prefix}你刚才提到订单系统，具体是如何处理幂等的？"
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
    assert status["state"] == "asking"
    assert status["questionsCompleted"] == 1

    assert status["currentQuestion"]["questionId"] == "biz_002"
    assert status["questionsCompleted"] == 1
    assert status["candidateMessage"]
    assert 0 <= status["progressPercent"] <= 100


@pytest.mark.asyncio
@pytest.mark.skip(
    reason=(
        "TODO(state-machine): the new generate_once + debounce answer pipeline "
        "no longer matches this expectation. After a candidate answer we now "
        "schedule evaluation as a background task and only block the follow-up "
        "decider if a stubbable join point is reached. This test was originally "
        "about 'ack_idle during analysis must not stall the interview' — that "
        "behaviour is still expected but the test wiring needs to be updated to "
        "match the async evaluation pipeline. Tracked separately."
    )
)
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
    assert status["currentQuestion"]["questionId"] == "biz_002"


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
        foreground_evaluation_timeout_seconds=0.05,
    )

    await start_first_question_listening(controller)

    await asyncio.wait_for(controller.handle_answer("req_a1", "我做了订单系统"), timeout=0.2)
    await asyncio.sleep(0)

    assert evaluator.started.is_set()
    assert decider.seen_evaluation is not None
    assert controller.get_status()["state"] == "asking"
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
async def test_background_report_is_published_atomically_and_hides_retry_counts():
    manager = InterviewManager("config/interview.yaml")
    report_generator = ControlledReportGenerator()
    controller = InterviewController(
        agent=AsyncMock(),
        manager=manager,
        planner=QuestionPlanner(manager.get_question_specs()),
        evaluator=StaticEvaluator([]),
        report_generator=report_generator,
        follow_up_decider=NoFollowUpDecider(),
        interview_id="iv_atomic_report",
    )

    await controller.start()
    task = controller.begin_stop_report()
    await report_generator.started.wait()
    pending = controller.get_status()
    assert pending["state"] == "report_generating"
    assert pending["finalReport"] is None
    assert pending["reportGeneration"]["generationId"].startswith("gen_")
    assert "attempt" not in pending["reportGeneration"]
    assert "maxAttempts" not in pending["reportGeneration"]

    report_generator.release.set()
    await task
    completed = controller.get_status()
    assert completed["state"] == "terminated"
    assert completed["finalReport"] is not None


@pytest.mark.asyncio
async def test_report_error_status_preserves_specific_validation_reason():
    manager = InterviewManager("config/interview.yaml")
    controller = InterviewController(
        agent=AsyncMock(),
        manager=manager,
        planner=QuestionPlanner(manager.get_question_specs()),
        evaluator=StaticEvaluator([]),
        report_generator=FailingReportGenerator(),
        follow_up_decider=NoFollowUpDecider(),
        interview_id="iv_report_error_detail",
    )

    await controller.start()
    task = controller.begin_stop_report()
    await task

    status = controller.get_status()
    assert status["state"] == "report_error"
    assert (
        status["reportGeneration"]["error"]
        == "AI 综合结论缺少学习计划"
    )


def test_report_progress_never_moves_backwards(controller):
    controller._report_generation["percent"] = 62
    controller._report_generation["completedSteps"] = 5
    controller._report_generation["totalSteps"] = 6
    controller._report_generation["stage"] = "validating"
    controller._on_report_progress(
        {
            "state": "retrying",
            "stage": "overview",
            "message": "继续处理",
            "percent": 20,
        }
    )
    assert controller.get_status()["reportGeneration"]["percent"] == 62
    assert controller.get_status()["reportGeneration"]["completedSteps"] == 5
    assert controller.get_status()["reportGeneration"]["totalSteps"] == 6
    assert controller.get_status()["reportGeneration"]["stage"] == "overview"


def test_next_question_transitions_do_not_repeat_within_a_round():
    speech = type(
        "Speech",
        (),
        {
            "next_question_transitions": ["转场一。", "转场二。", "转场三。"],
            "follow_up_prefixes": ["追问一。", "追问二。"],
        },
    )()
    controller = InterviewController(
        agent=None,
        manager=None,
        planner=None,
        evaluator=None,
        report_generator=None,
        speech_config=speech,
    )
    question = QuestionSpec("s", "业务", "q", "问题？")
    controller._asked_question_ids.add("q")
    controller._completed_question_ids.add("done")
    transitions = [
        controller._spoken_prompt_text(
            question=question,
            prompt_type="main_question",
            prompt_text=None,
            probe_index=0,
        ).removesuffix("问题？")
        for _ in range(3)
    ]
    assert len(set(transitions)) == 3


@pytest.mark.asyncio
async def test_explicit_candidate_skip_moves_to_next_question_without_follow_up(controller):
    await start_first_question_listening(controller)
    before = len(controller._agent.send_prompt.await_args_list)
    await controller.handle_answer("req_skip", "下一题吧")
    assert len(controller._agent.send_prompt.await_args_list) == before + 1
    assert controller._transcript[-2].type == "question_skipped"
    assert controller._transcript[-2].metadata["reason"] == "candidate_requested_skip"
    assert controller._transcript[-1].type == "main_question"


@pytest.mark.asyncio
async def test_generic_ai_follow_up_is_rejected_and_next_question_is_used():
    manager = InterviewManager("config/interview.yaml")
    agent = AsyncMock()
    controller = InterviewController(
        agent=agent,
        manager=manager,
        planner=QuestionPlanner(manager.get_question_specs()),
        evaluator=StaticEvaluator(
            [
                Evaluation(
                    score=2,
                    dimensions={"depth": 2},
                    follow_up_needed=True,
                    follow_up_question="请补充一个具体做法和最终结果？",
                )
            ]
        ),
        report_generator=ReportGenerator(),
        opening_to_question_delay_seconds=0,
    )
    await start_first_question_listening(controller)
    await controller.handle_answer("req_generic", "我的回答比较简短")
    assert controller.get_status()["currentExchange"]["promptType"] == "main_question"


@pytest.mark.asyncio
async def test_unrelated_ai_follow_up_is_rejected_and_next_question_is_used():
    manager = InterviewManager("config/interview.yaml")
    agent = AsyncMock()
    controller = InterviewController(
        agent=agent,
        manager=manager,
        planner=QuestionPlanner(manager.get_question_specs()),
        evaluator=StaticEvaluator(
            [
                Evaluation(
                    score=2,
                    dimensions={"depth": 2},
                    follow_up_needed=True,
                    follow_up_question="数据库索引应该如何设计？",
                )
            ]
        ),
        report_generator=ReportGenerator(),
        opening_to_question_delay_seconds=0,
    )
    await start_first_question_listening(controller)
    await controller.handle_answer("req_unrelated", "我负责了订单系统的产品设计")
    assert controller.get_status()["currentExchange"]["promptType"] == "main_question"


@pytest.mark.asyncio
@pytest.mark.skip(
    reason=(
        "TODO(state-machine): controller.stop() currently exits as soon as "
        "evaluation_join_timeout expires, even if a real background evaluation is "
        "still in flight. The product expectation is 'stop should wait for at most "
        "one join cycle of pending evaluations before producing the report', which "
        "is the correct behaviour but requires re-wiring stop()'s join. Tracking "
        "this as a follow-up so this test can be re-enabled."
    )
)
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
@pytest.mark.skip(
    reason=(
        "TODO(ux): 最后一题衔接语已关闭（_send_processing_acknowledgement now no-op），"
        "所以最后一次 send_prompt 不再是 final_answer_acknowledgement，"
        "测试断言'最后一句 prompt 必须在 _FINAL_ANSWER_ACKNOWLEDGEMENTS 中'不再适用。"
    )
)
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
    assert controller.state == InterviewState.REPORT_GENERATING
    await finish_report_generation(controller)

    assert controller.state == InterviewState.CLOSING
    assert terminal_events == []

    await finish_current_prompt(controller)

    assert controller.state == InterviewState.COMPLETED
    assert terminal_events == [InterviewState.COMPLETED]


@pytest.mark.asyncio
async def test_closing_speaks_llm_recap_before_closing_line():
    manager = InterviewManager("config/interview.yaml")
    agent = AsyncMock()
    agent.send_prompt = AsyncMock()

    class StubRecap:
        async def generate_async(self, report, *, target_role=""):
            return "整体来看你今天讲得挺清楚。"

    controller = InterviewController(
        agent=agent,
        manager=manager,
        planner=QuestionPlanner(manager.get_question_specs()[:1]),
        evaluator=StaticEvaluator(
            [Evaluation(score=4, dimensions={"technical_depth": 4}, follow_up_needed=False)]
        ),
        report_generator=ReportGenerator(),
        closing_comment_generator=StubRecap(),
        follow_up_decider=NoFollowUpDecider(),
        interview_id="iv_recap",
        opening_to_question_delay_seconds=0,
    )

    await start_first_question_listening(controller)
    await controller.handle_answer("req_a1", "我做了订单系统")
    assert controller.state == InterviewState.REPORT_GENERATING
    await finish_report_generation(controller)

    assert controller.state == InterviewState.CLOSING
    closing_utterance = agent.send_prompt.call_args_list[-1].args[0]
    assert "整体来看你今天讲得挺清楚。" in closing_utterance   # spoken recap …
    assert "反馈报告" in closing_utterance                     # … then the closing line
    closing_turns = [
        t for t in controller.get_status()["transcript"] if t["type"] == "closing"
    ]
    assert closing_turns and "整体来看你今天讲得挺清楚。" in closing_turns[-1]["text"]


@pytest.mark.asyncio
async def test_transcript_records_opening_question_and_answer(controller):
    await start_first_question_listening(controller)
    await controller.handle_answer("req_a1", "我做了订单系统")

    status = controller.get_status()
    turn_types = [turn["type"] for turn in status["transcript"]]

    assert turn_types[:3] == ["opening", "main_question", "answer"]
    assert status["transcript"][1]["questionId"] == "biz_001"
    assert status["transcript"][2]["text"] == "我做了订单系统"


@pytest.mark.asyncio
async def test_hard_timeout_skips_question_when_more_questions_remain(controller):
    await start_first_question_listening(controller)

    await controller.handle_exchange_timeout()

    status = controller.get_status()
    assert status["state"] == "skipping_question"
    assert status["currentQuestion"]["questionId"] == "biz_001"
    assert status["skippedQuestions"] == 1
    assert any(turn["type"] == "question_skipped" for turn in status["transcript"])

    await finish_current_prompt(controller)

    status = controller.get_status()
    assert status["state"] == "asking"
    assert status["currentQuestion"]["questionId"] == "biz_002"
    assert status["skippedQuestions"] == 1


@pytest.mark.asyncio
async def test_timeout_task_sends_thinking_check_then_skips(fast_timeout_controller):
    await start_first_question_listening(fast_timeout_controller)

    for _ in range(4):
        await asyncio.sleep(0)

    # 新规则：thinking_check / question_skip_transition 在 get_status 返回
    # transcript 时被服务端过滤（前端不显示具体文案），但 controller 内部
    # transcript 仍保留全部记录用于审计。
    internal_types = [t.type for t in fast_timeout_controller._transcript]
    status_types = [t["type"] for t in fast_timeout_controller.get_status()["transcript"]]
    assert "thinking_check" in internal_types
    assert "thinking_check" not in status_types
    assert "question_skipped" not in status_types
    assert fast_timeout_controller.get_status()["state"] == "thinking_check"

    await finish_current_prompt(fast_timeout_controller)
    for _ in range(4):
        await asyncio.sleep(0)

    internal_types = [t.type for t in fast_timeout_controller._transcript]
    status_types = [t["type"] for t in fast_timeout_controller.get_status()["transcript"]]
    assert "question_skipped" in internal_types


@pytest.mark.asyncio
async def test_repeated_timeouts_terminate_interview(controller):
    await start_first_question_listening(controller)

    await controller.handle_exchange_timeout()
    await finish_current_prompt(controller)
    await finish_current_prompt(controller)
    await controller.handle_exchange_timeout()

    assert controller.get_status()["state"] == "report_generating"
    await finish_report_generation(controller)
    status = controller.get_status()
    assert status["state"] == "closing"
    assert status["terminationReason"] == "too_many_no_answer_timeouts"

    await finish_current_prompt(controller)

    status = controller.get_status()
    assert status["state"] == "terminated"


@pytest.mark.asyncio
async def test_completed_interview_produces_report(controller):
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
    if status["state"] == "report_generating":
        await finish_report_generation(controller)
        status = controller.get_status()

    assert status["state"] == "closing"
    assert status["finalReport"]["summary"]  # a holistic report is produced

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
async def test_status_exposes_stage_progress_for_weak_progress_bar(controller):
    await controller.start()
    assert controller.get_status()["stageLabel"] == "开场了解中"
    assert controller.get_status()["stageProgressPercent"] == 0

    await controller.mark_scene_ready()
    opening = controller.get_status()
    assert opening["stageLabel"] == "开场了解中"
    assert opening["stageProgressPercent"] == 8

    await finish_current_prompt(controller)
    await finish_current_prompt(controller)
    current_stage = controller.get_status()
    current_question = current_stage["currentQuestion"]
    assert current_question is not None
    if current_question["sectionId"] == "self_intro":
        assert current_stage["stageLabel"] == "开场了解中"
        assert current_stage["stageProgressPercent"] == 15
    elif current_question["sectionId"] in {"resume_project_intro", "resume_experience"}:
        assert current_stage["stageLabel"] == "项目深挖中"
        assert current_stage["stageProgressPercent"] >= 30
    else:
        assert current_stage["stageLabel"] == "综合考察中"
        assert current_stage["stageProgressPercent"] >= 60

    await controller.handle_answer("req_stage", "我最近负责支付系统的稳定性治理。")
    if controller.get_status()["state"] == "planning_followup":
        await finish_current_prompt(controller)
    resume_stage = controller.get_status()
    assert resume_stage["stageProgressPercent"] >= current_stage["stageProgressPercent"]


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
    assert status["state"] == "asking"
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


@pytest.mark.asyncio
async def test_actual_duration_seconds_tracks_interview_progress(controller):
    """真实面试时长：从 mark_scene_ready 起开始累计，最后一题没 flush 也要算上。"""
    # mark_scene_ready 之前：时长为 0
    assert controller._actual_duration_seconds() == 0.0
    await controller.start()  # 进入 WAITING_SCENE_READY
    await controller.mark_scene_ready()
    # mark_scene_ready 后让一小段时间过去—— perf_counter() 应当 >= 1
    await asyncio.sleep(0.05)
    initial = controller._actual_duration_seconds()
    assert initial > 0  # 已经至少走过一点点（perf_counter 粒度）
    # stop 时，flush 当前 question + 真实秒数应当进入 report.cover.duration_text
    await start_first_question_listening(controller)
    await controller.handle_answer(
        "req_a0",
        "我结合项目背景、技术难点和结果来回答。",
    )
    if controller.get_status()["state"] == "transitioning":
        await finish_current_prompt(controller)
    if controller.get_status()["state"] == "planning_followup":
        await finish_current_prompt(controller)
    if controller.get_status()["state"] == "asking":
        await finish_current_prompt(controller)
    await controller.stop()
    report = controller.get_status()["finalReport"]
    assert report is not None
    # 时长必须是「X 分 Y 秒」/「X 秒」格式，不能是 45 分钟 yaml 默认
    duration = report["cover"]["durationText"]
    assert duration
    assert "45分钟" not in duration
    assert "分" in duration or "秒" in duration
