"""Tests for teaching_controller.py — state machine and lecture loop."""

import asyncio
import time
from pathlib import Path
import pytest
from teaching.teaching_controller import TeachingController, TeachingState


class FakeAgent:
    def __init__(self):
        self.prompts: list[str] = []
        self.custom_events: list[dict] = []

    async def send_prompt(self, text: str) -> None:
        self.prompts.append(text)

    async def send_custom_event(self, request_id, event, data) -> None:
        self.custom_events.append({"event": event, "data": data})

    async def send_interrupt(self) -> None:
        pass

    async def send_response_cancel(self, response_id: str) -> None:
        pass


class FakeCourseManager:
    def __init__(self):
        self._chapters = [
            {"id": "ch1", "title": "第一章", "skeleton": ["要点1", "要点2"],
             "visual": {"type": "card", "ref": "card1"},
             "interaction": {"prompt": "你觉得呢？", "expect_keywords": ["觉得"]}},
            {"id": "ch2", "title": "第二章", "skeleton": ["测验前讲解"],
             "quiz": {"question": "选哪个？", "options": [
                 {"key": "A", "text": "对", "correct": True},
                 {"key": "B", "text": "错", "correct": False}],
                 "explanation_correct": "对了！", "explanation_wrong": "不对哦"}},
            {"id": "ch3", "title": "第三章（纯讲解）", "skeleton": ["最后的内容"]},
        ]

    def get_course(self):
        return {"title": "测试课", "lang": "zh", "default_tts_speed": 0.9}

    def get_chapter(self, cid):
        for ch in self._chapters:
            if ch["id"] == cid:
                return ch
        raise ValueError(f"Chapter '{cid}' not found")

    def get_first_chapter(self):
        return self._chapters[0]

    def get_next_chapter(self, cid):
        for i, ch in enumerate(self._chapters):
            if ch["id"] == cid:
                return self._chapters[i + 1] if i + 1 < len(self._chapters) else None
        raise ValueError(f"Chapter '{cid}' not found")

    def get_chapter_count(self):
        return len(self._chapters)

    def get_chapter_by_index(self, index):
        if 0 <= index < len(self._chapters):
            return self._chapters[index]
        return None

    def get_cards(self):
        return [{"id": "card1", "title": "卡片", "content": "内容"}]


class FakeLlmClient:
    def __init__(self):
        self.generate_calls: list[str] = []
        self._response = "润色后的文本"
        self._system_prompt = ""
        self._messages = []
        self._model = "test-model"
        # Non-streaming _client mock. Uses side_effect to return different
        # responses for sequential calls (classifier → answer LLM).
        from unittest.mock import AsyncMock, MagicMock
        self._client = MagicMock()
        self._client_create = AsyncMock()
        self._client_create.return_value = self._make_chat_response(self._response)
        self._client.chat.completions.create = self._client_create

    @staticmethod
    def _make_chat_response(content: str, finish_reason: str = "stop"):
        from unittest.mock import MagicMock
        choice = MagicMock()
        choice.message.content = content
        choice.finish_reason = finish_reason
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    def set_response(self, text: str):
        self._response = text

    def set_nonstreaming_responses(self, *responses: str):
        """Set sequential responses for _client.chat.completions.create calls.
        First call returns responses[0], second returns responses[1], etc.
        Use this when a test triggers both intent correction and question answering.
        """
        self._client_create.side_effect = [
            self._make_chat_response(r) for r in responses
        ]

    def set_chat_responses(self, *responses):
        self._client_create.side_effect = responses

    async def generate(self, user_text: str, max_tokens: int = 512) -> str:
        self.generate_calls.append(user_text)
        return self._response

    def reset_context(self):
        pass


class FakeClassmateEngine:
    enabled = True

    def __init__(self):
        self.interject_name = "小明"
        self.quiz_name = "小明"
        self.quiz_audio_url = None
        self.interjection_calls = []

    def should_interject(self, knowledge_index: int = 999):
        return self.interject_name

    def should_answer_interaction(self):
        return self.quiz_name

    async def generate_interjection(self, name: str, context: str):
        self.interjection_calls.append({"name": name, "context": context})
        return {
            "speaker": name,
            "kind": "interjection",
            "text": "我有个问题",
            "audio_url": None,
        }

    async def generate_quiz_answer(self, name: str, question: str):
        return {
            "speaker": name,
            "kind": "quiz_guess",
            "text": "我猜A",
            "audio_url": self.quiz_audio_url,
        }

    async def generate_interaction_answer(self, name: str, question: str):
        return {
            "speaker": name,
            "kind": "interaction_answer",
            "text": "我来回答",
            "audio_url": None,
        }


class FakeManager:
    def __init__(self, action: str):
        self.action = action
        self.updated = []

    def update_state(self, **kwargs):
        self.updated.append(kwargs)

    async def evaluate(self):
        from teaching.pacing_engine import PacingAction
        return [PacingAction(self.action, "test")]

    async def decide(self):
        """Legacy interface for backward compat."""
        from teaching.manager_agent import ManagerDecision
        return ManagerDecision(self.action, "test")


async def _keep_tts_idle(ctrl):
    """Keep calling notify_platform_idle so _broadcast_chapter doesn't hang."""
    while True:
        ctrl.notify_platform_idle()
        await asyncio.sleep(0.001)


async def _wait_until(predicate, attempts: int = 1000):
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("Condition was not met")


def has_component(ctrl, ctype: str, kind: str | None = None) -> bool:
    for item in ctrl.get_status()["components"]:
        if item["type"] != ctype:
            continue
        if kind is None or item["data"].get("kind") == kind:
            return True
    return False


@pytest.fixture
def controller():
    agent = FakeAgent()
    cm = FakeCourseManager()
    llm = FakeLlmClient()
    ctrl = TeachingController(
        agent=agent,
        course_manager=cm,
        llm_client=llm,
        course_end_pause_seconds=0,
    )
    return ctrl, agent, cm, llm


# ---------------------------------------------------------------------------
# State transition tests
# ---------------------------------------------------------------------------

