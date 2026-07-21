"""Settings persistence for the hub console.

Stores platform / LLM / ASR credentials and per-agent ports in
``config/hub_settings.json`` (owner-only permissions, gitignored) and maps
them onto the environment variables each agent already reads — the agents
themselves need no code changes.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from interview.prompts import (
    DEFAULT_REPORT_PROMPT_MODULES,
    build_report_overview_prompt,
    build_report_prompt_from_modules,
    build_report_qa_prompt,
)

MASK_PREFIX = "••••"
MIN_PORT, MAX_PORT = 1024, 65535

DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "config" / "hub_settings.json"

# Registry of launchable agents. ``script`` is relative to python-agent/.
# Product focus is the interview avatar — other agents' source stays in the
# repo (chat/ broadcast/ teaching/ talkshow/) and can be re-registered here.
AGENTS: dict[str, dict[str, Any]] = {
    "interview": {
        "label": "模拟面试",
        "desc": "结构化提问 → 语音作答 → 评估追问 → 生成报告",
        "script": "interview/agent.py",
        "port_env": "INTERVIEW_HTTP_PORT",
        "default_port": 8083,
        "page": "interview.html",
    },
}

# (section, field) pairs masked in API responses.
SECRET_FIELDS = {
    ("platform", "api_key"),
    ("asr", "dashscope_api_key"),
    ("asr", "volc_access_token"),
    ("asr", "volc_secret_key"),
}
AVATAR_PLATFORM_FIELDS = ("api_key", "base_url", "sandbox")
LLM_PROVIDERS = ("deepseek", "volcengine")
LLM_PROVIDER_DEFAULTS = {
    "deepseek": {
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
    },
    "volcengine": {
        "api_key": "",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-1-turbo-260628",
    },
}

# ASR providers selectable in the console.
ASR_PROVIDERS = ("dashscope", "volcengine")
REPORT_PROMPT_MODULE_KEYS = tuple(DEFAULT_REPORT_PROMPT_MODULES)


def _default_report_prompt_modules() -> dict[str, str]:
    return copy.deepcopy(DEFAULT_REPORT_PROMPT_MODULES)


def _sanitize_report_prompt_modules(value: Any) -> dict[str, str]:
    modules = _default_report_prompt_modules()
    if not isinstance(value, dict):
        return modules
    for key in REPORT_PROMPT_MODULE_KEYS:
        if key in value:
            modules[key] = str(value[key] or "")
    return modules


def default_settings() -> dict[str, Any]:
    return {
        "platform": {
            "api_key": "",
            "avatar_id": "",
            "voice_id": "",
            "base_url": "https://facemarket.ai/vih/dispatcher",
            "sandbox": "",
            "voice_speed": "",
            # Per-avatar credentials stay in this owner-only, gitignored file.
            # A missing profile means the avatar inherits the global platform.
            "avatar_profiles": {},
        },
        "llm": {
            "provider": "deepseek",
            "profiles": copy.deepcopy(LLM_PROVIDER_DEFAULTS),
        },
        "asr": {
            "provider": "dashscope",
            # DashScope Qwen
            "dashscope_api_key": "",
            "model": "qwen3-asr-flash-realtime",
            # Volcengine streaming ASR (豆包语音)
            "volc_app_id": "",
            "volc_access_token": "",
            "volc_secret_key": "",
            "volc_cluster": "volcengine_streaming_common",
        },
        "interview": {
            # Reports can use a model independently from the real-time interview.
            # DeepSeek is preferred for long-form analysis; when its key has not
            # been configured, build_agent_env falls back to the active chat model.
            "report_llm_provider": "deepseek",
            "report_prompt": "",
            "report_prompt_modules": _default_report_prompt_modules(),
        },
        "agents": {
            name: {"port": spec["default_port"]} for name, spec in AGENTS.items()
        },
    }


def _normalize_llm_provider(value: Any) -> str:
    provider = str(value or "").strip().lower()
    aliases = {"ark": "volcengine", "volcano": "volcengine", "doubao": "volcengine"}
    provider = aliases.get(provider, provider)
    return provider if provider in LLM_PROVIDERS else "deepseek"


def _infer_llm_provider(base_url: Any) -> str:
    url = str(base_url or "").lower()
    return "volcengine" if any(token in url for token in ("volces.com", "volcengine", "ark.")) else "deepseek"


def _sanitize_llm(value: Any) -> dict[str, Any]:
    """Normalize the two-provider LLM config and migrate the legacy flat shape."""
    result = {
        "provider": "deepseek",
        "profiles": copy.deepcopy(LLM_PROVIDER_DEFAULTS),
    }
    if not isinstance(value, dict):
        return result

    profiles = value.get("profiles")
    if isinstance(profiles, dict):
        for provider in LLM_PROVIDERS:
            incoming = profiles.get(provider)
            if not isinstance(incoming, dict):
                continue
            for field in ("api_key", "base_url", "model"):
                if field in incoming:
                    result["profiles"][provider][field] = str(incoming[field] or "")

    # Backward compatibility: the original console stored one flat LLM config.
    # Migrate it into the matching provider slot without touching the other slot.
    legacy_fields = ("api_key", "base_url", "model")
    if any(field in value for field in legacy_fields):
        provider = _normalize_llm_provider(
            value.get("provider") or _infer_llm_provider(value.get("base_url"))
        )
        for field in legacy_fields:
            if field in value:
                result["profiles"][provider][field] = str(value[field] or "")
        result["provider"] = provider
    else:
        result["provider"] = _normalize_llm_provider(value.get("provider"))
    return result


def active_llm(settings: dict[str, Any]) -> dict[str, str]:
    """Return the selected OpenAI-compatible LLM profile."""
    llm = _sanitize_llm(settings.get("llm"))
    provider = llm["provider"]
    return {"provider": provider, **llm["profiles"][provider]}


def report_llm(settings: dict[str, Any]) -> dict[str, str | bool]:
    """Return the effective report model without breaking existing installs.

    The requested profile is used when it has a key. Otherwise reports fall back
    to the currently active chat profile, and the caller can expose that state.
    """
    llm = _sanitize_llm(settings.get("llm"))
    interview = settings.get("interview")
    requested = _normalize_llm_provider(
        interview.get("report_llm_provider")
        if isinstance(interview, dict)
        else "deepseek"
    )
    selected = llm["profiles"][requested]
    fallback = not bool(str(selected.get("api_key") or "").strip())
    actual = llm["provider"] if fallback else requested
    profile = llm["profiles"][actual]
    return {
        "requested_provider": requested,
        "provider": actual,
        "fallback": fallback,
        "api_key": profile["api_key"],
        "base_url": profile["base_url"],
        "model": profile["model"],
    }


def _sanitize(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a new settings dict containing only known sections/fields,
    with defaults filling anything missing."""
    settings = default_settings()
    if not isinstance(raw, dict):
        return settings
    for section, fields in settings.items():
        incoming = raw.get(section)
        if not isinstance(incoming, dict):
            continue
        if section == "llm":
            settings["llm"] = _sanitize_llm(incoming)
            continue
        for field in fields:
            if field not in incoming:
                continue
            if section == "agents":
                port = incoming[field].get("port") if isinstance(incoming[field], dict) else None
                if port is not None:
                    settings[section][field] = {"port": _validate_port(port)}
            else:
                if section == "platform" and field == "avatar_profiles":
                    settings[section][field] = _sanitize_avatar_platform_profiles(
                        incoming[field]
                    )
                elif section == "interview" and field == "report_prompt_modules":
                    settings[section][field] = _sanitize_report_prompt_modules(
                        incoming[field]
                    )
                elif section == "interview" and field == "report_llm_provider":
                    settings[section][field] = _normalize_llm_provider(
                        incoming[field]
                    )
                else:
                    settings[section][field] = str(incoming[field])
    return settings


