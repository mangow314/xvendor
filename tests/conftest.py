from pathlib import Path

import pytest

TESTS = Path(__file__).parent
FIXTURES = TESTS / "fixtures"
FAKES = TESTS / "fakes"


@pytest.fixture
def fixture_path():
    """fixture_path("codex-done") -> tests/fixtures/codex-done.jsonl"""
    return lambda name: FIXTURES / f"{name}.jsonl"


@pytest.fixture
def fixture_lines(fixture_path):
    """fixture_lines("codex-done") -> list of lines without trailing newlines"""
    return lambda name: fixture_path(name).read_text(encoding="utf-8").splitlines()


@pytest.fixture
def fakes_dir():
    return FAKES


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """HOME with the minimal codex and agy state isolation.check requires, so tests do not read the host's."""
    home = tmp_path / "home"
    (home / ".codex" / "hooks").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text("")
    agy = home / ".gemini" / "antigravity-cli"
    (agy / "bin").mkdir(parents=True)
    (agy / "builtin").mkdir()
    (agy / "settings.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return home