class TestStateTransitions:
    def test_initial_state_is_idle(self, controller):
        ctrl, _, _, _ = controller
        assert ctrl.state == TeachingState.IDLE

    @pytest.mark.asyncio
    async def test_start_transitions_to_lecturing(self, controller):
        ctrl, agent, _, _ = controller
        assert ctrl.state == TeachingState.IDLE
        ctrl.start()
        try:
            await asyncio.sleep(0.05)
            assert ctrl.state == TeachingState.LECTURING
        finally:
            task = ctrl._task
            ctrl.stop()
            if task:
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    @pytest.mark.asyncio
    async def test_stop_from_lecturing_returns_to_idle(self, controller):
        ctrl, agent, _, _ = controller
        ctrl.start()
        await asyncio.sleep(0.05)
        assert ctrl.state != TeachingState.IDLE
        ctrl.stop()
        assert ctrl.state == TeachingState.IDLE

    def test_pause_from_idle_noop(self, controller):
        ctrl, _, _, _ = controller
        ctrl.pause()
        assert ctrl.state == TeachingState.IDLE

    def test_raise_hand_from_lecturing_enters_answering(self, controller):
        ctrl, _, _, _ = controller
        ctrl._state = TeachingState.LECTURING
        ctrl.raise_hand()
        assert ctrl.state == TeachingState.ANSWERING

    def test_raise_hand_records_breakpoint(self, controller):
        ctrl, _, _, _ = controller
        ctrl._state = TeachingState.LECTURING
        ctrl._current_chapter_id = "ch1"
        ctrl._current_skeleton_index = 1
        ctrl.raise_hand()
        assert ctrl.state == TeachingState.ANSWERING
        assert ctrl._breakpoint is not None
        assert ctrl._breakpoint["chapter_id"] == "ch1"
        assert ctrl._breakpoint["skeleton_index"] == 1

    @pytest.mark.asyncio
    async def test_stop_classmate_audio_emits_stop_component(self, controller):
        ctrl, _, _, _ = controller

        await ctrl.stop_classmate_audio()

        components = ctrl.get_status()["components"]
        assert any(
            c["type"] == "play_audio" and c["action"] == "stop"
            for c in components
        )

    def test_raise_hand_from_quizzing_noop(self, controller):
        ctrl, _, _, _ = controller
        ctrl._state = TeachingState.QUIZZING
        ctrl.raise_hand()
        assert ctrl.state == TeachingState.QUIZZING

    def test_cancel_hand_from_answering_returns_to_lecturing(self, controller):
        ctrl, _, _, _ = controller
        ctrl._state = TeachingState.ANSWERING
        ctrl.cancel_hand()
        assert ctrl.state == TeachingState.LECTURING

    def test_answer_quiz_from_quizzing_enters_quiz_result(self, controller):
        ctrl, _, _, _ = controller
        ctrl._state = TeachingState.QUIZZING
        ctrl._current_chapter_id = "ch2"
        ctrl.answer_quiz("ch2", "A")
        assert ctrl._quiz_chosen == "A"

    def test_answer_quiz_wrong_chapter_raises(self, controller):
        ctrl, _, _, _ = controller
        ctrl._state = TeachingState.QUIZZING
        ctrl._current_chapter_id = "ch2"
        with pytest.raises(ValueError, match="current quiz"):
            ctrl.answer_quiz("ch1", "A")

    def test_get_status(self, controller):
        ctrl, _, cm, _ = controller
        ctrl._current_chapter_id = "ch2"
        ctrl._current_skeleton_index = 0
        status = ctrl.get_status()
        assert "state" in status
        assert "currentChapter" in status
        assert "currentChapterIndex" in status
        assert "totalChapters" in status
        assert status["currentChapter"]["id"] == "ch2"
        assert status["currentChapter"]["title"] == "第二章"
        assert status["currentChapter"]["skeleton"] == cm.get_chapter("ch2")["skeleton"]
        assert status["currentChapterIndex"] == 1
        assert "componentSeq" in status
        assert "quiz" in status
        assert "visual" in status

    def test_get_status_normalizes_text_skeleton_steps(self, controller):
        ctrl, _, cm, _ = controller
        cm._chapters.append({
            "id": "text_chapter",
            "title": "小手比一比",
            "skeleton": [
                {"text": "伸出两只手，比比谁大？", "experience": {"primitive": "cut_fold_unfold"}},
                {"text": "拿两个苹果，一个切一半。", "experience": {"primitive": "cut_fold_unfold"}},
            ],
        })
        ctrl._current_chapter_id = "text_chapter"

        status = ctrl.get_status()

        assert status["currentChapter"]["skeleton"] == [
            "伸出两只手，比比谁大？",
            "拿两个苹果，一个切一半。",
        ]

    def test_get_status_exposes_course_ended(self, controller):
        ctrl, _, _, _ = controller

        assert ctrl.get_status()["courseEnded"] is False
        ctrl._course_ended.set()
        assert ctrl.get_status()["courseEnded"] is False
        ctrl.mark_course_closed()
        assert ctrl.get_status()["courseEnded"] is True


@pytest.mark.asyncio
async def test_status_exposes_recent_components(controller):
    ctrl, _, _, _ = controller
    await ctrl._send_component("interaction_prompt", "show", {
        "text": "你会怎么做？",
        "chapter_id": "ch1",
    })
    await ctrl._send_component("encouragement", "show", {
        "text": "太棒了！",
        "style": "star",
    })

    status = ctrl.get_status()

    assert "components" in status
    assert status["components"][-2]["type"] == "interaction_prompt"
    assert status["components"][-1]["type"] == "encouragement"
    assert status["componentSeq"] == status["components"][-1]["seq"]
    status["components"][-1]["data"]["text"] = "mutated"
    assert ctrl._component_queue[-1]["data"]["text"] == "太棒了！"


