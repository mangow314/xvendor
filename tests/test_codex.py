import json

import pytest

from xvendor.events import Event
from xvendor.vendors.codex import CodexVendor

FLAGGED = ("This content was flagged for possible cybersecurity risk. If this seems wrong, try rephrasing "
           "your request. If you’re doing authorized security work that requires more cyber permissive "
           "safeguards, apply for Daybreak access via "
           "https://platform.openai.com/settings/organization/status-and-access before retrying.")


@pytest.fixture
def v():
    return CodexVendor()


def test_name(v):
    assert v.name == "codex"


# build

def test_build_round1(v):
    assert v.build(1, None, "hi", None) == [
        "codex", "exec", "-s", "read-only", "--skip-git-repo-check", "--json", "hi"]


def test_build_round1_profile(v):
    assert v.build(1, None, "hi", "local") == [
        "codex", "-p", "local", "exec", "-s", "read-only", "--skip-git-repo-check", "--json", "hi"]


def test_build_resume(v):
    assert v.build(2, "sid-1", "again", None) == [
        "codex", "exec", "resume", "-c", 'sandbox_mode="read-only"', "--skip-git-repo-check", "--json",
        "sid-1", "again"]


def test_build_resume_profile(v):
    assert v.build(3, "sid-1", "again", "local") == [
        "codex", "-p", "local", "exec", "resume", "-c", 'sandbox_mode="read-only"', "--skip-git-repo-check",
        "--json", "sid-1", "again"]


# parse

def line(obj):
    return json.dumps(obj, ensure_ascii=False)


def started(command):
    return line({"type": "item.started", "item": {"type": "command_execution", "command": command}})


@pytest.mark.parametrize("raw", ["", "not json", "{broken"])
def test_parse_non_json(v, raw):
    assert v.parse(raw) is None


@pytest.mark.parametrize("obj", [
    {"type": "thread.started", "thread_id": "t"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"type": "command_execution", "command": "ls"}},
    {"type": "item.started", "item": {"type": "web_search"}},
])
def test_parse_unknown_events(v, obj):
    assert v.parse(line(obj)) is None


@pytest.mark.parametrize("command, expected", [
    ('/bin/bash -lc "sed -n \'1,240p\' a.rs"', "sed -n '1,240p' a.rs"),
    ("/bin/sh -lc 'ls -la'", "ls -la"),
    ("rg foo", "rg foo"),
])
def test_parse_command(v, command, expected):
    assert v.parse(started(command)) == Event("action", expected)


def test_parse_agent_message(v):
    obj = {"type": "item.completed", "item": {"type": "agent_message", "text": "done reading"}}
    assert v.parse(line(obj)) == Event("note", "done reading")


def test_parse_error_message(v):
    assert v.parse(line({"type": "error", "message": "boom"})) == Event("error", "boom")


def test_parse_turn_failed_error_message(v):
    obj = {"type": "turn.failed", "error": {"message": "nested"}}
    assert v.parse(line(obj)) == Event("error", "nested")


def test_parse_error_without_message_uses_raw_line(v):
    raw = line({"type": "error", "code": 7})
    assert v.parse(raw) == Event("error", raw)


def test_parse_turn_completed(v):
    assert v.parse(line({"type": "turn.completed", "usage": {}})) == Event("done", "")


# fixtures

def parsed(v, lines):
    return [e for e in map(v.parse, lines) if e is not None]


def test_parse_fixture_codex_done(v, fixture_lines):
    lines = fixture_lines("codex-done")
    assert parsed(v, lines) == [
        Event("note", json.loads(lines[2])["item"]["text"]),
        Event("action", "sed -n '1,240p' crates/ab-core/src/paths.rs"),
        Event("note", json.loads(lines[5])["item"]["text"]),
        Event("done", ""),
    ]


def test_parse_fixture_codex_failed(v, fixture_lines):
    events = parsed(v, fixture_lines("codex-failed"))
    assert [e.kind for e in events] == ["note", "action", "action", "note", "action", "action", "error", "error"]
    assert events[1] == Event("action", "wc -l -c /home/mango/.claude/skills/xvendor/scripts/xv.sh; "
                                        "sed -n '100,140p' /home/mango/.local/share/chezmoi/.chezmoitemplates/"
                                        "settings.json.tmpl; nl -ba /home/mango/.claude/skills/xvendor/scripts/xv.sh")
    assert events[6] == events[7] == Event("error", FLAGGED)


def test_parse_fixture_agy_done_is_all_none(v, fixture_lines):
    assert parsed(v, fixture_lines("agy-done")) == []


# result

def test_result_codex_done(v, fixture_lines):
    lines = fixture_lines("codex-done")
    session, response, ok = v.result(lines)
    assert session == "01a126af-a00e-7501-bc5e-5b7215dc60ac"
    assert response == json.loads(lines[5])["item"]["text"]
    assert response.startswith("### 資料目錄路徑管理")
    assert ok is True


def test_result_codex_failed(v, fixture_lines):
    lines = fixture_lines("codex-failed")
    session, response, ok = v.result(lines)
    assert session == "01a1263b-7821-7c52-b722-51a025c3338b"
    assert response == json.loads(lines[7])["item"]["text"]
    assert response.startswith("已找到可繞過限制的入口")
    assert ok is False


def test_result_agy_done(v, fixture_lines):
    assert v.result(fixture_lines("agy-done")) == (None, "", False)


def test_result_first_thread_last_message(v):
    lines = [
        line({"type": "thread.started", "thread_id": "first"}),
        line({"type": "thread.started", "thread_id": "second"}),
        line({"type": "item.completed", "item": {"type": "agent_message", "text": "one"}}),
        line({"type": "item.completed", "item": {"type": "agent_message", "text": "two"}}),
    ]
    assert v.result(lines) == ("first", "two", False)


def test_result_skips_non_json(v):
    lines = [
        "garbage",
        line({"type": "thread.started", "thread_id": "s"}),
        "{not json",
        line({"type": "item.completed", "item": {"type": "agent_message", "text": "answer"}}),
        "",
        line({"type": "turn.completed"}),
    ]
    assert v.result(lines) == ("s", "answer", True)


def test_result_empty(v):
    assert v.result([]) == (None, "", False)
