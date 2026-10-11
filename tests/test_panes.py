import os
import shutil
import subprocess
import time

import pytest

from xvendor import panes
from xvendor.jobs import JobStore


def _tmux_usable(tmp_path_factory) -> str | None:
    """Return why an isolated tmux server cannot run here (e.g. sandbox blocks unix sockets), or None."""
    if shutil.which("tmux") is None:
        return "tmux not installed"
    sock = tmp_path_factory.mktemp("tmuxprobe") / "s"
    proc = subprocess.run(["tmux", "-S", str(sock), "-f", "/dev/null", "new-session", "-d", "sleep 5"],
                          capture_output=True, text=True)
    subprocess.run(["tmux", "-S", str(sock), "kill-server"], capture_output=True)
    return None if proc.returncode == 0 else f"cannot start an isolated tmux server: {proc.stderr.strip()}"


@pytest.fixture
def server(tmp_path_factory, monkeypatch, fakes_dir, fixture_path):
    if reason := _tmux_usable(tmp_path_factory):
        pytest.skip(reason)
    sock = tmp_path_factory.mktemp("tmux") / "s"
    env = {k: v for k, v in os.environ.items() if not k.startswith("TMUX")}
    # panes inherit the server's environment: point vendor CLIs at the fakes
    env.update(PATH=f"{fakes_dir}{os.pathsep}{env.get('PATH', '')}", XV_FAKE_FIXTURE=str(fixture_path("codex-done")))
    subprocess.run(["tmux", "-S", str(sock), "-f", "/dev/null", "new-session", "-d", "-x", "200", "-y", "50"],
                   check=True, env=env)
    caller = subprocess.run(["tmux", "-S", str(sock), "display-message", "-p", "#{pane_id}"],
                            capture_output=True, text=True, check=True).stdout.strip()
    # with $TMUX set, a plain `tmux` client talks to this server
    monkeypatch.setenv("TMUX", f"{sock},0,0")
    monkeypatch.setenv("TMUX_PANE", caller)
    yield caller
    subprocess.run(["tmux", "-S", str(sock), "kill-server"], capture_output=True)


def fmt(pane, f):
    return subprocess.run(["tmux", "display-message", "-p", "-t", pane, f],
                          capture_output=True, text=True, check=True).stdout.strip()


def pane_count(pane):
    return len(subprocess.run(["tmux", "list-panes", "-t", pane], capture_output=True, text=True).stdout.splitlines())


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "data")


def new_job(store, tmp_path):
    return store.create("codex", str(tmp_path), 60, False, None, "q")


def test_first_pane_splits_caller_horizontally(server, store, tmp_path):
    job = new_job(store, tmp_path)
    panes.launch(store, job, 1)
    pane = store.meta(job)["pane"]
    assert pane and pane != server
    assert fmt(pane, "#{pane_top}") == fmt(server, "#{pane_top}")
    assert int(fmt(pane, "#{pane_left}")) > int(fmt(server, "#{pane_left}"))
    assert fmt(pane, "#{@xv}") == "1"


def test_second_pane_stacks_under_first(server, store, tmp_path):
    a, b = new_job(store, tmp_path), new_job(store, tmp_path)
    panes.launch(store, a, 1)
    panes.launch(store, b, 1)
    pa, pb = store.meta(a)["pane"], store.meta(b)["pane"]
    assert fmt(pb, "#{pane_left}") == fmt(pa, "#{pane_left}")
    assert int(fmt(pb, "#{pane_top}")) > int(fmt(pa, "#{pane_top}"))
    assert fmt(pb, "#{@xv}") == "1"
    assert pane_count(server) == 3


def test_respawn_reuses_pane(server, store, tmp_path):
    job = new_job(store, tmp_path)
    panes.launch(store, job, 1)
    first = store.meta(job)["pane"]
    for _ in range(100):
        if store.meta(job)["status"] != "running":
            break
        time.sleep(0.1)
    n = store.new_round(job, "again")
    panes.launch(store, job, n)
    assert store.meta(job)["pane"] == first
    assert pane_count(server) == 2


def test_close_leaves_unmarked_pane(server, store, tmp_path):
    panes.close_pane(server)
    assert fmt(server, "#{pane_id}") == server
    job = new_job(store, tmp_path)
    panes.launch(store, job, 1)
    pane = store.meta(job)["pane"]
    panes.close_pane(pane)
    assert pane_count(server) == 1


def history(pane):
    return subprocess.run(["tmux", "capture-pane", "-p", "-S", "-", "-t", pane],
                          capture_output=True, text=True, check=True).stdout


def wait_done(store, job):
    for _ in range(100):
        if store.meta(job)["status"] != "running":
            break
        time.sleep(0.1)
    time.sleep(1)  # _run prints the finish block after the final meta update


def test_respawn_keeps_previous_round_end(server, store, tmp_path):
    job = new_job(store, tmp_path)
    panes.launch(store, job, 1)
    wait_done(store, job)
    n = store.new_round(job, "again")
    panes.launch(store, job, n)
    wait_done(store, job)
    # one finish line per round: none lost to respawn, none duplicated by the carry-over
    assert history(store.meta(job)["pane"]).count(f"看全文：xv show {job[-4:]}") == 2
