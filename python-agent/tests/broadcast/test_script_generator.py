"""Tests for ScriptGenerator — JSON mode with retry-on-truncation."""

from unittest.mock import AsyncMock, MagicMock
import pytest
from broadcast.script_generator import ScriptGenerator


SCRIPT_GEN_PROMPT = """你是一个专业的电商直播带货主播。请根据以下商品信息，生成5段口播脚本。
每段脚本50-200字，适合口播，语气热情有感染力。
分段时注意：
- 每段脚本对应一段展示视频
- 内容涵盖：开场吸引、产品卖点、使用场景、价格优惠、限时催单
- 用口语化中文，多用感叹词（姐妹们、家人们、真的太...了）

商品信息：
{product_info}

请返回JSON格式：["脚本1", "脚本2", ...]"""


def _make_llm_response(content: str, finish_reason: str = "stop"):
    """Build a mock response object matching OpenAI chat completions shape."""
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = finish_reason
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_mock_llm(return_content: str = "", finish_reason: str = "stop",
                   side_effect=None):
    """Create a mock LlmClient with _client.chat.completions.create mocked."""
    mock_llm = MagicMock()
    mock_llm._model = "deepseek-v4-flash"
    mock_create = AsyncMock()
    if side_effect:
        mock_create.side_effect = side_effect
    else:
        mock_create.return_value = _make_llm_response(return_content, finish_reason)
    mock_llm._client.chat.completions.create = mock_create
    return mock_llm


@pytest.mark.asyncio
async def test_generate_scripts_parses_json_array():
    mock_llm = _make_mock_llm(
        '["脚本一内容", "脚本二内容", "脚本三内容", "脚本四内容", "脚本五内容"]'
    )

    generator = ScriptGenerator(llm_client=mock_llm, prompt_template=SCRIPT_GEN_PROMPT)
    name, scripts = await generator.generate(
        url="https://example.com/product",
        product_info="XX气垫粉底，价格99元，遮瑕力强",
    )

    assert len(scripts) == 5
    assert scripts[0] == "脚本一内容"
    assert scripts[4] == "脚本五内容"
    # Called with json_object response_format
    call_kwargs = mock_llm._client.chat.completions.create.call_args.kwargs
    assert call_kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_generate_scripts_uses_custom_template_and_system_prompt():
    mock_llm = _make_mock_llm('["风险教育脚本"]')

    generator = ScriptGenerator(
        llm_client=mock_llm,
        prompt_template="生成泛行情解说：{product_info}",
        system_prompt="你是虚拟币风险教育主播。只输出JSON数组。",
    )
    _, scripts = await generator.generate(product_info="BTC 波动风险")

    assert scripts == ["风险教育脚本"]
    call_kwargs = mock_llm._client.chat.completions.create.call_args.kwargs
    messages = call_kwargs["messages"]
    assert messages[0]["content"] == "你是虚拟币风险教育主播。只输出JSON数组。"
    assert messages[1]["content"] == "生成泛行情解说：BTC 波动风险"


@pytest.mark.asyncio
async def test_generate_scripts_handles_markdown_fence():
    """_parse_response strips ``` fences before JSON parsing."""
    mock_llm = _make_mock_llm(
        '```json\n["开场脚本", "卖点脚本", "促销脚本"]\n```'
    )

    generator = ScriptGenerator(llm_client=mock_llm)
    name, scripts = await generator.generate(url="https://example.com/product")

    assert len(scripts) == 3
    assert scripts == ["开场脚本", "卖点脚本", "促销脚本"]


@pytest.mark.asyncio
async def test_generate_scripts_llm_failure():
    mock_llm = _make_mock_llm(side_effect=Exception("API error"))

    generator = ScriptGenerator(llm_client=mock_llm)
    name, scripts = await generator.generate(url="https://example.com/product")

    assert scripts == []
    assert name == ""


@pytest.mark.asyncio
async def test_generate_scripts_retries_on_truncation():
    """When first attempt returns truncated JSON, retries with 2x max_tokens."""
    call_count = 0

    async def mock_create(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Truncated — missing closing brackets
            return _make_llm_response(
                '["脚本一", "脚本二", "脚本三"',
                finish_reason="length",
            )
        else:
            return _make_llm_response(
                '["脚本一", "脚本二", "脚本三"]',
                finish_reason="stop",
            )

    mock_llm = MagicMock()
    mock_llm._model = "deepseek-v4-flash"
    mock_llm._client.chat.completions.create = AsyncMock(side_effect=mock_create)

    generator = ScriptGenerator(llm_client=mock_llm)
    name, scripts = await generator.generate(url="https://example.com/product")

    assert len(scripts) == 3
    assert call_count == 2  # first truncated → retried successfully
    # Second call should have 2x max_tokens
    assert mock_llm._client.chat.completions.create.call_args_list[1].kwargs["max_tokens"] == 4096
