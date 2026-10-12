import json
import os
import shutil
import sys
import threading
import time

import pytest

from xvendor import runner
from xvendor.jobs import JobError, JobStore
from xvendor.vendors.agy import AgyVendor
from xvendor.vendors.codex import CodexVendor

needs_bwrap = pytest.mark.skipif(sys.platform != "linux" or shutil.which("bwrap") is None, reason="needs bwrap")


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "data")


@pytest.fixture
def env(fakes_dir):
    def make(fixture=None, **extra):
        e = {k: v for k, v in os.environ.items() if not k.startswith("XV_FAKE_")}
        e["PATH"] = f"{fakes_dir}{os.pathsep}{e.get('PATH', '')}"
        if fixture is not None:
            e["XV_FAKE_FIXTURE"] = str(fixture)
        e.update({k: str(v) for k, v in extra.items()})
        return e
    return make


def new_job(store, tmp_path, vendor="codex", timeout=30):
    return store.create(vendor, str(tmp_path), timeout, True, None, "question")


def stop_when_running(store, job):
    def go():
        for _ in range(200):
            if store.meta(job).get("run_pgid"):
                runner.stop(store, job)
                return
            time.sleep(0.05)
    t = threading.Thread(target=go)
    t.start()
    return t


def test_codex_done(store, tmp_path, env, fixture_path):
    job = new_job(store, tmp_path)
    status = runner.run_round(store, job, 1, CodexVendor(), popen_env=env(fixture_path("codex-done")))
    m = store.meta(job)
    rd = store.round_dir(job, 1)
    assert status == "done" == m["status"]
    assert m["rc"] == 0 and type(m["secs"]) is int
    assert m["session"] == "01a126af-a00e-7501-bc5e-5b7215dc60ac"
    assert m["run_pid"] is None and m["run_pgid"] is None
    assert (rd / "events.jsonl").read_bytes() == fixture_path("codex-done").read_bytes()
    assert (rd / "response.md").read_text(encoding="utf-8").startswith("### Data directory paths")


def test_argv_reaches_cli(store, tmp_path, env, fixture_path):
    job = new_job(store, tmp_path)
    out = tmp_path / "argv.json"
    runner.run_round(store, job, 1, CodexVendor(), popen_env=env(fixture_path("codex-done"), XV_FAKE_ARGV_OUT=out))
    assert json.loads(out.read_text()) == CodexVendor().build(1, None, "question", None)[1:]


def test_nonzero_rc_failed(store, tmp_path, env, fixture_path):
    job = new_job(store, tmp_path)
    status = runner.run_round(store, job, 1, CodexVendor(), popen_env=env(fixture_path("codex-done"), XV_FAKE_RC=1))
    assert status == "failed"
    assert store.meta(job)["rc"] == 1


def test_turn_failed_is_failed(store, tmp_path, env, fixture_path):
    job = new_job(store, tmp_path)
    assert runner.run_round(store, job, 1, CodexVendor(), popen_env=env(fixture_path("codex-failed"))) == "failed"
    assert store.meta(job)["session"] == "01a1263b-7821-7c52-b722-51a025c3338b"


def test_timeout(store, tmp_path, env, fixture_path):
    job = new_job(store, tmp_path, timeout=1)
    t0 = time.monotonic()
    status = runner.run_round(store, job, 1, CodexVendor(), popen_env=env(fixture_path("codex-done"), XV_FAKE_DELAY=5))
    assert status == "timeout"
    assert time.monotonic() - t0 < 5
    assert store.meta(job)["rc"] == 143


def test_stop_keeps_session(store, tmp_path, env, fixture_path):
    job = new_job(store, tmp_path)
    store.update(job, session="kept-session")
    t = stop_when_running(store, job)
    status = runner.run_round(store, job, 1, CodexVendor(),
                              popen_env=env(fixture_path("codex-done"), XV_FAKE_DELAY=2))
    t.join()
    m = store.meta(job)
    assert status == "stopped" == m["status"]
    assert m["stop_requested"] is True
    assert m["session"] == "kept-session"


@needs_bwrap
def test_agy_stop_kills_whole_group(store, tmp_path, env, fixture_path):
    job = new_job(store, tmp_path, vendor="agy")
    seen = {}

    def go():
        for _ in range(200):
            if pgid := store.meta(job).get("run_pgid"):
                seen["pgid"] = pgid
                runner.stop(store, job)
                return
            time.sleep(0.05)
    t = threading.Thread(target=go)
    t.start()
    status = runner.run_round(store, job, 1, AgyVendor(), popen_env=env(fixture_path("agy-done"), XV_FAKE_DELAY=2))
    t.join()
    assert status == "stopped"
    with pytest.raises(ProcessLookupError):
        os.killpg(seen["pgid"], 0)


@needs_bwrap
def test_agy_done_through_bwrap(store, tmp_path, env, fixture_path):
    job = new_job(store, tmp_path, vendor="agy")
    assert runner.run_round(store, job, 1, AgyVendor(), popen_env=env(fixture_path("agy-done"))) == "done"
    assert store.meta(job)["session"] == "0a85aca6-fc8a-46bd-9c66-81ccf6534908"


@pytest.mark.parametrize("name, actions", [("codex-done", 1), ("codex-failed", 4)])
def test_on_event_actions(store, tmp_path, env, fixture_path, name, actions):
    job = new_job(store, tmp_path)
    got = []
    runner.run_round(store, job, 1, CodexVendor(), got.append, popen_env=env(fixture_path(name)))
    assert sum(e.kind == "action" for e in got) == actions


def test_exception_marks_failed(store, tmp_path, env, fixture_path):
    class Broken(CodexVendor):
        def result(self, lines):
            raise RuntimeError("boom")

    job = new_job(store, tmp_path)
    with pytest.raises(RuntimeError):
        runner.run_round(store, job, 1, Broken(), popen_env=env(fixture_path("codex-done")))
    assert store.meta(job)["status"] == "failed"


def test_stop_rejects_not_running(store, tmp_path):
    job = new_job(store, tmp_path)
    store.update(job, status="done")
    with pytest.raises(JobError):
        runner.stop(store, job)


def test_stop_rejects_missing_pgid(store, tmp_path):
    job = new_job(store, tmp_path)
    with pytest.raises(JobError, match="try again"):
        runner.stop(store, job)