@pytest.mark.asyncio
async def test_quiz_result_state_is_visible_before_lecturing(controller):
    ctrl, agent, cm, _ = controller
    chapter = cm.get_chapter("ch2")
    ctrl._state = TeachingState.QUIZZING
    ctrl._current_chapter_id = "ch2"

    task = asyncio.create_task(ctrl._handle_quiz(chapter))
    await _wait_until(lambda: ctrl._quiz_answer is not None)
    ctrl.answer_quiz("ch2", "A")
    await _wait_until(lambda: ctrl.state == TeachingState.QUIZ_RESULT)

    assert any(e["event"] == "quiz_result" for e in agent.custom_events)
    assert any(e["event"] == "encouragement" for e in agent.custom_events)
    assert ctrl.state == TeachingState.QUIZ_RESULT
    assert ctrl.get_status()["state"] == TeachingState.QUIZ_RESULT.value

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_classmate_interjection_emits_visible_component(controller):
    """PacingEngine CLASSMATE_SPEAK triggers interjection via controller."""
    ctrl, agent, cm, _ = controller
    ctrl._classmates = FakeClassmateEngine()
    ctrl._pacing = FakeManager("CLASSMATE_SPEAK")
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
    try:
        await ctrl._broadcast_chapter(cm.get_chapter("ch3"))

        assert has_component(ctrl, "classmate_message", "interjection")
        assert any(m["role"] == "classmate" for m in ctrl.get_status()["messages"])
        assert "我有个问题" not in agent.prompts
    finally:
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_quiz_opens_with_classmate_guess_component(controller):
    ctrl, _, cm, _ = controller
    ctrl._classmates = FakeClassmateEngine()
    chapter = cm.get_chapter("ch2")
    ctrl._state = TeachingState.QUIZZING
    ctrl._current_chapter_id = "ch2"

    task = asyncio.create_task(ctrl._handle_quiz(chapter))
    try:
        await _wait_until(lambda: ctrl._quiz_answer is not None)

        assert has_component(ctrl, "classmate_message", "quiz_guess")
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_quiz_guess_with_audio_emits_play_audio_component(controller):
    ctrl, _, cm, _ = controller
    ctrl._classmates = FakeClassmateEngine()
    ctrl._classmates.quiz_audio_url = "https://audio.example/xiaoming.mp3"
    chapter = cm.get_chapter("ch2")
    ctrl._state = TeachingState.QUIZZING
    ctrl._current_chapter_id = "ch2"

    task = asyncio.create_task(ctrl._handle_quiz(chapter))
    try:
        await _wait_until(lambda: ctrl._quiz_answer is not None)

        audio_components = [
            item for item in ctrl.get_status()["components"]
            if item["type"] == "play_audio"
        ]
        assert audio_components[-1]["data"] == {
            "url": "https://audio.example/xiaoming.mp3",
            "speaker": "小明",
        }
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_manager_classmate_decision_emits_visible_component(controller):
    ctrl, _, cm, _ = controller
    ctrl._classmates = FakeClassmateEngine()
    ctrl._classmates.interject_name = None
    ctrl._manager = FakeManager("CLASSMATE_SPEAK")
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
    try:
        await ctrl._broadcast_chapter(cm.get_chapter("ch3"))

        assert has_component(ctrl, "classmate_message", "interjection")
    finally:
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_manager_classmate_decision_waits_for_teacher_tts_idle(controller):
    ctrl, _, cm, _ = controller
    ctrl._classmates = FakeClassmateEngine()
    ctrl._classmates.interject_name = None
    ctrl._state = TeachingState.LECTURING
    ctrl._tts_idle.clear()

    from teaching.pacing_engine import PacingAction
    task = asyncio.create_task(ctrl._execute_pacing_classmate(
        cm.get_chapter("ch3"),
        "最后的内容",
        PacingAction("CLASSMATE_SPEAK", "test"),
    ))
    try:
        await _wait_until(lambda: ctrl._classmates.interjection_calls)

        assert not has_component(ctrl, "classmate_message", "interjection")

        ctrl.notify_platform_idle()
        await task
        assert ctrl._classmates.interjection_calls
        assert has_component(ctrl, "classmate_message", "interjection")
    finally:
        if not task.done():
            ctrl.notify_platform_idle()
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_manager_classmate_decision_pregenerates_before_teacher_tts_idle(controller):
    ctrl, agent, cm, _ = controller
    ctrl._greeting_sent = True
    ctrl._classmates = FakeClassmateEngine()
    ctrl._manager = FakeManager("CLASSMATE_SPEAK")

    task = asyncio.create_task(ctrl._broadcast_chapter(cm.get_chapter("ch3")))
    try:
        await asyncio.sleep(1.6)
        await _wait_until(lambda: agent.prompts == ["润色后的文本"])
        await _wait_until(lambda: ctrl._classmates.interjection_calls)

        assert not has_component(ctrl, "classmate_message", "interjection")

        ctrl.notify_platform_idle()
        await task
        assert has_component(ctrl, "classmate_message", "interjection")
    finally:
        if not task.done():
            ctrl.notify_platform_idle()
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_manager_classmate_decision_teacher_acknowledges_classmate(controller):
    ctrl, agent, cm, llm = controller
    llm.set_nonstreaming_responses("STATEMENT", "小明说得真好！我们继续往下看～")
    ctrl._classmates = FakeClassmateEngine()
    ctrl._classmates.interject_name = None
    ctrl._manager = FakeManager("CLASSMATE_SPEAK")
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
    try:
        await ctrl._broadcast_chapter(cm.get_chapter("ch3"))

        assert has_component(ctrl, "classmate_message", "interjection")
        assert any("小明说得真好" in prompt for prompt in agent.prompts)
    finally:
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_teacher_answers_classmate_question_instead_of_generic_ack(controller):
    ctrl, agent, _, llm = controller
    llm.set_nonstreaming_responses(
        "QUESTION",
        "小红问得真好！镜子会把左右换过来，所以右手看起来像左手。",
    )

    await ctrl._emit_classmate_turn({
        "speaker": "小红",
        "kind": "interjection",
        "intent": "question",
        "text": "老师老师！为什么我举右手，镜子里会举左手呀？",
        "audio_url": None,
    }, ack_template="{name}问得真好！我们继续往下看～")

    assert "小红问得真好！镜子会把左右换过来，所以右手看起来像左手。" in agent.prompts
    assert "小红问得真好！我们继续往下看～" not in agent.prompts