def _sanitize_avatar_platform_profiles(value: Any) -> dict[str, dict[str, str]]:
    profiles: dict[str, dict[str, str]] = {}
    if not isinstance(value, dict):
        return profiles
    for raw_slug, raw_profile in value.items():
        slug = str(raw_slug or "").strip()
        if not slug or not isinstance(raw_profile, dict):
            continue
        profiles[slug] = {
            field: str(raw_profile.get(field) or "")
            for field in AVATAR_PLATFORM_FIELDS
        }
    return profiles


def effective_avatar_platform(
    settings: dict[str, Any], avatar_slug: str
) -> dict[str, str | bool]:
    """Resolve one avatar's LiveAvatar access without exposing its secret."""
    clean = _sanitize(settings)
    platform = clean["platform"]
    profile = platform.get("avatar_profiles", {}).get(str(avatar_slug or "").strip())
    use_global = not isinstance(profile, dict)
    source = platform if use_global else profile
    return {
        "use_global": use_global,
        "api_key": str(source.get("api_key") or ""),
        "base_url": str(source.get("base_url") or ""),
        "sandbox": str(source.get("sandbox") or ""),
    }


def apply_avatar_platform_update(
    current: dict[str, Any], avatar_slug: str, incoming: dict[str, Any]
) -> dict[str, Any]:
    """Apply one avatar's platform choice while preserving a masked API key."""
    slug = str(avatar_slug or "").strip()
    if not slug:
        raise ValueError("缺少面试官标识")
    merged = copy.deepcopy(_sanitize(current))
    profiles = merged["platform"].setdefault("avatar_profiles", {})
    use_global_raw = incoming.get("use_global", True)
    use_global = (
        use_global_raw
        if isinstance(use_global_raw, bool)
        else str(use_global_raw).strip().lower() in {"1", "true", "yes", "on"}
    )
    if use_global:
        profiles.pop(slug, None)
        return _sanitize(merged)

    existing = profiles.get(slug, {})
    api_key = str(incoming.get("api_key") or "")
    if api_key.startswith(MASK_PREFIX):
        api_key = str(existing.get("api_key") or "")
    base_url = str(incoming.get("base_url") or "").strip().rstrip("/")
    sandbox = str(incoming.get("sandbox") or "")
    if not api_key:
        raise ValueError("使用独立平台配置时必须填写 API Key")
    if not base_url:
        raise ValueError("使用独立平台配置时必须填写平台地址")
    profiles[slug] = {
        "api_key": api_key,
        "base_url": base_url,
        "sandbox": "true" if sandbox.strip().lower() in {"1", "true", "yes", "on"} else "",
    }
    return _sanitize(merged)


