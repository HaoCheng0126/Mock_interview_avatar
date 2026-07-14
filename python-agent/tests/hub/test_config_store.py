"""Tests for hub.config_store — settings persistence, masking, agent env building."""

import json
import os
import stat

import pytest

from hub.config_store import (
    AGENTS,
    MASK_PREFIX,
    apply_update,
    build_agent_env,
    default_settings,
    load_settings,
    mask_settings,
    save_settings,
)


SAMPLE = {
    "platform": {
        "api_key": "lk_test_abcd1234",
        "avatar_id": "avatar_test_001",
        "voice_id": "voice_test_001",
        "base_url": "https://example.com/vih/dispatcher",
    },
    "llm": {
        "api_key": "sk-test-llm-5678",
        "base_url": "https://api.example.com",
        "model": "test-model",
        "system_prompt": "你是测试助手",
    },
    "asr": {
        "dashscope_api_key": "sk-test-asr-9012",
        "model": "qwen3-asr-flash-realtime",
    },
    "agents": {
        "interview": {"port": 8083},
    },
}


# ---------------------------------------------------------------------------
# defaults / load / save
# ---------------------------------------------------------------------------


def test_default_settings_has_all_sections():
    settings = default_settings()
    assert set(settings) >= {"platform", "llm", "asr", "agents"}
    # official guide + SDK default endpoint
    assert settings["platform"]["base_url"] == "https://facemarket.ai/vih/dispatcher"
    assert settings["platform"]["sandbox"] == ""
    assert settings["platform"]["voice_speed"] == ""
    assert settings["llm"]["base_url"] == "https://api.deepseek.com"
    assert settings["llm"]["model"] == "deepseek-v4-flash"
    for name in AGENTS:
        assert settings["agents"][name]["port"] == AGENTS[name]["default_port"]


def test_load_settings_missing_file_returns_defaults(tmp_path):
    settings = load_settings(tmp_path / "nope.json")
    assert settings == default_settings()


def test_load_settings_merges_partial_file_with_defaults(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"llm": {"model": "custom-model"}}))
    settings = load_settings(path)
    assert settings["llm"]["model"] == "custom-model"
    assert settings["llm"]["base_url"] == "https://api.deepseek.com"
    assert settings["platform"]["api_key"] == ""