@pytest.mark.asyncio
async def test_teacher_rejects_length_truncated_classmate_question_answer(controller):
    ctrl, agent, _, llm = controller
    llm.set_chat_responses(
        llm._make_chat_response("QUESTION"),
        llm._make_chat_response("小明问得真好！反过来想，就像把积木", finish_reason="length"),
    )

    await ctrl._emit_classmate_turn({
        "speaker": "小明",
        "kind": "interjection",
        "intent": "question",
        "text": "老师，反过来想是什么意思？",
        "audio_url": None,
    }, ack_template="{name}问得真好！我们继续往下看～")

    assert agent.prompts[-1] == "小明问了一个很好的问题！我们记住它，学完这节课再来回答，好不好？"
    assert "把积木" not in agent.prompts[-1]


@pytest.mark.asyncio
async def test_classmate_turn_with_audio_still_plays_outside_qa(controller):
    ctrl, _, _, _ = controller
    ctrl._state = TeachingState.LECTURING

    await ctrl._emit_classmate_turn({
        "speaker": "小明",
        "kind": "interjection",
        "intent": "statement",
        "text": "我看见镜子里的手反过来了。",
        "audio_url": "https://audio.example/xiaoming.mp3",
    })

    audio_components = [
        c for c in ctrl.get_status()["components"]
        if c["type"] == "play_audio"
    ]
    assert audio_components[-1]["action"] == "show"
    assert audio_components[-1]["data"] == {
        "url": "https://audio.example/xiaoming.mp3",
        "speaker": "小明",
    }


@pytest.mark.asyncio
async def test_classmate_turn_is_suppressed_during_student_qa(controller):
    ctrl, agent, _, _ = controller
    ctrl._state = TeachingState.ANSWERING

    await ctrl._emit_classmate_turn({
        "speaker": "小明",
        "kind": "interjection",
        "intent": "question",
        "text": "老师，我也想问问题。",
        "audio_url": "https://audio.example/xiaoming.mp3",
    }, ack_template="{name}问得真好！我们继续往下看～")

    assert not agent.prompts
    assert not any(
        c["type"] in ("classmate_message", "play_audio")
        for c in ctrl.get_status()["components"]
    )


@pytest.mark.asyncio
async def test_teacher_answers_classmate_question_with_llm(controller):
    """LLM-generated answer is used directly (no boilerplate filter)."""
    ctrl, agent, _, llm = controller
    llm.set_nonstreaming_responses(
        "QUESTION",
        "小明真细心！镜子的秘密就是左右会换过来，你试试举右手看看。",
    )

    await ctrl._emit_classmate_turn({
        "speaker": "小明",
        "kind": "interjection",
        "intent": "question",
        "text": "老师，为什么红色那么重要呀？",
        "audio_url": None,
    }, ack_template="{name}问得真好！我们继续往下看～")

    # LLM answer is accepted as-is
    assert "小明真细心！镜子的秘密就是左右会换过来" in agent.prompts[-1]


@pytest.mark.asyncio
async def test_teacher_acknowledges_classmate_statement_as_observation(controller):
    ctrl, agent, _, llm = controller
    llm.set_nonstreaming_responses("STATEMENT", "小红真细心，发现了红色的小秘密！我们继续往下看～")

    await ctrl._emit_classmate_turn({
        "speaker": "小红",
        "kind": "interjection",
        "intent": "statement",
        "text": "老师老师，我家里有红色的小手套！",
        "audio_url": None,
    }, ack_template="{name}问得真好！我们继续往下看～")

    assert "小红问得真好！我们继续往下看～" not in agent.prompts
    assert any("小红真细心" in prompt for prompt in agent.prompts)


@pytest.mark.asyncio
async def test_teacher_answers_obvious_question_even_if_intent_is_wrong(controller):
    ctrl, agent, _, llm = controller
    llm.set_nonstreaming_responses(
        "QUESTION",
        "小红问得好！镜子不会真的变东西，它只是把另一半照出来。",
    )

    await ctrl._emit_classmate_turn({
        "speaker": "小红",
        "kind": "interjection",
        "intent": "statement",
        "text": "老师，镜子真的能变出另一半吗？",
        "audio_url": None,
    }, ack_template="{name}问得真好！我们继续往下看～")

    assert "小红观察得很细！这个线索很有用。" not in agent.prompts
    assert "小红问得好！镜子不会真的变东西，它只是把另一半照出来。" in agent.prompts


@pytest.mark.asyncio
async def test_teacher_treats_permission_to_ask_as_question(controller):
    ctrl, agent, _, llm = controller
    llm.set_nonstreaming_responses(
        "QUESTION",
        "当然可以，小美。你慢慢问，老师听着呢。",
    )

    await ctrl._emit_classmate_turn({
        "speaker": "小美",
        "kind": "interjection",
        "intent": "statement",
        "text": "那个……我可以小声问一个问题吗？",
        "audio_url": None,
    }, ack_template="{name}问得真好！我们继续往下看～")

    assert "小美观察得很细！这个线索很有用。" not in agent.prompts
    assert "当然可以，小美。你慢慢问，老师听着呢。" in agent.prompts
    classifier_call = llm._client_create.await_args_list[0].kwargs["messages"]
    assert "请求老师允许提问" in classifier_call[0]["content"]
    assert "QUESTION" in classifier_call[0]["content"]


@pytest.mark.asyncio
async def test_classmate_intent_avoids_text_classifier(controller):
    ctrl, agent, _, llm = controller
    llm.set_nonstreaming_responses(
        "QUESTION",
        "小明问得好！红色是订单给我们的第一条线索。",
    )

    await ctrl._emit_classmate_turn({
        "speaker": "小明",
        "kind": "interjection",
        "intent": "question",
        "text": "老师，我想知道这条线索怎么用。",
        "audio_url": None,
    }, ack_template="{name}问得真好！我们继续往下看～")

    assert len(llm._client_create.await_args_list) == 2
    assert "小明问得好！红色是订单给我们的第一条线索。" in agent.prompts


