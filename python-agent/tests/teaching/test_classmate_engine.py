"""Tests for classmate_engine.py structured classmate messages."""

from unittest.mock import MagicMock
import pytest

from teaching.classmate_engine import ClassmateEngine
from teaching.tts_client import TtsClient


class FakePersona:
    classmates = [{"name": "小明", "voice": "boy"}]

    def build_classmate_prompt(self, name):
        return f"{name} prompt"

    def build_classmate_interjection_prompt(self, name, context):
        return f"{name} interject {context}"

    def build_classmate_quiz_answer_prompt(self, name, question):
        return f"{name} quiz {question}"


def _make_chat_response(content: str, finish_reason: str = "stop") -> MagicMock:
    """Build a mock OpenAI chat completions response."""
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = finish_reason
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class FakeLlm:
    def __init__(self, response, finish_reason: str = "stop"):
        self.response = response
        self.finish_reason = finish_reason
        self._model = "test-model"
        self._system_prompt = ""
        self._messages = []
        self._client = MagicMock()
        self._client.chat.completions.create = _make_async_mock(response, finish_reason)

    def reset_context(self):
        pass


def _make_async_mock(response: str, finish_reason: str = "stop"):
    """Create an async mock that returns a chat completion response."""
    from unittest.mock import AsyncMock
    mock = AsyncMock()
    mock.return_value = _make_chat_response(response, finish_reason)
    return mock


class FailingLlm(FakeLlm):
    def __init__(self):
        super().__init__("")
        self._client.chat.completions.create = _make_failing_mock()


def _make_failing_mock():
    from unittest.mock import AsyncMock
    mock = AsyncMock()
    mock.side_effect = RuntimeError("llm unavailable")
    return mock


class FakeTts:
    def get_voice_for(self, name, yaml_voice=""):
        return "voice-id"

    def synthesize(self, text, voice="voice-id"):
        return f"https://audio.example/{voice}.mp3"


class FallbackTts:
    def __init__(self):
        self.calls = []

    def get_voice_for(self, name, yaml_voice=""):
        return "bad-voice"

    def synthesize(self, text, voice="voice-id"):
        self.calls.append(voice)
        if voice == "bad-voice":
            return None
        return f"https://audio.example/{voice}.mp3"


def make_engine(response="我觉得答案是A。"):
    return ClassmateEngine(FakePersona(), lambda name, prompt: FakeLlm(response))


def make_failing_engine():
    return ClassmateEngine(FakePersona(), lambda name, prompt: FailingLlm())


class TwoClassmatePersona(FakePersona):
    classmates = [
        {"name": "小明", "voice": "boy"},
        {"name": "小红", "voice": "girl"},
    ]


def test_xiaohong_descriptive_voice_maps_to_child_voice():
    client = TtsClient(api_key="test-key")

    voice = client.get_voice_for("小红", yaml_voice="可爱小女孩声音")

    assert voice != client.get_voice_for("小明", yaml_voice="活泼小男孩声音")
    assert voice.startswith("long")


@pytest.mark.asyncio
async def test_generate_interjection_returns_structured_message():
    result = await make_engine("QUESTION: 这里我有点好奇？").generate_interjection("小明", "刚讲完")

    assert result["speaker"] == "小明"
    assert result["kind"] == "interjection"
    assert result["intent"] == "question"
    assert result["text"] == "这里我有点好奇？"
    assert "audio_url" in result


@pytest.mark.asyncio
async def test_generate_interjection_marks_statement_intent():
    result = await make_engine("STATEMENT: 我看到红色的小手套了。").generate_interjection("小明", "刚讲完")

    assert result["speaker"] == "小明"
    assert result["kind"] == "interjection"
    assert result["intent"] == "statement"
    assert result["text"] == "我看到红色的小手套了。"


@pytest.mark.asyncio
async def test_interjection_parser_preserves_model_labeled_intent():
    result = await make_engine("STATEMENT: 老师，镜子真的能变出另一半吗？").generate_interjection("小明", "刚讲完")

    assert result["intent"] == "statement"
    assert result["text"] == "老师，镜子真的能变出另一半吗？"


@pytest.mark.asyncio
async def test_generate_interjection_strips_speaker_prefix_and_uses_tts_audio():
    engine = ClassmateEngine(
        FakePersona(),
        lambda name, prompt: FakeLlm("小明：我有个问题。"),
        tts_client=FakeTts(),
    )

    result = await engine.generate_interjection("小明", "刚讲完")

    assert result == {
        "speaker": "小明",
        "kind": "interjection",
        "intent": "statement",
        "text": "我有个问题。",
        "audio_url": "https://audio.example/voice-id.mp3",
    }


@pytest.mark.asyncio
async def test_incomplete_classmate_interjection_is_not_sent_to_tts():
    engine = ClassmateEngine(
        FakePersona(),
        lambda name, prompt: FakeLlm("老师老师，我照镜子的时候，举左手，镜子里"),
        tts_client=FakeTts(),
    )

    result = await engine.generate_interjection("小明", "镜子左右相反")

    assert result["text"] != "老师老师，我照镜子的时候，举左手，镜子里"
    assert result["text"] == "我有个问题，为什么不能反过来想呢？"
    assert result["audio_url"] == "https://audio.example/voice-id.mp3"


