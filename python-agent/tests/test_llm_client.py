import pytest
from llm_client import LlmClient


def test_llm_client_init_defaults():
    client = LlmClient()
    assert client._model == "deepseek-v4-flash"
    assert client._base_url == "https://api.deepseek.com"
    assert client._system_prompt == ""


def test_llm_client_init_custom():
    client = LlmClient(
        api_key="sk-test",
        base_url="https://custom.api.com",
        model="custom-model",
        system_prompt="You are a helpful shopping assistant.",
    )
    assert client._model == "custom-model"
    assert client._base_url == "https://custom.api.com"
    assert client._system_prompt == "You are a helpful shopping assistant."


def test_llm_client_reset_context():
    client = LlmClient(system_prompt="System")
    client._messages = [
        {"role": "system", "content": "System"},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
    ]
    client.reset_context()
    assert len(client._messages) == 1
    assert client._messages[0] == {"role": "system", "content": "System"}
