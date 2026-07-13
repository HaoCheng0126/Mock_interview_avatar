from unittest.mock import AsyncMock, MagicMock

import pytest

from talkshow.script_generator import TalkshowScriptGenerator
from talkshow.show_manager import Persona, Segment, Show, Topic


def _make_llm_response(content: str, finish_reason: str = "stop"):
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = finish_reason
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_mock_llm(content: str):
    llm = MagicMock()
    llm._model = "deepseek-v4-flash"
    llm._client.chat.completions.create = AsyncMock(
        return_value=_make_llm_response(content)
    )
    return llm


def test_parse_batch_accepts_segments_and_bridges():
    raw = """
    {
      "batch_title": "职场玄学观察",
      "segments": [
        {
          "topic_id": "workplace",
          "title": "会议室里的时间黑洞",
          "beats": ["开场观察", "黑话递进", "纪要包袱"],
          "text": "会议室有一种特殊的物理规则，只要门一关，时间就开始打折。"
        },
        {
          "topic_id": "city_life",
          "title": "地铁里的社交礼仪",
          "text": "早高峰地铁是城市里最公平的地方，进去以后大家统一变成压缩文件。"
        }
      ],
      "bridges": [
        {
          "from_title": "会议室里的时间黑洞",
          "to_title": "地铁里的社交礼仪",
          "text": "说到时间被偷走，地铁也不甘示弱。"
        }
      ]
    }
    """

    batch = TalkshowScriptGenerator.parse_batch(raw)

    assert batch.batch_title == "职场玄学观察"
    assert len(batch.segments) == 2
    assert batch.segments[0].beats == ["开场观察", "黑话递进", "纪要包袱"]
    assert batch.bridges[0].text == "说到时间被偷走，地铁也不甘示弱。"


def test_parse_batch_strips_markdown_fence():
    raw = """```json
    {"batch_title":"一批","segments":[{"topic_id":"workplace","title":"标题","text":"正文内容"}],"bridges":[]}
    ```"""

    batch = TalkshowScriptGenerator.parse_batch(raw)

    assert batch.batch_title == "一批"
    assert batch.segments[0].title == "标题"


def test_parse_batch_rejects_empty_segments():
    with pytest.raises(ValueError, match="segments"):
        TalkshowScriptGenerator.parse_batch('{"batch_title":"空","segments":[]}')


def test_build_fallback_batch_uses_config_segments():
    fallback = [
        Segment(topic_id="workplace", title="备用", text="备用段子正文"),
    ]

    batch = TalkshowScriptGenerator.build_fallback_batch(fallback)

    assert batch.batch_title == "fallback"
    assert batch.segments == fallback
    assert batch.bridges == []


@pytest.mark.asyncio
async def test_generate_batch_calls_llm_with_json_mode():
    llm = _make_mock_llm(
        '{"batch_title":"一批","segments":[{"topic_id":"workplace","title":"标题","text":"正文内容"}],"bridges":[]}'
    )
    generator = TalkshowScriptGenerator(llm)

    batch = await generator.generate_batch(
        persona=Persona(name="阿麦", style="轻微自嘲"),
        show=Show(title="今晚不加班", opening="开场"),
        topics=[Topic(id="workplace", title="职场日常", description="会议")],
        recent_segments=[],
        batch_size=1,
        lang="zh",
    )

    assert batch.segments[0].title == "标题"
    kwargs = llm._client.chat.completions.create.call_args.kwargs
    assert kwargs["response_format"] == {"type": "json_object"}
    prompt = kwargs["messages"][1]["content"]
    assert "bridge 40-120个中文字符" in prompt
    assert "callback" in prompt
    assert "pivot" in prompt
    assert "包袱句单独成行" in prompt
    assert "重音点" in prompt
    assert "停顿点" in prompt