@pytest.mark.asyncio
async def test_length_truncated_classmate_output_is_rejected_by_finish_reason():
    engine = ClassmateEngine(
        FakePersona(),
        lambda name, prompt: FakeLlm("这句话看起来有句号。", finish_reason="length"),
        tts_client=FakeTts(),
    )

    result = await engine.generate_interjection("小明", "镜子左右相反")

    assert result["text"] == "我有个问题，为什么不能反过来想呢？"


@pytest.mark.asyncio
async def test_teaser_interjection_is_replaced_with_complete_fallback():
    engine = ClassmateEngine(
        FakePersona(),
        lambda name, prompt: FakeLlm("STATEMENT: 等一下等一下！我有个超酷的想法！"),
        tts_client=FakeTts(),
    )

    result = await engine.generate_interjection("小明", "刚讲完")

    assert result["text"] == "我有个问题，为什么不能反过来想呢？"
    assert "想法" not in result["text"]
    assert result["audio_url"] == "https://audio.example/voice-id.mp3"


def test_classmate_interjection_fallbacks_are_complete_not_teasers():
    forbidden = ("等一下等一下", "我有个超酷的想法", "我想到一个小发现", "我可以小声问")

    for fallback in ClassmateEngine._FALLBACKS.values():
        text = fallback["interjection"]
        assert not any(phrase in text for phrase in forbidden)


def test_incomplete_detection_does_not_use_dangling_word_list():
    from pathlib import Path

    source = Path("teaching/classmate_engine.py").read_text(encoding="utf-8")

    assert "dangling_endings" not in source


@pytest.mark.asyncio
async def test_generate_quiz_answer_returns_structured_message():
    result = await make_engine("我选A。").generate_quiz_answer("小明", "选哪个？")

    assert result == {
        "speaker": "小明",
        "kind": "quiz_guess",
        "text": "我选A。",
        "audio_url": None,
    }


@pytest.mark.asyncio
async def test_generate_quiz_answer_uses_tts_audio():
    engine = ClassmateEngine(
        FakePersona(),
        lambda name, prompt: FakeLlm("小明：我选A。"),
        tts_client=FakeTts(),
    )

    result = await engine.generate_quiz_answer("小明", "选哪个？")

    assert result == {
        "speaker": "小明",
        "kind": "quiz_guess",
        "text": "我选A。",
        "audio_url": "https://audio.example/voice-id.mp3",
    }


@pytest.mark.asyncio
async def test_generate_interaction_answer_uses_tts_audio():
    engine = ClassmateEngine(
        FakePersona(),
        lambda name, prompt: FakeLlm("小明：我觉得要先想一想。"),
        tts_client=FakeTts(),
    )

    result = await engine.generate_interaction_answer("小明", "你会怎么做？")

    assert result == {
        "speaker": "小明",
        "kind": "interaction_answer",
        "text": "我觉得要先想一想。",
        "audio_url": "https://audio.example/voice-id.mp3",
    }


@pytest.mark.asyncio
async def test_classmate_tts_retries_default_voice_when_configured_voice_fails():
    tts = FallbackTts()
    engine = ClassmateEngine(
        FakePersona(),
        lambda name, prompt: FakeLlm("QUESTION: 老师，我猜镜子里也是A对不对？"),
        tts_client=tts,
    )

    result = await engine.generate_interjection("小明", "刚讲完字母A")

    assert tts.calls == ["bad-voice", "longanhuan"]
    assert result["audio_url"] == "https://audio.example/longanhuan.mp3"


@pytest.mark.asyncio
async def test_generate_interjection_falls_back_when_llm_fails():
    result = await make_failing_engine().generate_interjection("小明", "老师刚讲了一个知识点")

    assert result["speaker"] == "小明"
    assert result["kind"] == "interjection"
    assert not result["text"].startswith("小明")
    assert result["audio_url"] is None


@pytest.mark.asyncio
async def test_generate_quiz_answer_falls_back_when_llm_fails():
    result = await make_failing_engine().generate_quiz_answer("小明", "选哪个？")

    assert result == {
        "speaker": "小明",
        "kind": "quiz_guess",
        "text": "我先猜一个，我觉得可能是A！",
        "audio_url": None,
    }


@pytest.mark.asyncio
async def test_repeated_classmate_text_falls_back_to_distinct_voice():
    engine = ClassmateEngine(
        TwoClassmatePersona(),
        lambda name, prompt: FakeLlm("老师，那镜子里会举左手吗？"),
    )

    first = await engine.generate_interjection("小明", "镜子")
    second = await engine.generate_interjection("小红", "镜子")

    assert first["text"] == "老师，那镜子里会举左手吗？"
    assert second["speaker"] == "小红"
    assert second["text"] != first["text"]
    assert second["text"] == "老师老师，我发现要先看线索，再做决定。"
