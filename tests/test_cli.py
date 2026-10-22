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
    home = tmp_path / "home"
    (home / ".codex" / "hooks").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text("")
    agy = home / ".gemini" / "antigravity-cli"
    (agy / "bin").mkdir(parents=True)
    (agy / "builtin").mkdir()
    (agy / "settings.json").write_text("{}")
    base.pop("CODEX_HOME", None)
    base.update(
        HOME=str(home),
        XV_DATA=str(data),
        PATH=f"{fakes_dir}{os.pathsep}{base.get('PATH', '')}",
        XV_FAKE_FIXTURE=str(fixture_path("codex-done")),
    )

    def run(*args, **env):
        e = {**base, **{k: str(v) for k, v in env.items()}}
        return subprocess.run(
            [sys.executable, "-m", "xvendor", *args], capture_output=True, text=True, env=e, cwd=tmp_path, timeout=60
        )

    return run


def make_worktree(tmp_path, name="wt"):
    main = tmp_path / f"main-{name}"
    ident = ["-c", "user.name=t", "-c", "user.email=t@x"]
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    (main / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(main), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(main), *ident, "commit", "-q", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", f"../{name}", "-b", f"lane-{name}"], check=True)
    return tmp_path / name


def start(xv, vendor="codex", **env):
    proc = xv("start", vendor, "-m", "question", "--no-pane", **env)
    assert proc.returncode == 0, proc.stderr
    job = proc.stdout.strip()
    assert JOB_ID_RE.fullmatch(job)
    return job


def test_start_without_pane_hints_watch(xv):
    proc = xv("start", "codex", "-m", "q", "--no-pane")
    job = proc.stdout.strip()
    assert JOB_ID_RE.fullmatch(job)
    assert f"xv watch {job}" in proc.stderr


def test_start_wait_codex(xv):
    job = start(xv)
    proc = xv("wait", job)
    assert proc.returncode == 0, proc.stderr
    assert re.fullmatch(r"status=done round=1 secs=\d+ response=\S+/round-1/response\.md\n", proc.stdout)
    assert xv("show", job).stdout.startswith("### Data directory paths")


def test_show_reports_vendor_error(xv, fixture_path):
    job = start(xv, XV_FAKE_FIXTURE=fixture_path("codex-failed"))
    assert xv("wait", job).returncode == 1
    proc = xv("show", job)
    assert proc.stdout.startswith("Found a possible way around the restriction")
    assert "xv: round 1 failed: This content was flagged" in proc.stderr


@needs_bwrap
def test_start_wait_agy(xv, fixture_path):
    job = start(xv, "agy", XV_FAKE_FIXTURE=fixture_path("agy-done"))
    proc = xv("wait", job)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("status=done round=1 ")
    assert xv("show", job).stdout == "layout test 2\n\n"


@pytest.mark.skipif(sys.platform != "linux", reason="bwrap probe is Linux-only")
def test_start_refuses_when_pidns_unavailable(xv, data, tmp_path):
    # stands in for bwrap inside a Docker container that masks parts of /proc
    fake = tmp_path / "dockerbin"
    fake.mkdir()
    (fake / "bwrap").write_text(
        '#!/bin/sh\necho "bwrap: Can\'t mount proc on /newroot/proc: Operation not permitted" >&2\nexit 1\n'
    )
    (fake / "bwrap").chmod(0o755)
    path = f"{fake}{os.pathsep}{os.environ['PATH']}"
    proc = xv("start", "codex", "-m", "q", "--no-pane", PATH=path)
    assert proc.returncode == 2
    for want in ("Can't mount proc", "systempaths=unconfined", "XV_NO_PIDNS=1"):
        assert want in proc.stderr
    assert not (data / "jobs").exists() or not any((data / "jobs").iterdir())


@needs_bwrap
def test_start_without_pidns_runs_read_only(xv, data):
    job = start(xv, XV_NO_PIDNS=1)
    assert xv("wait", job).stdout.startswith("status=done round=1 ")
    assert json.loads((data / "jobs" / job / "meta.json").read_text())["pidns"] is False


def test_start_without_pidns_refuses_write(xv, tmp_path):
    proc = xv("start", "codex", "-m", "q", "--no-pane", "--write", make_worktree(tmp_path), XV_NO_PIDNS=1)
    assert proc.returncode == 2
    assert "--write needs a pid namespace" in proc.stderr


def test_ask_resumes_session(xv, tmp_path):
    job = start(xv)
    assert xv("wait", job).returncode == 0
    argv_out = tmp_path / "home" / ".codex" / "argv.json"  # writable state dir inside bwrap
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
    assert xv("show", job[-4:], "1").stdout.startswith("### Data directory paths")


def test_ask_rejected_while_running(xv):
    job = start(xv, XV_FAKE_DELAY=1)
    proc = xv("ask", job, "-m", "x")
    assert proc.returncode == 2
    assert proc.stderr.startswith("xv: ")
    xv("stop", job)


@pytest.mark.parametrize(
    "args, env",
    [
        (["start", "codex", "-m", "x", "--no-pane", "--timeout", "a[$(id)0]"], {}),
        (["close", "../../x"], {}),
        (["start", "codex", "-m", "x", "--no-pane", "--profile", "../x"], {}),
        (["list"], {"XV_DATA": "/tmp/a'b\"c"}),
        (["show", "JOB", "x"], {}),
        (["wait", "ffff"], {}),
        (["ask", "JOB", "-m", "x", "--cwd", "/tmp"], {}),
    ],
)
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
    assert re.fullmatch(rf"{a}  codex  r1  done  \d+  -\n", proc.stdout)


def test_list_hides_old_finished_jobs(xv, data):
    old, new = start(xv), start(xv)
    xv("wait", old)
    xv("wait", new)
    week_ago = time.time() - 7 * 86400
    os.utime(data / "jobs" / old / "meta.json", (week_ago, week_ago))
    listed = xv("list").stdout
    assert new in listed and old not in listed
    assert old in xv("list", "--all").stdout


def test_start_write_sets_meta(xv, data, tmp_path):
    work = make_worktree(tmp_path)
    proc = xv("start", "codex", "-m", "q", "--no-pane", "--write", work)
    assert proc.returncode == 0, proc.stderr
    meta = json.loads((data / "jobs" / proc.stdout.strip() / "meta.json").read_text())
    assert (meta["mode"], meta["writable"], meta["cwd"]) == ("write", str(work.resolve()), str(work.resolve()))
    xv("wait", proc.stdout.strip())
    assert f"write:{work.resolve()}" in xv("list").stdout


def test_run_reports_nested_git(xv, data, tmp_path):
    work = make_worktree(tmp_path)
    (work / "sub" / ".git").mkdir(parents=True)
    job = xv("start", "codex", "-m", "q", "--no-pane", "--write", work).stdout.strip()
    xv("wait", job)
    meta = json.loads((data / "jobs" / job / "meta.json").read_text())  # same read as the one wait saw: no polling
    assert meta["nested_git"] == [str(work.resolve() / "sub" / ".git")]
    assert "nested git" in xv("show", job).stderr


def test_wait_reports_nested_git(xv, data, tmp_path):
    work = make_worktree(tmp_path)
    (work / "sub" / ".git").mkdir(parents=True)
    job = xv("start", "codex", "-m", "q", "--no-pane", "--write", work).stdout.strip()
    proc = xv("wait", job)
    assert "status=done" in proc.stdout
    assert "nested git repositories" in proc.stderr
    assert json.loads((data / "jobs" / job / "meta.json").read_text())["nested_git"]


def test_start_write_refuses_gitlinks(xv, tmp_path):
    main = tmp_path / "main-wt"
    ident = ["-c", "user.name=t", "-c", "user.email=t@x"]
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    (main / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(main), "add", "a.txt"], check=True)
    sha = "1" * 40
    subprocess.run(["git", "-C", str(main), "update-index", "--add", "--cacheinfo", f"160000,{sha},sub"], check=True)
    subprocess.run(["git", "-C", str(main), *ident, "commit", "-q", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", "../wt", "-b", "lane"], check=True)
    proc = xv("start", "codex", "-m", "q", "--no-pane", "--write", tmp_path / "wt")
    assert proc.returncode == 2
    assert "submodule" in proc.stderr


def test_ask_refuses_gitlinks_added_between_rounds(xv, data, tmp_path):
    work = make_worktree(tmp_path)
    job = xv("start", "codex", "-m", "q", "--no-pane", "--write", work).stdout.strip()
    assert xv("wait", job).returncode == 0
    sha = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(["git", "-C", str(work), "update-index", "--add", "--cacheinfo", f"160000,{sha},sub"], check=True)
    assert xv("ask", job, "-m", "again").returncode == 0
    proc = xv("wait", job)
    assert proc.returncode == 1
    meta = json.loads((data / "jobs" / job / "meta.json").read_text())
    assert meta["status"] == "failed"
    assert "gitlinks" in meta["error"]
    assert "gitlinks" in (data / "jobs" / job / "round-2" / "stderr.log").read_text()
    assert "gitlinks" in proc.stderr


def test_start_write_refuses_second_writer(xv, tmp_path):
    work = make_worktree(tmp_path)
    first = xv("start", "codex", "-m", "q", "--no-pane", "--write", work, XV_FAKE_DELAY=30)
    assert first.returncode == 0, first.stderr
    second = xv("start", "codex", "-m", "q", "--no-pane", "--write", work)
    assert second.returncode == 2
    assert f"another job is still writing {work.resolve()}: {first.stdout.strip()}" in second.stderr
    xv("stop", first.stdout.strip())


def test_ask_rejects_write(xv, tmp_path):
    job = start(xv)
    xv("wait", job)
    proc = xv("ask", job, "-m", "again", "--write", make_worktree(tmp_path))
    assert proc.returncode == 2
    assert "ask only takes" in proc.stderr


def test_start_write_missing_dir(xv, tmp_path):
    proc = xv("start", "codex", "-m", "q", "--no-pane", "--write", tmp_path / "nope")
    assert proc.returncode == 2
    assert f"xv: --write does not exist: {tmp_path / 'nope'}" in proc.stderr


def test_start_write_refuses_git_dir(xv, tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    proc = xv("start", "codex", "-m", "x", "--no-pane", "--write", repo)
    assert proc.returncode == 2
    assert "git worktree add" in proc.stderr


def test_start_write_refuses_separate_git_dir(xv, tmp_path):
    subprocess.run(
        ["git", "init", "-q", "--separate-git-dir", str(tmp_path / "sep.git"), str(tmp_path / "sepwt")], check=True
    )
    proc = xv("start", "codex", "-m", "x", "--no-pane", "--write", tmp_path / "sepwt")
    assert proc.returncode == 2
    assert "git worktree add" in proc.stderr


def test_start_write_refuses_plain_dir(xv, tmp_path):
    (tmp_path / "plain").mkdir()
    proc = xv("start", "codex", "-m", "x", "--no-pane", "--write", tmp_path / "plain")
    assert proc.returncode == 2
    assert "git worktree add" in proc.stderr


def test_start_write_refuses_hidden_or_state_dir(xv, tmp_path):
    for rel in (".codex", ".ssh"):
        base = tmp_path / "home" / rel
        base.mkdir(parents=True, exist_ok=True)
        d = make_worktree(base)
        proc = xv("start", "codex", "-m", "x", "--no-pane", "--write", d)
        assert proc.returncode == 2, proc.stderr
        assert "hides" in proc.stderr


def test_ask_refuses_when_other_writer_running(xv, tmp_path):
    wt = make_worktree(tmp_path)
    a = xv("start", "codex", "-m", "q", "--no-pane", "--write", wt).stdout.strip()
    assert xv("wait", a).returncode == 0
    b = xv("start", "codex", "-m", "q", "--no-pane", "--write", wt, XV_FAKE_DELAY=30)
    assert b.returncode == 0, b.stderr
    proc = xv("ask", a, "-m", "x")
    assert proc.returncode == 2
    assert "another job is still writing" in proc.stderr
    xv("stop", b.stdout.strip())


def test_doctor_ok(xv):
    proc = xv("doctor", "codex")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert re.fullmatch(r"codex  ok  \d+s\n", proc.stdout)


def test_doctor_reports_vendor_error(xv, fixture_path):
    proc = xv("doctor", "codex", XV_FAKE_FIXTURE=fixture_path("codex-failed"))
    assert proc.returncode == 1
    assert proc.stdout.startswith("codex  FAIL")
    assert "This content was flagged" in proc.stdout


def test_doctor_missing_cli(xv):
    proc = xv("doctor", "agy", PATH="/usr/bin:/bin")
    assert proc.returncode == 1
    assert proc.stdout == "agy    FAIL  0s  agy not found on PATH\n"


def test_list_empty(xv):
    proc = xv("list")
    assert (proc.returncode, proc.stdout) == (0, "")


def test_watch_finished_round(xv):
    job = start(xv)
    xv("wait", job)
    proc = xv("watch", job)
    assert proc.returncode == 0
    assert "run    sed -n '1,240p' src/app/paths.py" in proc.stdout
    assert "done | " in proc.stdout and f"xv show {job[-4:]}" in proc.stdout


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
    assert err.startswith("xv: ") and "excludedCommands" in err


def test_run_scrubs_env(xv, tmp_path):
    out = tmp_path / "home" / ".codex" / "env.txt"  # writable state dir inside bwrap
    job = start(xv, XV_FAKE_ENV_OUT=out, XV_PROBE_TOKEN="1")
    assert xv("wait", job).returncode == 0
    names = {line.split("=", 1)[0] for line in out.read_text(encoding="utf-8").splitlines()}
    assert "XV_PROBE_TOKEN" not in names
    assert "PATH" in names