def _validate_port(value: Any) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"端口必须是数字: {value!r}")
    if not MIN_PORT <= port <= MAX_PORT:
        raise ValueError(f"端口必须在 {MIN_PORT}-{MAX_PORT} 之间: {port}")
    return port


def _seed_report_prompt_modules(settings: dict[str, Any]) -> bool:
    interview = settings.get("interview")
    if not isinstance(interview, dict):
        settings["interview"] = {"report_prompt": "", "report_prompt_modules": _default_report_prompt_modules()}
        return True
    modules = interview.get("report_prompt_modules")
    modules = _sanitize_report_prompt_modules(modules)
    defaults = _default_report_prompt_modules()
    changed = False
    for key in REPORT_PROMPT_MODULE_KEYS:
        if not str(modules.get(key) or "").strip():
            modules[key] = defaults.get(key, "")
            changed = True
    if changed:
        interview["report_prompt_modules"] = modules
    return changed


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        settings = default_settings()
        save_settings(settings, path)
        return settings
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        settings = default_settings()
        save_settings(settings, path)
        return settings
    try:
        settings = _sanitize(raw)
    except ValueError:
        settings = default_settings()
    changed = _seed_report_prompt_modules(settings)
    if raw != settings:
        changed = True
    if changed:
        save_settings(settings, path)
    return settings


def save_settings(settings: dict[str, Any], path: Path = DEFAULT_SETTINGS_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = _sanitize(settings)
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)


def mask_settings(settings: dict[str, Any]) -> dict[str, Any]:
    masked = copy.deepcopy(settings)
    for section, field in SECRET_FIELDS:
        value = masked.get(section, {}).get(field, "")
        if value:
            masked[section][field] = MASK_PREFIX + value[-4:]
    for provider in LLM_PROVIDERS:
        profile = masked.get("llm", {}).get("profiles", {}).get(provider, {})
        value = profile.get("api_key", "")
        if value:
            profile["api_key"] = MASK_PREFIX + value[-4:]
    for profile in masked.get("platform", {}).get("avatar_profiles", {}).values():
        value = profile.get("api_key", "")
        if value:
            profile["api_key"] = MASK_PREFIX + value[-4:]
    return masked


