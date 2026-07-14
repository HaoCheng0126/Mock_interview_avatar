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
    ("llm", "api_key"),
    ("asr", "dashscope_api_key"),
    ("asr", "volc_access_token"),
    ("asr", "volc_secret_key"),
}

# ASR providers selectable in the console.
ASR_PROVIDERS = ("dashscope", "volcengine")


def default_settings() -> dict[str, Any]:
    return {
        "platform": {
            "api_key": "",
            "avatar_id": "",
            "voice_id": "",
            "base_url": "https://facemarket.ai/vih/dispatcher",
            "sandbox": "",
            "voice_speed": "",
        },
        "llm": {
            "api_key": "",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
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
        "agents": {
            name: {"port": spec["default_port"]} for name, spec in AGENTS.items()
        },
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
        for field in fields:
            if field not in incoming:
                continue
            if section == "agents":
                port = incoming[field].get("port") if isinstance(incoming[field], dict) else None
                if port is not None:
                    settings[section][field] = {"port": _validate_port(port)}
            else:
                settings[section][field] = str(incoming[field])
    return settings


def _validate_port(value: Any) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"端口必须是数字: {value!r}")
    if not MIN_PORT <= port <= MAX_PORT:
        raise ValueError(f"端口必须在 {MIN_PORT}-{MAX_PORT} 之间: {port}")
    return port


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_settings()
    try:
        return _sanitize(raw)
    except ValueError:
        return default_settings()


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
        for field, value in fields.items():
            if field not in merged[section]:
                continue
            if (section, field) in SECRET_FIELDS and isinstance(value, str) and value.startswith(MASK_PREFIX):
                continue
            if section == "agents":
                if isinstance(value, dict) and "port" in value:
                    merged[section][field] = {"port": _validate_port(value["port"])}
            else:
                merged[section][field] = str(value)
    return _sanitize(merged)


def build_agent_env(settings: dict[str, Any], agent_name: str) -> dict[str, str]:
    """Map settings onto the env vars the given agent reads. Empty values are
    omitted so the agent's own defaults still apply."""
    spec = AGENTS[agent_name]
    platform, llm, asr = settings["platform"], settings["llm"], settings["asr"]
    port = settings["agents"].get(agent_name, {}).get("port", spec["default_port"])
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
