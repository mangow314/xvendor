import json

import pytest

from xvendor.events import Event
from xvendor.vendors.codex import CodexVendor

FLAGGED = (
    "This content was flagged for possible cybersecurity risk. If this seems wrong, try rephrasing "
    "your request. If you’re doing authorized security work that requires more cyber permissive "
    "safeguards, apply for Daybreak access via "
    "https://platform.openai.com/settings/organization/status-and-access before retrying."
)


ROOTS = [
    "-c",
    "sandbox_workspace_write.exclude_slash_tmp=true",
    "-c",
    "sandbox_workspace_write.exclude_tmpdir_env_var=true",
    "-c",
    "sandbox_workspace_write.writable_roots=[]",
]


@pytest.fixture
def v():
    return CodexVendor()


def test_name(v):
    assert v.name == "codex"


# build


def test_build_round1(v):
    assert v.build(1, None, "hi", None) == ["codex", "exec", "-s", "read-only", "--skip-git-repo-check", "--json", "hi"]


def test_build_round1_profile(v):
    assert v.build(1, None, "hi", "local") == [
        "codex",
        "-p",
        "local",
        "exec",
        "-s",
        "read-only",
        "--skip-git-repo-check",
        "--json",
        "hi",
    ]


def test_build_resume(v):
    assert v.build(2, "sid-1", "again", None) == [
        "codex",
        "exec",
        "resume",
        "-c",
        'sandbox_mode="read-only"',
        "--skip-git-repo-check",
        "--json",
        "sid-1",
        "again",
    ]


def test_build_resume_profile(v):
    assert v.build(3, "sid-1", "again", "local") == [
        "codex",
        "-p",
        "local",
        "exec",
        "resume",
        "-c",
        'sandbox_mode="read-only"',
        "--skip-git-repo-check",
        "--json",
        "sid-1",
        "again",
    ]


def test_build_write_mode(v):
    assert v.build(1, None, "hi", None, mode="write") == [
        "codex",
        "exec",
        "-s",
        "workspace-write",
        *ROOTS,
        "--skip-git-repo-check",
        "--json",
        "hi",
    ]
    assert v.build(2, "sid-1", "again", None, mode="write") == [
        "codex",
        "exec",
        "resume",
        "-c",
        'sandbox_mode="workspace-write"',
        *ROOTS,
        "--skip-git-repo-check",
        "--json",
        "sid-1",
        "again",
    ]
    assert v.build(1, None, "hi", None, mode="read") == v.build(1, None, "hi", None)


def test_write_mode_codex_sandbox_roots(v):
    for argv in (v.build(1, None, "hi", None, mode="write"), v.build(2, "sid-1", "again", None, mode="write")):
        for value in ROOTS[1::2]:
            assert argv[argv.index(value) - 1] == "-c"
    for argv in (v.build(1, None, "hi", None), v.build(2, "sid-1", "again", None)):
        assert not any("sandbox_workspace_write" in a for a in argv)


# parse


def line(obj):
    return json.dumps(obj, ensure_ascii=False)


def started(command):
    return line({"type": "item.started", "item": {"type": "command_execution", "command": command}})


@pytest.mark.parametrize("raw", ["", "not json", "{broken"])
def test_parse_non_json(v, raw):
    assert v.parse(raw) is None


@pytest.mark.parametrize(
    "obj",
    [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "ls"}},
        {"type": "item.started", "item": {"type": "web_search"}},
    ],
)
def test_parse_unknown_events(v, obj):
    assert v.parse(line(obj)) is None


@pytest.mark.parametrize(
    "command, expected",
    [
        ("/bin/bash -lc \"sed -n '1,240p' a.rs\"", "sed -n '1,240p' a.rs"),
        ("/bin/sh -lc 'ls -la'", "ls -la"),
        ("rg foo", "rg foo"),
    ],
)
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
        Event("action", "sed -n '1,240p' src/app/paths.py"),
        Event("note", json.loads(lines[5])["item"]["text"]),
        Event("done", ""),
    ]


def test_parse_fixture_codex_failed(v, fixture_lines):
    events = parsed(v, fixture_lines("codex-failed"))
    assert [e.kind for e in events] == ["note", "action", "action", "note", "action", "action", "error", "error"]
    assert events[1] == Event("action", "wc -l -c /home/user/project/xv.sh; nl -ba /home/user/project/xv.sh")
    assert events[6] == events[7] == Event("error", FLAGGED)


def test_parse_fixture_agy_done_is_all_none(v, fixture_lines):
    assert parsed(v, fixture_lines("agy-done")) == []


# result


def test_result_codex_done(v, fixture_lines):
    lines = fixture_lines("codex-done")
    session, response, ok = v.result(lines)
    assert session == "01a126af-a00e-7501-bc5e-5b7215dc60ac"
    assert response == json.loads(lines[5])["item"]["text"]
    assert response.startswith("### Data directory paths")
    assert ok is True


def test_result_codex_failed(v, fixture_lines):
    lines = fixture_lines("codex-failed")
    session, response, ok = v.result(lines)
    assert session == "01a1263b-7821-7c52-b722-51a025c3338b"
    assert response == json.loads(lines[7])["item"]["text"]
    assert response.startswith("Found a possible way around the restriction")
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


# model


def _rollout(home, session, records):
    d = home / "sessions" / "2026" / "10" / "18"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"rollout-2026-10-18T00-00-00-{session}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))


def _ctx(ts, **payload):
    return {"timestamp": ts, "type": "turn_context", "payload": payload}


T0 = 1792281600  # 2026-10-18T00:00:00Z


def test_model_from_rollout(v, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _rollout(tmp_path, "s1", [_ctx("2026-10-18T00:00:01.000Z", model="gpt-x", effort="high")])
    _rollout(tmp_path, "s2", [_ctx("2026-10-18T00:00:01.000Z", model="gpt-y")])
    assert v.model("s1", T0) == "gpt-x | effort high"
    assert v.model("s2", T0) == "gpt-y | effort default"
    assert v.model("missing", T0) is None


def test_model_skips_earlier_rounds(v, tmp_path, monkeypatch):
    # round 2 started at T0: round 1's turn_context must not be reported, before or after round 2's is written
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    old = [_ctx("2026-10-17T23:59:00.000Z", model="old"), {"type": "event_msg", "payload": {"type": "task_complete"}}]
    _rollout(tmp_path, "s", old)
    assert v.model("s", T0) is None
    _rollout(tmp_path, "s", [*old, _ctx("2026-10-18T00:00:02.500Z", model="new"), {"type": "event_msg"}])
    assert v.model("s", T0) == "new | effort default"