def apply_update(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge a (possibly partial, possibly masked) update into current settings.

    Secret fields whose value still carries the mask sentinel are kept as-is,
    so the UI can round-trip masked values safely. Returns a new dict.
    """
    merged = copy.deepcopy(current)
    if not isinstance(incoming, dict):
        return _sanitize(merged)
    for section, fields in incoming.items():
        if section not in merged or not isinstance(fields, dict):
            continue
        if section == "llm":
            current_llm = _sanitize_llm(merged.get("llm"))
            provider = _normalize_llm_provider(fields.get("provider") or current_llm["provider"])
            current_llm["provider"] = provider
            incoming_profiles = fields.get("profiles")
            if isinstance(incoming_profiles, dict):
                for profile_name in LLM_PROVIDERS:
                    profile_update = incoming_profiles.get(profile_name)
                    if not isinstance(profile_update, dict):
                        continue
                    for field in ("api_key", "base_url", "model"):
                        if field not in profile_update:
                            continue
                        value = profile_update[field]
                        if field == "api_key" and isinstance(value, str) and value.startswith(MASK_PREFIX):
                            continue
                        current_llm["profiles"][profile_name][field] = str(value or "")
            # Accept requests from the previous UI/API shape during migration.
            if any(field in fields for field in ("api_key", "base_url", "model")):
                legacy_provider = _normalize_llm_provider(
                    fields.get("provider")
                    or _infer_llm_provider(fields.get("base_url"))
                    or provider
                )
                current_llm["provider"] = legacy_provider
                for field in ("api_key", "base_url", "model"):
                    if field not in fields:
                        continue
                    value = fields[field]
                    if field == "api_key" and isinstance(value, str) and value.startswith(MASK_PREFIX):
                        continue
                    current_llm["profiles"][legacy_provider][field] = str(value or "")
            merged["llm"] = current_llm
            continue
        for field, value in fields.items():
            if field not in merged[section]:
                continue
            if (section, field) in SECRET_FIELDS and isinstance(value, str) and value.startswith(MASK_PREFIX):
                continue
            if section == "agents":
                if isinstance(value, dict) and "port" in value:
                    merged[section][field] = {"port": _validate_port(value["port"])}
            else:
                if section == "platform" and field == "avatar_profiles":
                    # Managed through the per-avatar edit API, never by the
                    # global form's bulk payload.
                    continue
                if section == "interview" and field == "report_prompt_modules":
                    merged[section][field] = _sanitize_report_prompt_modules(value)
                elif section == "interview" and field == "report_llm_provider":
                    merged[section][field] = _normalize_llm_provider(value)
                else:
                    merged[section][field] = str(value)
    return _sanitize(merged)


def build_agent_env(settings: dict[str, Any], agent_name: str) -> dict[str, str]:
    """Map settings onto the env vars the given agent reads. Empty values are
    omitted so the agent's own defaults still apply."""
    spec = AGENTS[agent_name]
    platform, llm, asr = settings["platform"], active_llm(settings), settings["asr"]
    report_model = report_llm(settings)
    port = settings["agents"].get(agent_name, {}).get("port", spec["default_port"])
    interview = settings.get("interview", {})
    prompt_modules = _sanitize_report_prompt_modules(interview.get("report_prompt_modules"))
    compiled_report_prompt = build_report_prompt_from_modules(prompt_modules).strip()
    compiled_report_overview_prompt = build_report_overview_prompt(prompt_modules).strip()
    compiled_report_qa_prompt = build_report_qa_prompt(prompt_modules).strip()
    legacy_report_prompt = str(interview.get("report_prompt") or "").strip()
    candidates = {
        "LIVEAVATAR_API_KEY": platform["api_key"],
        "LIVEAVATAR_AVATAR_ID": platform["avatar_id"],
        "LIVEAVATAR_VOICE_ID": platform["voice_id"],
        "LIVEAVATAR_BASE_URL": platform["base_url"],
        "LIVEAVATAR_SANDBOX": platform.get("sandbox", ""),
        "LIVEAVATAR_VOICE_SPEED": platform.get("voice_speed", ""),
        "DEEPSEEK_API_KEY": llm["api_key"],
        "DEEPSEEK_BASE_URL": llm["base_url"],
        "DEEPSEEK_MODEL": llm["model"],
        "REPORT_LLM_API_KEY": report_model["api_key"],
        "REPORT_LLM_BASE_URL": report_model["base_url"],
        "REPORT_LLM_MODEL": report_model["model"],
        "REPORT_LLM_PROVIDER": report_model["provider"],
        "REPORT_LLM_REQUESTED_PROVIDER": report_model["requested_provider"],
        "REPORT_LLM_FALLBACK": "1" if report_model["fallback"] else "0",
        "INTERVIEW_GLOBAL_REPORT_PROMPT": compiled_report_prompt or legacy_report_prompt,
        "INTERVIEW_GLOBAL_REPORT_OVERVIEW_PROMPT": compiled_report_overview_prompt,
        "INTERVIEW_GLOBAL_REPORT_QA_PROMPT": compiled_report_qa_prompt,
        "ASR_PROVIDER": asr.get("provider", "dashscope"),
        "DASHSCOPE_API_KEY": asr["dashscope_api_key"],
        "DASHSCOPE_ASR_MODEL": asr["model"],
        "VOLC_ASR_APP_ID": asr.get("volc_app_id", ""),
        "VOLC_ASR_ACCESS_TOKEN": asr.get("volc_access_token", ""),
        "VOLC_ASR_SECRET_KEY": asr.get("volc_secret_key", ""),
        "VOLC_ASR_CLUSTER": asr.get("volc_cluster", ""),
        spec["port_env"]: str(port),
    }
    return {k: v for k, v in candidates.items() if v}
