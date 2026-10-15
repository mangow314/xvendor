import json

import pytest

from xvendor.events import Event
from xvendor.vendors.agy import AgyVendor


@pytest.fixture
def v():
    return AgyVendor()


def test_build_round1(v):
    assert v.build(1, None, "hi", None) == [
        "agy",
        "-p",
        "hi",
        "--output-format",
        "stream-json",
        "--dangerously-skip-permissions",
    ]


def test_build_resume(v):
    assert v.build(2, "conv-1", "again", None) == [
        "agy",
        "-p",
        "again",
        "--output-format",
        "stream-json",
        "--dangerously-skip-permissions",
        "--conversation",
        "conv-1",
    ]


def test_build_rejects_profile(v):
    with pytest.raises(ValueError):
        v.build(1, None, "hi", "local")


def step(**fields):
    return json.dumps({"event": "step_update", "step_update": fields}, ensure_ascii=False)


def test_parse_active_tool(v):
    line = step(
        step_type="tool", state="ACTIVE", tool_name="view_file", tool_info={"parameters": {"path": "/a/檔.py", "n": 1}}
    )
    assert v.parse(line) == Event("action", 'view_file  {"path":"/a/檔.py","n":1}')


def test_parse_tool_without_params(v):
    assert v.parse(step(step_type="tool", state="ACTIVE", tool_name="ls")) == Event("action", "ls  {}")


@pytest.mark.parametrize(
    "line",
    [
        step(step_type="tool", state="DONE", tool_name="ls"),
        step(step_type="agent_response", state="ACTIVE"),
        '{"event":"init","conversation_id":"c"}',
        "not json",
    ],
)
def test_parse_ignored(v, line):
    assert v.parse(line) is None


def test_parse_fixture(v, fixture_lines):
    events = [e for e in map(v.parse, fixture_lines("agy-done")) if e is not None]
    assert events == [Event("done", "")]


def test_result_fixture(v, fixture_lines):
    assert v.result(fixture_lines("agy-done")) == ("0a85aca6-fc8a-46bd-9c66-81ccf6534908", "layout test 2\n", True)


def test_result_failed_status(v):
    lines = [
        '{"event":"init","conversation_id":"c"}',
        "garbage",
        '{"event":"result","result":{"status":"ERROR","response":""}}',
    ]
    assert v.result(lines) == ("c", "", False)


def test_result_codex_fixture_is_empty(v, fixture_lines):
    assert v.result(fixture_lines("codex-done")) == (None, "", False)
