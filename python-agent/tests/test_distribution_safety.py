from pathlib import Path
import re


ROOT = Path(__file__).parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_no_liveavatar_secret_defaults_in_source():
    live_key_pattern = re.compile(r"lk_live_[A-Za-z0-9]{20,}")
    for path in [
        "interview/agent.py",
        "hub/hub.py",
        "hub/config_store.py",
        "llm_client.py",
    ]:
        text = _read(path)
        assert not live_key_pattern.search(text), path


def test_env_template_exists_with_empty_secrets():
    assert (ROOT / ".env.example").exists()
    text = _read(".env.example")
    for var in ("LIVEAVATAR_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY"):
        assert f"{var}=" in text, var
    for line in text.splitlines():
        if "_KEY=" in line and not line.lstrip().startswith("#"):
            assert line.strip().endswith("="), f"secret value committed: {line}"
