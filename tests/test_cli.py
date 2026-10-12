import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

import pytest

from xvendor.jobs import JOB_ID_RE

needs_bwrap = pytest.mark.skipif(sys.platform != "linux" or shutil.which("bwrap") is None, reason="needs bwrap")


@pytest.fixture
def data(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def xv(data, fakes_dir, fixture_path, tmp_path):
    base = {k: v for k, v in os.environ.items() if not k.startswith(("XV_", "TMUX"))}
    base.update(XV_DATA=str(data), PATH=f"{fakes_dir}{os.pathsep}{base.get('PATH', '')}",
                XV_FAKE_FIXTURE=str(fixture_path("codex-done")))

    def run(*args, **env):
        e = {**base, **{k: str(v) for k, v in env.items()}}
        return subprocess.run(["xv", *args], capture_output=True, text=True, env=e, cwd=tmp_path, timeout=60)
    return run


def start(xv, vendor="codex", **env):
    proc = xv("start", vendor, "-m", "question", "--no-pane", **env)
    assert proc.returncode == 0, proc.stderr
    job = proc.stdout.strip()
    assert JOB_ID_RE.fullmatch(job)
    return job


def test_start_wait_codex(xv):
    job = start(xv)
    proc = xv("wait", job)
    assert proc.returncode == 0, proc.stderr
    assert re.fullmatch(r"status=done round=1 secs=\d+ response=\S+/round-1/response\.md\n", proc.stdout)
    assert xv("show", job).stdout.startswith("### 資料目錄路徑管理")


@needs_bwrap
def test_start_wait_agy(xv, fixture_path):
    job = start(xv, "agy", XV_FAKE_FIXTURE=fixture_path("agy-done"))
    proc = xv("wait", job)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("status=done round=1 ")
    assert xv("show", job).stdout == "layout test 2\n\n"


def test_ask_resumes_session(xv, tmp_path):
    job = start(xv)
    assert xv("wait", job).returncode == 0
    argv_out = tmp_path / "argv.json"
    proc = xv("ask", job, "-m", "again", XV_FAKE_ARGV_OUT=argv_out)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == f"{job} round-2\n"
    assert xv("wait", job).stdout.startswith("status=done round=2 ")
    argv = json.loads(argv_out.read_text())
    assert "resume" in argv
    assert argv[-2:] == ["01a126af-a00e-7501-bc5e-5b7215dc60ac", "again"]


def test_stop_then_ask(xv, data):
    job = start(xv, XV_FAKE_DELAY=1)
    events = data / "jobs" / job / "round-1" / "events.jsonl"
    for _ in range(100):  # wait for thread.started so the round has a session to keep
        if events.exists() and events.read_text():
            break
        time.sleep(0.1)
    proc = xv("stop", job[-4:])
    assert proc.returncode == 0, proc.stderr
    proc = xv("wait", job)
    assert proc.returncode == 1
    assert proc.stdout.startswith("status=stopped round=1 ")
    assert xv("ask", job, "-m", "go on").returncode == 0
    assert xv("wait", job).stdout.startswith("status=done round=2 ")


def test_run_killed_by_sighup_cleans_up(xv, data, fakes_dir, fixture_path, tmp_path):
    # what closing a pane mid-round does to `xv _run`
    job = start(xv, XV_FAKE_DELAY=30)
    meta_path = data / "jobs" / job / "meta.json"
    for _ in range(100):
        meta = json.loads(meta_path.read_text())
        if meta.get("run_pgid"):
            break
        time.sleep(0.1)
    pgid = meta["run_pgid"]
    os.kill(meta["run_pid"], signal.SIGHUP)
    for _ in range(100):
        meta = json.loads(meta_path.read_text())
        if meta["status"] != "running":
            break
        time.sleep(0.1)
    assert meta["status"] == "failed"
    assert meta["run_pgid"] is None
    with pytest.raises(ProcessLookupError):  # the vendor CLI's group is gone too
        os.killpg(pgid, 0)


def test_last4_resolves(xv):
    job = start(xv)
    assert xv("wait", job[-4:]).returncode == 0
    assert xv("show", job[-4:], "1").stdout.startswith("### 資料目錄路徑管理")


def test_ask_rejected_while_running(xv):
    job = start(xv, XV_FAKE_DELAY=1)
    proc = xv("ask", job, "-m", "x")
    assert proc.returncode == 2
    assert proc.stderr.startswith("xv: ")
    xv("stop", job)


@pytest.mark.parametrize("args, env", [
    (["start", "codex", "-m", "x", "--no-pane", "--timeout", "a[$(id)0]"], {}),
    (["close", "../../x"], {}),
    (["start", "codex", "-m", "x", "--no-pane", "--profile", "../x"], {}),
    (["list"], {"XV_DATA": "/tmp/a'b\"c"}),
    (["show", "JOB", "x"], {}),
    (["wait", "ffff"], {}),
    (["ask", "JOB", "-m", "x", "--cwd", "/tmp"], {}),
])
def test_bad_input_rc2(xv, data, args, env):
    job = start(xv)
    xv("wait", job)
    if job.endswith("ffff"):
        pytest.skip("random suffix collision")
    proc = xv(*[job if a == "JOB" else a for a in args], **env)
    assert proc.returncode == 2
    assert proc.stderr.startswith("xv: ")
    assert sorted(p.name for p in (data / "jobs").iterdir()) == [job]  # nothing new created


def test_list_format(xv):
    a = start(xv)
    xv("wait", a)
    proc = xv("list")
    assert proc.returncode == 0
    assert re.fullmatch(rf"{a}  codex  r1  done  \d+\n", proc.stdout)


def test_list_empty(xv):
    proc = xv("list")
    assert (proc.returncode, proc.stdout) == (0, "")


def test_watch_finished_round(xv):
    job = start(xv)
    xv("wait", job)
    proc = xv("watch", job)
    assert proc.returncode == 0
    assert "執行  sed -n '1,240p' crates/ab-core/src/paths.rs" in proc.stdout
    assert f"done | " in proc.stdout and f"xv show {job[-4:]}" in proc.stdout


def test_run_prints_carry_first(xv, data):
    job = start(xv)
    xv("wait", job)
    carry = data / "jobs" / job / "round-1" / "pane-carry.txt"
    carry.write_text("PREVIOUS ROUND TAIL\n")
    proc = xv("_run", job, "1")
    assert proc.stdout.startswith("PREVIOUS ROUND TAIL\n")
    assert "codex | " in proc.stdout
    assert not carry.exists()


def test_readonly_data_dir_message(xv, data):
    data.mkdir()
    data.chmod(0o500)
    try:
        proc = xv("start", "codex", "-m", "x", "--no-pane")
    finally:
        data.chmod(0o700)
    assert proc.returncode == 1
    assert proc.stderr.startswith("xv: ")
    assert "Traceback" not in proc.stderr


def test_erofs_hint(monkeypatch, capsys, tmp_path):
    import errno
    from xvendor import cli

    def boom(store, args):
        raise OSError(errno.EROFS, "Read-only file system", str(tmp_path))
    monkeypatch.setitem(cli.COMMANDS, "list", boom)
    monkeypatch.setenv("XV_DATA", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["xv", "list"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("xv: ") and "單獨一條" in err