@pytest.mark.asyncio
async def test_classmate_message_log_preserves_speaker_for_frontend_dedupe(controller):
    ctrl, _, _, _ = controller

    await ctrl._emit_classmate_turn({
        "speaker": "小刚",
        "kind": "interjection",
        "text": "哇！镜子里会举右手对不对？",
        "audio_url": None,
    }, ack_template=None)

    message = ctrl.get_status()["messages"][-1]
    assert message["role"] == "classmate"
    assert message["name"] == "小刚"
    assert message["text"] == "哇！镜子里会举右手对不对？"


@pytest.mark.asyncio
async def test_interaction_timeout_lets_classmate_answer_after_student_is_silent(controller, monkeypatch):
    ctrl, agent, _, llm = controller
    ctrl._classmates = FakeClassmateEngine()
    ctrl._state = TeachingState.WAITING_INTERACT
    ctrl._current_chapter_id = "ch1"
    llm.set_nonstreaming_responses("STATEMENT")

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    await ctrl._interaction_timeout("ch1", "你觉得呢？")

    assert has_component(ctrl, "classmate_message", "interaction_answer")
    assert any("小明说得很好" in prompt for prompt in agent.prompts)
    assert "我们先继续往下看，等你想到答案可以再举手告诉老师。" not in agent.prompts


@pytest.mark.asyncio
async def test_final_interaction_classmate_answer_is_followed_by_goodbye(controller, monkeypatch):
    ctrl, agent, cm, _ = controller
    ctrl._classmates = FakeClassmateEngine()
    cm.get_chapter("ch3")["interaction"] = {"prompt": "你会剪什么？"}
    ctrl._state = TeachingState.WAITING_INTERACT
    ctrl._current_chapter_id = "ch3"

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    await ctrl._interaction_timeout("ch3", "你会剪什么？")
    await ctrl._task

    assert any("小明说得很好" in prompt for prompt in agent.prompts)
    assert "我们先继续往下看，等你想到答案可以再举手告诉老师。" not in agent.prompts
    assert any("再见" in prompt for prompt in agent.prompts)
    assert ctrl._course_ended.is_set()
    assert ctrl.get_status()["courseEnded"] is False


# ---------------------------------------------------------------------------
# Lecture loop tests
# ---------------------------------------------------------------------------

def _event_payload(events, event_name):
    """Return the nested data payload for events matching event_name."""
    for e in events:
        if e["event"] == event_name:
            return e["data"]["data"]
    return None


