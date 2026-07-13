from pathlib import Path
import re


ROOT = Path(__file__).parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_no_liveavatar_secret_defaults_in_source():
    live_key_pattern = re.compile(r"lk_live_[A-Za-z0-9]{20,}")
    for path in [
        "broadcast/agent.py",
        "chat/agent.py",
        "interview/agent.py",
        "talkshow/agent.py",
        "teaching/config.py",
    ]:
        text = _read(path)
        assert not live_key_pattern.search(text), path


def test_distribution_templates_and_package_script_exist():
    assert (ROOT / ".env.example").exists()
    assert (ROOT / "config/crypto_market.example.yaml").exists()
    assert (ROOT / "config/products.example.yaml").exists()
    assert (ROOT / "scripts/package_python_agent.sh").exists()

    package_script = _read("scripts/package_python_agent.sh")
    assert 'rm -f "$DIST_DIR/$PACKAGE_NAME.zip"' in package_script
    for excluded in [
        ".venv",
        "node_modules",
        ".omc",
        ".claude",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "test-report.json",
        "course-test-report.json",
    ]:
        assert excluded in package_script

    assert "frontend" in package_script
    frontend_section = package_script.split('"$PROJECT_ROOT/frontend/"', 1)[1]
    assert "--exclude \".omc\"" in frontend_section
    assert "--exclude \".ruff_cache\"" in frontend_section