def test_load_settings_corrupt_file_returns_defaults(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json")
    assert load_settings(path) == default_settings()


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "s.json"
    save_settings(SAMPLE, path)
    assert load_settings(path)["platform"]["api_key"] == "lk_test_abcd1234"
    assert load_settings(path)["llm"]["system_prompt"] == "你是测试助手"


def test_save_settings_drops_unknown_keys(tmp_path):
    path = tmp_path / "s.json"
    dirty = {**SAMPLE, "evil": {"x": 1}, "llm": {**SAMPLE["llm"], "extra": "y"}}
    save_settings(dirty, path)
    stored = json.loads(path.read_text())
    assert "evil" not in stored
    assert "extra" not in stored["llm"]


def test_save_settings_file_is_owner_only(tmp_path):
    path = tmp_path / "s.json"
    save_settings(SAMPLE, path)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


# ---------------------------------------------------------------------------
# masking / update
# ---------------------------------------------------------------------------


def test_mask_settings_hides_secrets_keeps_rest():
    masked = mask_settings(SAMPLE)
    assert masked["platform"]["api_key"] == MASK_PREFIX + "1234"
    assert masked["llm"]["api_key"] == MASK_PREFIX + "5678"
    assert masked["asr"]["dashscope_api_key"] == MASK_PREFIX + "9012"
    assert masked["platform"]["avatar_id"] == "avatar_test_001"
    assert masked["llm"]["model"] == "test-model"
    # original untouched (immutability)
    assert SAMPLE["platform"]["api_key"] == "lk_test_abcd1234"


def test_mask_settings_empty_secret_stays_empty():
    settings = default_settings()
    assert mask_settings(settings)["platform"]["api_key"] == ""


def test_apply_update_masked_sentinel_keeps_old_secret():
    incoming = {"platform": {"api_key": MASK_PREFIX + "1234", "avatar_id": "new_av"}}
    updated = apply_update(SAMPLE, incoming)
    assert updated["platform"]["api_key"] == "lk_test_abcd1234"
    assert updated["platform"]["avatar_id"] == "new_av"
    # original untouched (immutability)
    assert SAMPLE["platform"]["avatar_id"] == "avatar_test_001"


def test_apply_update_new_secret_replaces_and_empty_clears():
    updated = apply_update(SAMPLE, {"llm": {"api_key": "sk-new"}})
    assert updated["llm"]["api_key"] == "sk-new"
    cleared = apply_update(SAMPLE, {"llm": {"api_key": ""}})
    assert cleared["llm"]["api_key"] == ""


def test_apply_update_ignores_unknown_and_coerces_port():
    updated = apply_update(SAMPLE, {"bogus": {"a": 1}, "agents": {"interview": {"port": "9090"}}})
    assert "bogus" not in updated
    assert updated["agents"]["interview"]["port"] == 9090


def test_apply_update_rejects_bad_port():
    with pytest.raises(ValueError):
        apply_update(SAMPLE, {"agents": {"interview": {"port": "abc"}}})
    with pytest.raises(ValueError):
        apply_update(SAMPLE, {"agents": {"interview": {"port": 99}}})


# ---------------------------------------------------------------------------
# agent registry / env building
# ---------------------------------------------------------------------------


def test_agents_registry_shape():
    assert set(AGENTS) == {"interview"}
    for spec in AGENTS.values():
        assert spec["script"].endswith("agent.py")
        assert spec["port_env"]
        assert isinstance(spec["default_port"], int)
        assert spec["label"]
    ports = [spec["default_port"] for spec in AGENTS.values()]
    assert len(ports) == len(set(ports)), "default ports must not clash"


def test_build_agent_env_maps_settings_to_env_vars():
    env = build_agent_env(SAMPLE, "interview")
    assert env["LIVEAVATAR_API_KEY"] == "lk_test_abcd1234"
    assert env["LIVEAVATAR_AVATAR_ID"] == "avatar_test_001"
    assert env["LIVEAVATAR_VOICE_ID"] == "voice_test_001"
    assert env["LIVEAVATAR_BASE_URL"] == "https://example.com/vih/dispatcher"
    assert env["DEEPSEEK_API_KEY"] == "sk-test-llm-5678"
    assert env["DEEPSEEK_BASE_URL"] == "https://api.example.com"
    assert env["DEEPSEEK_MODEL"] == "test-model"
    assert env["SYSTEM_PROMPT"] == "你是测试助手"
    assert env["DASHSCOPE_API_KEY"] == "sk-test-asr-9012"
    assert env["DASHSCOPE_ASR_MODEL"] == "qwen3-asr-flash-realtime"
    assert env["INTERVIEW_HTTP_PORT"] == "8083"


def test_build_agent_env_uses_per_agent_port_var_and_settings_port():
    settings = default_settings()
    settings = apply_update(settings, {"agents": {"interview": {"port": 9083}}})
    env = build_agent_env(settings, "interview")
    assert env["INTERVIEW_HTTP_PORT"] == "9083"
    assert "HTTP_PORT" not in env


def test_build_agent_env_omits_empty_values():
    env = build_agent_env(default_settings(), "interview")
    assert "LIVEAVATAR_API_KEY" not in env
    assert "LIVEAVATAR_VOICE_ID" not in env
    assert "LIVEAVATAR_SANDBOX" not in env
    assert "LIVEAVATAR_VOICE_SPEED" not in env
    assert "SYSTEM_PROMPT" not in env
    # non-empty defaults still pass through
    assert env["DEEPSEEK_BASE_URL"] == "https://api.deepseek.com"


def test_build_agent_env_maps_sandbox_and_voice_speed():
    settings = apply_update(
        default_settings(),
        {"platform": {"sandbox": "true", "voice_speed": "1.2"}},
    )
    env = build_agent_env(settings, "interview")
    assert env["LIVEAVATAR_SANDBOX"] == "true"
    assert env["LIVEAVATAR_VOICE_SPEED"] == "1.2"


def test_build_agent_env_unknown_agent_raises():
    with pytest.raises(KeyError):
        build_agent_env(SAMPLE, "nope")