@pytest.mark.asyncio
async def test_lecture_sends_chapter_indicator(controller):
    ctrl, agent, cm, _ = controller
    sent_components = []

    async def capture_component(ctype: str, action: str, data: dict) -> None:
        sent_components.append({"type": ctype, "action": action, "data": data})

    ctrl._send_component = capture_component
    ctrl.log_message = lambda *_args, **_kwargs: None
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
    try:
        chapter = cm.get_chapter("ch1")
        await ctrl._broadcast_chapter(chapter)
        payload = next(
            item["data"]
            for item in sent_components
            if item["type"] == "chapter_indicator"
        )
        assert payload["title"] == "第一章"
        assert payload["chapter_id"] == "ch1"
        assert payload["chapter_index"] == 0
        assert payload["total_chapters"] == cm.get_chapter_count()
    finally:
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_lecture_sends_visual_card(controller):
    ctrl, agent, cm, _ = controller
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
    try:
        chapter = cm.get_chapter("ch1")
        await ctrl._broadcast_chapter(chapter)
        payload = _event_payload(agent.custom_events, "card")
        assert payload is not None
        assert payload["id"] == "card1"
        assert payload["title"] == "卡片"
        assert payload["content"] == "内容"
    finally:
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_lecture_polishes_skeleton_via_llm(controller):
    ctrl, agent, cm, llm = controller
    llm.set_response("润色版")
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
    try:
        chapter = cm.get_chapter("ch1")
        await ctrl._broadcast_chapter(chapter)
        assert len(llm.generate_calls) >= 2  # 2 skeleton points, plus possible pre-polish
        assert len(agent.prompts) >= 2  # 2 skeleton prompts + possibly interaction
        for call in llm.generate_calls[:2]:
            assert "要点" in call
    finally:
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_structured_skeleton_step_emits_interactive_scene(controller):
    ctrl, agent, cm, llm = controller
    chapter = {
        "id": "scene_chapter",
        "title": "互动章节",
        "skeleton": [
            {
                "text": "我们试着挥动右手，镜子里怪兽挥动左手。",
                "experience": {
                    "primitive": "mirror_transform",
                    "goal": "理解镜像左右相反",
                    "prompt": "点一点怪兽的右手。",
                    "props": {"action": "hand_flip", "rule": "horizontal_flip"},
                },
            }
        ],
    }
    cm._chapters.append(chapter)
    ctrl._greeting_sent = True
    llm.set_response("润色后的镜子讲解")
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
    try:
        await ctrl._broadcast_chapter(chapter)

        scene = _event_payload(agent.custom_events, "interactive_scene")
        assert scene == {
            "chapter_id": "scene_chapter",
            "step_num": 1,
            "step_total": 1,
            "primitive": "mirror_transform",
            "goal": "理解镜像左右相反",
            "title": "互动章节",
            "prompt": "点一点怪兽的右手。",
            "props": {"action": "hand_flip", "rule": "horizontal_flip"},
        }
        whiteboard = _event_payload(agent.custom_events, "whiteboard_step")
        assert whiteboard["text"] == "我们试着挥动右手，镜子里怪兽挥动左手。"
        assert "镜子里怪兽" in llm.generate_calls[0]
    finally:
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_lecture_skeleton_fallback_on_llm_failure(controller):
    ctrl, agent, cm, llm = controller

    class FailOnceLlm:
        def __init__(self):
            self.generate_calls = []
            self.called = 0
            self._response = ""
            self._system_prompt = ""
            self._messages = []

        def set_response(self, text):
            pass

        async def generate(self, user_text, max_tokens=512):
            self.generate_calls.append(user_text)
            self.called += 1
            raise RuntimeError("LLM error")

        def reset_context(self):
            pass

    fail_llm = FailOnceLlm()
    ctrl._llm = fail_llm
    ctrl._greeting_sent = True  # Skip greeting for this test
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
    try:
        chapter = cm.get_chapter("ch3")
        await ctrl._broadcast_chapter(chapter)
        # Should fall back to raw skeleton text (greeting already suppressed)
        assert len(agent.prompts) == 1
        assert agent.prompts[0] == "最后的内容"
    finally:
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_lecture_sends_lecture_progress(controller):
    ctrl, agent, cm, llm = controller
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
    try:
        chapter = cm.get_chapter("ch1")
        await ctrl._broadcast_chapter(chapter)
        progress_events = [e for e in agent.custom_events
                           if e["event"] == "lecture_progress"]
        assert len(progress_events) == 2
        assert progress_events[0]["data"]["data"]["segment_current"] == 1
        assert progress_events[0]["data"]["data"]["segment_total"] == 2
        assert progress_events[1]["data"]["data"]["segment_current"] == 2
        assert progress_events[1]["data"]["data"]["segment_total"] == 2
    finally:
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_chapter_with_interaction_enters_waiting(controller):
    ctrl, agent, cm, llm = controller
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
    try:
        chapter = cm.get_chapter("ch1")
        await ctrl._broadcast_chapter(chapter)
        assert ctrl.state == TeachingState.WAITING_INTERACT
        assert agent.prompts[-1] == (
            "小侦探，现在轮到你啦！你觉得呢？"
            "想好后，点蓝色麦克风告诉老师。"
        )
    finally:
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_interaction_prompt_component_waits_until_teacher_prompt_idle(controller):
    ctrl, agent, _, _ = controller
    ctrl._greeting_sent = True
    ctrl._tts_idle.clear()
    chapter = {
        "id": "interaction_only",
        "title": "互动章节",
        "skeleton": [],
        "interaction": {"prompt": "你会怎么做？"},
    }

    task = asyncio.create_task(ctrl._broadcast_chapter(chapter))
    try:
        await _wait_until(lambda: agent.prompts)

        assert agent.prompts[-1] == (
            "小侦探，现在轮到你啦！你会怎么做？"
            "想好后，点蓝色麦克风告诉老师。"
        )
        assert not any(
            event["event"] == "interaction_prompt"
            for event in agent.custom_events
        )
        assert ctrl._interaction_timeout_task is None

        ctrl.notify_platform_idle()
        await task

        assert any(
            event["event"] == "interaction_prompt"
            for event in agent.custom_events
        )
    finally:
        if not task.done():
            ctrl.notify_platform_idle()
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_interaction_state_waits_for_response_or_timeout(controller):
    ctrl, agent, cm, _ = controller
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
    try:
        chapter = cm.get_chapter("ch1")
        await ctrl._broadcast_chapter(chapter)

        assert ctrl.state == TeachingState.WAITING_INTERACT
        status = ctrl.get_status()
        assert status["interaction"] == {
            "text": "你觉得呢？",
            "chapter_id": "ch1",
        }
    finally:
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_run_lecture_loop_stops_at_interaction(controller):
    """After WAITING_INTERACT, loop should wait instead of advancing."""
    ctrl, agent, cm, llm = controller
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
    ctrl.start()
    try:
        for _ in range(500):
            if ctrl.state == TeachingState.WAITING_INTERACT:
                break
            await asyncio.sleep(0.01)
        assert ctrl.state == TeachingState.WAITING_INTERACT
        assert ctrl._current_chapter_id == "ch1"
    finally:
        idle_task.cancel()
        task = ctrl._task
        ctrl.stop()
        for t in (idle_task, task):
            if t:
                try:
                    await t
                except asyncio.CancelledError:
                    pass


@pytest.mark.asyncio
async def test_resume_lecture_stops_at_interaction_before_next_chapter(controller):
    ctrl, agent, cm, llm = controller
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))

    async def fail_if_quiz_reached(chapter):
        raise AssertionError("resume advanced past interaction chapter")

    ctrl._handle_quiz = fail_if_quiz_reached
    ctrl._breakpoint = {"chapter_id": "ch1", "skeleton_index": 0}
    try:
        await ctrl._resume_lecture()

        assert ctrl.state == TeachingState.WAITING_INTERACT
        assert ctrl._current_chapter_id == "ch1"
    finally:
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_chapter_with_quiz_enters_quizzing(controller):
    ctrl, agent, cm, llm = controller
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
    try:
        chapter = cm.get_chapter("ch2")
        await ctrl._broadcast_chapter(chapter)
        assert ctrl.state == TeachingState.QUIZZING
    finally:
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_chapter_with_interaction_and_quiz_handles_quiz_first(controller):
    ctrl, agent, cm, llm = controller
    chapter = {
        "id": "quiz_and_interaction",
        "title": "先测验再互动",
        "skeleton": ["最后一个知识点"],
        "quiz": {
            "question": "选一个？",
            "options": [
                {"key": "A", "text": "A", "correct": True},
                {"key": "B", "text": "B", "correct": False},
            ],
            "explanation_correct": "对",
            "explanation_wrong": "再想想",
        },
        "interaction": {
            "prompt": "再来动手试试",
            "expect_keywords": ["动手"],
        },
    }
    cm._chapters.append(chapter)
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
    try:
        await ctrl._broadcast_chapter(chapter)

        assert ctrl.state == TeachingState.QUIZZING
        assert not any(e["event"] == "interaction_prompt" for e in agent.custom_events)
    finally:
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_watermelon_chapter_emits_experience_and_enters_quiz(controller):
    ctrl, agent, cm, llm = controller
    from teaching.course_manager import CourseManager

    course_path = (
        Path(__file__).parents[2]
        / "config"
        / "courses"
        / "分一分大西瓜_4-6.yaml"
    )
    watermelon_cm = CourseManager(course_path)
    chapter = watermelon_cm.get_chapter("chapter_3")
    ctrl._cm = watermelon_cm
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
    try:
        await ctrl._broadcast_chapter(chapter)

        scene_events = [
            e["data"]["data"]
            for e in agent.custom_events
            if e["event"] == "interactive_scene"
        ]
        assert scene_events[-1]["primitive"] == "watermelon_halves"
        assert scene_events[-1]["props"] == {"mode": "match", "state": "complete"}
        assert ctrl.state == TeachingState.QUIZZING
    finally:
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_plain_chapter_advances(controller):
    ctrl, agent, cm, llm = controller
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
    try:
        ctrl._state = TeachingState.LECTURING
        chapter = cm.get_chapter("ch3")
        await ctrl._broadcast_chapter(chapter)
        # ch3 has no interaction or quiz, so state stays LECTURING
        assert ctrl.state == TeachingState.LECTURING
    finally:
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_full_lecture_loop(controller):
    ctrl, agent, cm, llm = controller
    llm.set_response("好")
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))

    async def answer_quiz_after_delay():
        await asyncio.sleep(0.1)
        while ctrl.state != TeachingState.QUIZZING:
            await asyncio.sleep(0.05)
        ctrl.answer_quiz("ch2", "A")

    answer_task = asyncio.create_task(answer_quiz_after_delay())
    try:
        await ctrl._run_lecture_loop()
        # ch1(2 points) + ch2(1 point) + ch3(1 point) = 4 prompts via _polish_skeleton
        # ch1 also has interaction prompt sent via agent.send_prompt
        # So total prompts >= 4
        assert len(agent.prompts) >= 4
    finally:
        idle_task.cancel()
        answer_task.cancel()
        for t in (idle_task, answer_task):
            try:
                await t
            except asyncio.CancelledError:
                pass


