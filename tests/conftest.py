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
