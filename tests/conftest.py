import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
FIXTURES = TESTS / "fixtures"
FAKES = TESTS / "fakes"
# the same probe `xv start` runs (isolation.pidns_error): where it fails, xv refuses to start a job
BWRAP_PROBE = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-pid", "true"]


@pytest.fixture(scope="session")
def bwrap_unusable() -> str | None:
    """Why bwrap cannot start a sandbox here, or None. Being on PATH is not enough: nested inside another
    sandbox, or on a kernel that restricts unprivileged user namespaces, it cannot create a namespace."""
    if shutil.which("bwrap") is None:
        return "bwrap not installed"
    proc = subprocess.run(BWRAP_PROBE, capture_output=True, text=True)
    if proc.returncode == 0:
        return None
    return f"bwrap probe `{' '.join(BWRAP_PROBE)}` failed: {proc.stderr.strip() or f'rc={proc.returncode}'}"


def _need_bwrap(unusable: str | None) -> None:
    """Skip when bwrap is unusable. With CI set the run is meant to have one, so fail instead: a runner image
    that restricts user namespaces would otherwise skip every isolation test and stay green."""
    if unusable is None:
        return
    if os.environ.get("CI"):
        pytest.fail(f"CI is set, so bwrap has to work here: {unusable}", pytrace=False)
    pytest.skip(unusable)


@pytest.fixture
def real_bwrap(bwrap_unusable: str | None) -> None:
    """For tests that run a real bwrap, which only exists on Linux."""
    if sys.platform != "linux":
        pytest.skip("bwrap is Linux-only")
    _need_bwrap(bwrap_unusable)


@pytest.fixture
def codex_isolation(bwrap_unusable: str | None) -> None:
    """For tests whose codex round goes through the real isolation.wrap: bwrap on Linux, no wrapper elsewhere."""
    if sys.platform == "linux":
        _need_bwrap(bwrap_unusable)


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