# ---------------------------------------------------------------------------
# QA timing tests — verify _tts_idle behavior for interrupt → QA → follow-up
# ---------------------------------------------------------------------------


class TestTtsIdleTiming:
    """Verify that _tts_idle doesn't fire prematurely during QA flow."""

    @pytest.mark.asyncio
    async def test_await_tts_idle_blocks_when_pre_cleared(self, controller):
        """After explicit clear(), await_tts_idle should block until notify."""
        ctrl, _, _, _ = controller
        ctrl._tts_idle.clear()  # Simulate: clear before QA response
        called = []

        async def delayed_idle():
            await asyncio.sleep(0.1)
            ctrl.notify_platform_idle()
            called.append(True)

        asyncio.create_task(delayed_idle())
        start = time.time()
        await ctrl.await_tts_idle(timeout=5.0)
        elapsed = time.time() - start
        assert called, "notify_platform_idle should have been called"
        assert elapsed >= 0.05, f"Should have waited, but elapsed={elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_await_tts_idle_immediate_when_pre_set(self, controller):
        """Without pre-clear, await_tts_idle returns immediately if already set."""
        ctrl, _, _, _ = controller
        ctrl._tts_idle.set()  # Already set (like after interrupt IDLE)
        start = time.time()
        await ctrl.await_tts_idle(timeout=5.0)
        elapsed = time.time() - start
        assert elapsed < 0.05, (
            f"Should return immediately when pre-set, but elapsed={elapsed:.3f}s"
        )


class TestClassifyIntent:
    """Verify student speech classification for QA follow-up."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", ["为什么", "什么意思", "我不懂"])
    async def test_question_is_not_resume(self, text):
        """Questions → not RESUME (classified by LLM)."""
        from teaching.agent import TeachingListener
        listener = TeachingListener()
        result = await listener._classify_intent(text)
        # Without API key, LLM call fails → returns False (safe default: QUESTION)
        assert result is False, f"'{text}' should NOT be RESUME"



class TestFollowUpLimit:
    """Verify follow-up doesn't loop infinitely."""

    def test_raise_hand_transitions_from_lecturing(self, controller):
        """raise_hand should transition LECTURING → ANSWERING."""
        ctrl, _, _, _ = controller
        ctrl._state = TeachingState.LECTURING  # Simulate: lecture in progress
        ctrl.raise_hand()
        assert ctrl.state == TeachingState.ANSWERING

    def test_raise_hand_noop_from_idle(self, controller):
        """raise_hand from IDLE is a no-op."""
        ctrl, _, _, _ = controller
        assert ctrl.state == TeachingState.IDLE
        ctrl.raise_hand()
        assert ctrl.state == TeachingState.IDLE

    def test_cancel_hand_transitions_back(self, controller):
        """cancel_hand should transition ANSWERING → LECTURING."""
        ctrl, _, _, _ = controller
        ctrl._state = TeachingState.ANSWERING
        ctrl.cancel_hand()
        assert ctrl.state == TeachingState.LECTURING


# ---------------------------------------------------------------------------
# Integration tests — verify lecture loop doesn't stall mid-chapter
# ---------------------------------------------------------------------------


