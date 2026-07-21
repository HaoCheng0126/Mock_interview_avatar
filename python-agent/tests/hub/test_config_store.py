"""Tests for hub.config_store — settings persistence, masking, agent env building."""

import json
import os
import stat

import pytest

from hub.config_store import (
    AGENTS,
    MASK_PREFIX,
    apply_avatar_platform_update,
    apply_update,
    build_agent_env,
    default_settings,
    effective_avatar_platform,
    load_settings,
    mask_settings,
    report_llm,
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
        "provider": "deepseek",
        "profiles": {
            "deepseek": {
                "api_key": "sk-test-llm-5678",
                "base_url": "https://api.example.com",
                "model": "test-model",
            },
            "volcengine": {
                "api_key": "ark-test-9012",
                "base_url": "https://ark.example.com/api/v3",
                "model": "doubao-test",
            },
        },
    },
    "asr": {
        "provider": "volcengine",
        "dashscope_api_key": "sk-test-asr-9012",
        "model": "qwen3-asr-flash-realtime",
        "volc_app_id": "app-test-1234",
        "volc_access_token": "tok-test-5678",
        "volc_secret_key": "sec-test-9012",
        "volc_cluster": "volcengine_streaming_common",
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
    assert set(settings) >= {"platform", "llm", "asr", "interview", "agents"}
    # official guide + SDK default endpoint
    assert settings["platform"]["base_url"] == "https://facemarket.ai/vih/dispatcher"
    assert settings["platform"]["sandbox"] == ""
    assert settings["platform"]["voice_speed"] == ""
    assert settings["platform"]["avatar_profiles"] == {}
    assert settings["llm"]["provider"] == "deepseek"
    assert settings["llm"]["profiles"]["deepseek"]["base_url"] == "https://api.deepseek.com"
    assert settings["llm"]["profiles"]["deepseek"]["model"] == "deepseek-v4-flash"
    assert settings["llm"]["profiles"]["volcengine"]["base_url"].endswith("/api/v3")
    assert settings["interview"]["report_llm_provider"] == "deepseek"
    assert settings["interview"]["report_prompt_modules"]["role_and_style"]
    for name in AGENTS:
        assert settings["agents"][name]["port"] == AGENTS[name]["default_port"]


def test_load_settings_missing_file_returns_defaults(tmp_path):
    settings = load_settings(tmp_path / "nope.json")
    assert settings == default_settings()


def test_load_settings_merges_partial_file_with_defaults(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"llm": {"model": "custom-model"}}))
    settings = load_settings(path)
    assert settings["llm"]["profiles"]["deepseek"]["model"] == "custom-model"
    assert settings["llm"]["profiles"]["deepseek"]["base_url"] == "https://api.deepseek.com"
    assert settings["platform"]["api_key"] == ""