class TestLectureLoopNoStall:
    """Verify the lecture loop advances through all points without stalling."""

    @pytest.mark.asyncio
    async def test_full_chapter_completes_all_points(self, controller):
        """All skeleton points are sent via send_prompt, loop doesn't hang."""
        ctrl, agent, cm, llm = controller
        idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
        try:
            chapter = cm.get_chapter("ch3")  # Plain chapter: 1 skeleton point
            await ctrl._broadcast_chapter(chapter)

            # LLM always returns "润色后的文本" — verify it was sent
            assert len(agent.prompts) >= 1, (
                f"Expected ≥1 prompt, got {len(agent.prompts)}: {agent.prompts}"
            )
        finally:
            idle_task.cancel()
            try:
                await idle_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_multi_point_chapter_no_stall(self, controller):
        """Chapter with 2 points: both are sent, loop completes cleanly."""
        ctrl, agent, cm, llm = controller
        ctrl._greeting_sent = True  # skip greeting
        idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
        try:
            chapter = cm.get_chapter("ch1")  # 2 skeleton points + interaction
            await ctrl._broadcast_chapter(chapter)

            # Both skeleton points should be polished and sent
            assert len(agent.prompts) >= 2 + 1, (  # 2 points + interaction prompt
                f"Expected ≥3 prompts (2 skeleton + interaction), got {len(agent.prompts)}: {agent.prompts}"
            )
            # Verify interaction prompt was sent
            assert any("你觉得呢" in p for p in agent.prompts), (
                f"Interaction prompt missing from: {agent.prompts}"
            )
            # State should be WAITING_INTERACT
            assert ctrl.state == TeachingState.WAITING_INTERACT
        finally:
            idle_task.cancel()
            try:
                await idle_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_qa_follow_up_timeout_resumes_lecture(self, controller):
        """After QA answer + follow-up prompt, 4s timeout → lecture resumes."""
        ctrl, agent, cm, llm = controller
        ctrl._greeting_sent = True
        ctrl._state = TeachingState.LECTURING
        ctrl._current_chapter_id = "ch1"
        ctrl._current_skeleton_index = 1
        ctrl._breakpoint = {"chapter_id": "ch1", "skeleton_index": 1}
        idle_task = asyncio.create_task(_keep_tts_idle(ctrl))

        # Simulate raise-hand + QA
        ctrl.raise_hand()
        assert ctrl.state == TeachingState.ANSWERING

        # Run _broadcast_chapter on ch3 (1 point) to verify resume works
        # The resume should send "好的，我们继续～" + polished skeleton
        ctrl.cancel_hand()
        assert ctrl.state == TeachingState.LECTURING

        # cancel_hand should NOT create task (handled by HTTP handler in prod)
        # For this test, manually simulate resume
        ctrl._task = asyncio.create_task(ctrl._resume_lecture())
        # Wait a tick for the task to start
        await asyncio.sleep(0.2)
        # The resume sends filler + polishes + sends skeleton point
        prompts_before = len(agent.prompts)
        # Wait for resume to complete (ch3 has 1 point)
        for _ in range(200):
            if ctrl._task.done():
                break
            await asyncio.sleep(0.05)
        assert len(agent.prompts) > prompts_before, (
            f"Resume should have sent prompts. Before={prompts_before}, after={len(agent.prompts)}"
        )
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_no_more_questions_triggers_resume(self, controller):
        """Student says '没有了' during follow-up → RESUME → lecture restarts."""
        ctrl, agent, cm, llm = controller
        from teaching.agent import TeachingListener
        ctrl._greeting_sent = True
        ctrl._state = TeachingState.LECTURING
        ctrl._current_chapter_id = "ch1"
        ctrl._current_skeleton_index = 1
        ctrl._breakpoint = {"chapter_id": "ch1", "skeleton_index": 1}
        idle_task = asyncio.create_task(_keep_tts_idle(ctrl))

        listener = TeachingListener(llm_client=llm)
        listener.agent = agent
        listener.controller = ctrl

        # Simulate: follow-up was asked, student says "没有了"
        ctrl._state = TeachingState.ANSWERING
        llm.set_response("RESUME")  # LLM classifies as confirmation
        result = await listener._classify_intent("没有了")
        assert result is True, "'没有了' should be classified as RESUME"

        ctrl._task = None
        await listener._handle_qa("没有了")
        await asyncio.sleep(0.3)
        assert ctrl.state == TeachingState.LECTURING, (
            f"Should be LECTURING after RESUME, got {ctrl.state}"
        )
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_quiz_chapter_enters_quizzing(self, controller):
        """Chapter with quiz transitions to QUIZZING state, doesn't stall."""
        ctrl, agent, cm, llm = controller
        ctrl._greeting_sent = True
        idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
        try:
            chapter = cm.get_chapter("ch2")  # Quiz chapter
            await ctrl._broadcast_chapter(chapter)

            assert ctrl.state == TeachingState.QUIZZING, (
                f"Expected QUIZZING, got {ctrl.state}"
            )
        finally:
            idle_task.cancel()
            try:
                await idle_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_lecture_loop_waits_at_interaction(self, controller):
        """Full _run_lecture_loop pauses at teacher interaction."""
        ctrl, agent, cm, llm = controller
        ctrl._greeting_sent = True  # skip greeting
        idle_task = asyncio.create_task(_keep_tts_idle(ctrl))

        ctrl.start()
        try:
            for _ in range(600):
                if ctrl.state == TeachingState.WAITING_INTERACT:
                    break
                await asyncio.sleep(0.05)
            assert ctrl.state == TeachingState.WAITING_INTERACT
            assert ctrl._current_chapter_id == "ch1"
            assert not ctrl._course_ended.is_set()
            assert len(agent.prompts) >= 3, (
                f"Expected ≥3 prompts, got {len(agent.prompts)}"
            )
        finally:
            idle_task.cancel()
            task = ctrl._task
            ctrl.stop()
            for t in (idle_task, task):
                if t:
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass

    @pytest.mark.asyncio
    async def test_course_end_sends_goodbye(self, controller):
        """_end_course should send goodbye prompt and set _course_ended."""
        ctrl, agent, cm, llm = controller
        idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
        try:
            assert not ctrl._course_ended.is_set()
            await ctrl._end_course()
            assert ctrl.state == TeachingState.LECTURING  # Allows raise-hand after goodbye
            assert ctrl._course_ended.is_set()
            assert any("再见" in p or "下次" in p for p in agent.prompts), (
                f"Goodbye prompt missing from: {agent.prompts}"
            )
        finally:
            idle_task.cancel()
            try:
                await idle_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_end_course_skipped_during_qa(self, controller):
        """_end_course is a no-op if student is in ANSWERING (QA in progress)."""
        ctrl, agent, _, _ = controller
        ctrl._state = TeachingState.ANSWERING
        assert not ctrl._course_ended.is_set()
        await ctrl._end_course()
        # Should have returned immediately without sending goodbye
        assert not ctrl._course_ended.is_set()
        assert ctrl.state == TeachingState.ANSWERING