def test_load_settings_corrupt_file_returns_defaults(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json")
    assert load_settings(path) == default_settings()


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "s.json"
    save_settings(SAMPLE, path)
    assert load_settings(path)["platform"]["api_key"] == "lk_test_abcd1234"
    assert load_settings(path)["asr"]["volc_app_id"] == "app-test-1234"


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
    assert masked["llm"]["profiles"]["deepseek"]["api_key"] == MASK_PREFIX + "5678"
    assert masked["llm"]["profiles"]["volcengine"]["api_key"] == MASK_PREFIX + "9012"
    assert masked["asr"]["dashscope_api_key"] == MASK_PREFIX + "9012"
    assert masked["asr"]["volc_access_token"] == MASK_PREFIX + "5678"
    assert masked["asr"]["volc_secret_key"] == MASK_PREFIX + "9012"
    assert masked["asr"]["volc_app_id"] == "app-test-1234"  # app id not secret
    assert masked["platform"]["avatar_id"] == "avatar_test_001"
    assert masked["llm"]["profiles"]["deepseek"]["model"] == "test-model"
    # original untouched (immutability)
    assert SAMPLE["platform"]["api_key"] == "lk_test_abcd1234"


def test_mask_settings_empty_secret_stays_empty():
    settings = default_settings()
    assert mask_settings(settings)["platform"]["api_key"] == ""


def test_avatar_platform_defaults_to_global_and_can_use_independent_credentials():
    settings = default_settings()
    settings["platform"].update(
        {
            "api_key": "lk-global-1234",
            "base_url": "https://global.example.com/dispatcher",
            "sandbox": "",
        }
    )
    inherited = effective_avatar_platform(settings, "avatar-a")
    assert inherited == {
        "use_global": True,
        "api_key": "lk-global-1234",
        "base_url": "https://global.example.com/dispatcher",
        "sandbox": "",
    }

    updated = apply_avatar_platform_update(
        settings,
        "avatar-a",
        {
            "use_global": False,
            "api_key": "lk-custom-9876",
            "base_url": "https://custom.example.com/dispatcher/",
            "sandbox": "true",
        },
    )
    custom = effective_avatar_platform(updated, "avatar-a")
    assert custom == {
        "use_global": False,
        "api_key": "lk-custom-9876",
        "base_url": "https://custom.example.com/dispatcher",
        "sandbox": "true",
    }
    assert effective_avatar_platform(updated, "avatar-b")["use_global"] is True
    assert (
        mask_settings(updated)["platform"]["avatar_profiles"]["avatar-a"]["api_key"]
        == MASK_PREFIX + "9876"
    )


def test_avatar_platform_masked_key_is_preserved_and_global_choice_removes_override():
    settings = apply_avatar_platform_update(
        default_settings(),
        "avatar-a",
        {
            "use_global": False,
            "api_key": "lk-custom-9876",
            "base_url": "https://custom.example.com",
            "sandbox": "",
        },
    )
    updated = apply_avatar_platform_update(
        settings,
        "avatar-a",
        {
            "use_global": False,
            "api_key": MASK_PREFIX + "9876",
            "base_url": "https://new.example.com",
            "sandbox": "",
        },
    )
    assert effective_avatar_platform(updated, "avatar-a")["api_key"] == "lk-custom-9876"
    inherited = apply_avatar_platform_update(
        updated, "avatar-a", {"use_global": True}
    )
    assert effective_avatar_platform(inherited, "avatar-a")["use_global"] is True
    assert "avatar-a" not in inherited["platform"]["avatar_profiles"]


def test_avatar_platform_independent_mode_requires_key_and_url():
    with pytest.raises(ValueError, match="API Key"):
        apply_avatar_platform_update(
            default_settings(),
            "avatar-a",
            {"use_global": False, "api_key": "", "base_url": "https://example.com"},
        )
    with pytest.raises(ValueError, match="平台地址"):
        apply_avatar_platform_update(
            default_settings(),
            "avatar-a",
            {"use_global": False, "api_key": "lk-test", "base_url": ""},
        )


def test_apply_update_masked_sentinel_keeps_old_secret():
    incoming = {"platform": {"api_key": MASK_PREFIX + "1234", "avatar_id": "new_av"}}
    updated = apply_update(SAMPLE, incoming)
    assert updated["platform"]["api_key"] == "lk_test_abcd1234"
    assert updated["platform"]["avatar_id"] == "new_av"
    # original untouched (immutability)
    assert SAMPLE["platform"]["avatar_id"] == "avatar_test_001"


def test_apply_update_new_secret_replaces_and_empty_clears():
    updated = apply_update(
        SAMPLE,
        {"llm": {"profiles": {"deepseek": {"api_key": "sk-new"}}}},
    )
    assert updated["llm"]["profiles"]["deepseek"]["api_key"] == "sk-new"
    assert updated["llm"]["profiles"]["volcengine"]["api_key"] == "ark-test-9012"
    cleared = apply_update(
        SAMPLE,
        {"llm": {"profiles": {"deepseek": {"api_key": ""}}}},
    )
    assert cleared["llm"]["profiles"]["deepseek"]["api_key"] == ""


def test_apply_update_switches_provider_without_overwriting_profiles():
    updated = apply_update(SAMPLE, {"llm": {"provider": "volcengine"}})
    assert updated["llm"]["provider"] == "volcengine"
    assert updated["llm"]["profiles"]["deepseek"]["api_key"] == "sk-test-llm-5678"
    assert updated["llm"]["profiles"]["volcengine"]["api_key"] == "ark-test-9012"


def test_load_settings_migrates_legacy_volcengine_shape(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({
        "llm": {
            "api_key": "ark-legacy",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "model": "doubao-legacy",
        }
    }))
    settings = load_settings(path)
    assert settings["llm"]["provider"] == "volcengine"
    assert settings["llm"]["profiles"]["volcengine"]["api_key"] == "ark-legacy"
    assert settings["llm"]["profiles"]["deepseek"]["base_url"] == "https://api.deepseek.com"


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
    settings = apply_update(
        default_settings(),
        {
            **SAMPLE,
            "interview": {
                "report_prompt_modules": {
                    "role_and_style": "模块A",
                    "output_contract": "模块B",
                }
            },
        },
    )
    env = build_agent_env(settings, "interview")
    assert env["LIVEAVATAR_API_KEY"] == "lk_test_abcd1234"
    assert env["LIVEAVATAR_AVATAR_ID"] == "avatar_test_001"
    assert env["LIVEAVATAR_VOICE_ID"] == "voice_test_001"
    assert env["LIVEAVATAR_BASE_URL"] == "https://example.com/vih/dispatcher"
    assert env["DEEPSEEK_API_KEY"] == "sk-test-llm-5678"
    assert env["DEEPSEEK_BASE_URL"] == "https://api.example.com"
    assert env["DEEPSEEK_MODEL"] == "test-model"
    assert "SYSTEM_PROMPT" not in env  # dropped: interview persona comes from YAML
    assert env["ASR_PROVIDER"] == "volcengine"
    assert env["DASHSCOPE_API_KEY"] == "sk-test-asr-9012"
    assert env["DASHSCOPE_ASR_MODEL"] == "qwen3-asr-flash-realtime"
    assert env["VOLC_ASR_APP_ID"] == "app-test-1234"
    assert env["VOLC_ASR_ACCESS_TOKEN"] == "tok-test-5678"
    assert env["VOLC_ASR_CLUSTER"] == "volcengine_streaming_common"
    assert env["INTERVIEW_HTTP_PORT"] == "8083"
    assert "模块A" in env["INTERVIEW_GLOBAL_REPORT_PROMPT"]
    assert "模块B" in env["INTERVIEW_GLOBAL_REPORT_PROMPT"]


def test_compiled_report_overview_contract_matches_required_sections():
    env = build_agent_env(default_settings(), "interview")
    prompt = env["INTERVIEW_GLOBAL_REPORT_OVERVIEW_PROMPT"]

    assert "不要输出 cover" in prompt
    for required_key in (
        '"summary"',
        '"evidenceRefs"',
        '"highlights"',
        '"dimensions"',
        '"dimensionCommentaries"',
        '"learningPlan"',
    ):
        assert required_key in prompt
    for phase in ("立即行动", "短期提升", "中期规划"):
        assert f'"title":"{phase}"' in prompt


def test_build_agent_env_uses_per_agent_port_var_and_settings_port():
    settings = default_settings()
    settings = apply_update(settings, {"agents": {"interview": {"port": 9083}}})
    env = build_agent_env(settings, "interview")
    assert env["INTERVIEW_HTTP_PORT"] == "9083"
    assert "HTTP_PORT" not in env


def test_build_agent_env_uses_selected_volcengine_profile():
    settings = apply_update(SAMPLE, {"llm": {"provider": "volcengine"}})
    env = build_agent_env(settings, "interview")
    assert env["DEEPSEEK_API_KEY"] == "ark-test-9012"
    assert env["DEEPSEEK_BASE_URL"] == "https://ark.example.com/api/v3"
    assert env["DEEPSEEK_MODEL"] == "doubao-test"


def test_report_llm_is_independent_from_active_chat_model():
    base = apply_update(default_settings(), SAMPLE)
    settings = apply_update(
        base,
        {
            "llm": {"provider": "volcengine"},
            "interview": {"report_llm_provider": "deepseek"},
        },
    )
    selected = report_llm(settings)
    env = build_agent_env(settings, "interview")
    assert selected["provider"] == "deepseek"
    assert selected["fallback"] is False
    assert env["DEEPSEEK_MODEL"] == "doubao-test"
    assert env["REPORT_LLM_MODEL"] == "test-model"
    assert env["REPORT_LLM_PROVIDER"] == "deepseek"
    assert env["REPORT_LLM_FALLBACK"] == "0"


def test_report_llm_falls_back_to_active_model_when_key_is_missing():
    base = apply_update(default_settings(), SAMPLE)
    settings = apply_update(
        base,
        {
            "llm": {
                "provider": "volcengine",
                "profiles": {"deepseek": {"api_key": ""}},
            },
            "interview": {"report_llm_provider": "deepseek"},
        },
    )
    selected = report_llm(settings)
    env = build_agent_env(settings, "interview")
    assert selected["requested_provider"] == "deepseek"
    assert selected["provider"] == "volcengine"
    assert selected["fallback"] is True
    assert env["REPORT_LLM_MODEL"] == "doubao-test"
    assert env["REPORT_LLM_FALLBACK"] == "1"


def test_build_agent_env_omits_empty_values():
    env = build_agent_env(default_settings(), "interview")
    assert "LIVEAVATAR_API_KEY" not in env
    assert "LIVEAVATAR_VOICE_ID" not in env
    assert "LIVEAVATAR_SANDBOX" not in env
    assert "LIVEAVATAR_VOICE_SPEED" not in env
    assert "VOLC_ASR_APP_ID" not in env  # empty volc creds omitted
    # non-empty defaults still pass through
    assert env["DEEPSEEK_BASE_URL"] == "https://api.deepseek.com"
    assert env["ASR_PROVIDER"] == "dashscope"  # default provider always present


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
