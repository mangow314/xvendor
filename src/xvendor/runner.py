import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from xvendor.events import Event
from xvendor.isolation import UnsupportedPlatform, has_gitlinks, nested_git, wrap
from xvendor.jobs import JobError, JobStore
from xvendor.vendors.base import Vendor

KILL_GRACE = 10  # seconds between SIGTERM and SIGKILL, like `timeout -k 10`
SECRET_ENV = re.compile(r"(?i)key|token|secret|passw|cred|auth")
KEEP_ENV = ("XV_DATA", "XV_PANE", "CODEX_HOME", "PATH", "HOME", "TMPDIR", "TERM", "LANG", "TMUX", "TMUX_PANE")


def scrub_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Drop variables whose name looks like a credential; vendors log in through files, not env.

    The GIT_CONFIG_* family goes as a whole: dropping only GIT_CONFIG_KEY_n leaves
    GIT_CONFIG_COUNT behind and git then refuses to run (rc 128).
    """
    return {
        k: v
        for k, v in environ.items()
        if not k.startswith("GIT_CONFIG_") and (k in KEEP_ENV or k.startswith("LC_") or not SECRET_ENV.search(k))
    }


def _killpg(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def run_round(
    store: JobStore,
    job: str,
    n: int,
    vendor: Vendor,
    on_event: Callable[[Event], None] = lambda e: None,
    *,
    popen_env: dict | None = None,
) -> str:
    """Run round n of job to completion and return its final status (spec R4)."""
    meta = store.meta(job)
    rd = store.round_dir(job, n)
    prompt = (rd / "request.md").read_text(encoding="utf-8").rstrip("\n")
    store.update(job, run_pid=os.getpid())
    tmpdir = Path(tempfile.mkdtemp(prefix=f"xv-{vendor.name}-"))
    proc = None
    final: dict = {}
    timers: list[threading.Timer] = []
    timed_out = threading.Event()
    t0 = time.monotonic()
    try:
        writable = Path(meta["writable"]) if meta.get("writable") else None
        try:
            built = vendor.build(n, meta["session"], prompt, meta["profile"], meta.get("mode", "read"))
            if meta.get("mode") == "write" and has_gitlinks(Path(meta["writable"])):
                raise RuntimeError(f"{meta['writable']} now has submodule entries (gitlinks); refusing the write round")
            argv = wrap(vendor.name, built, tmpdir, writable)
        except (UnsupportedPlatform, ValueError, RuntimeError) as e:
            msg = str(e)
            (rd / "stderr.log").write_text(msg + "\n", encoding="utf-8")
            final = {"status": "failed", "run_pid": None, "run_pgid": None, "error": msg}
            raise
        lines: list[str] = []
        with open(rd / "stderr.log", "wb") as err, open(rd / "events.jsonl", "wb") as out:
            proc = subprocess.Popen(
                argv,
                cwd=meta["cwd"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=err,
                start_new_session=True,
                env=popen_env,
            )
            # start_new_session makes the child its own process group leader: pgid == pid
            store.update(job, run_pgid=proc.pid)

            def expire():
                timed_out.set()
                _killpg(proc.pid, signal.SIGTERM)
                kill = threading.Timer(KILL_GRACE, _killpg, (proc.pid, signal.SIGKILL))
                kill.daemon = True
                timers.append(kill)
                kill.start()

            timer = threading.Timer(meta["timeout"], expire)
            timer.daemon = True
            timers.append(timer)
            timer.start()

            for raw in proc.stdout:
                out.write(raw)
                out.flush()
                line = raw.decode("utf-8", "replace").rstrip("\n")
                lines.append(line)
                if (ev := vendor.parse(line)) is not None:
                    on_event(ev)
            rc = proc.wait()
        for t in timers:
            t.cancel()
        if rc < 0:  # killed by a signal: report it the way a shell would
            rc = 128 - rc

        session, response, ok = vendor.result(lines)
        (rd / "response.md").write_text(response + "\n", encoding="utf-8")
        if store.meta(job)["stop_requested"]:
            status = "stopped"
        elif timed_out.is_set():
            status = "timeout"
        elif rc != 0 or not ok or not response.strip():
            status = "failed"
        else:
            status = "done"
        final = dict(
            status=status,
            rc=rc,
            secs=int(time.monotonic() - t0),
            run_pid=None,
            run_pgid=None,
            session=session or meta["session"],
            error=None,
        )
        return status
    finally:
        for t in timers:
            t.cancel()
        # an exception mid-round (e.g. the display's terminal went away) must not orphan the vendor CLI
        if proc is not None and proc.poll() is None:
            _killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        shutil.rmtree(tmpdir, ignore_errors=True)
        # one update writes the final status and nested_git together: `wait` returns as soon as status leaves running
        if not final and store.meta(job)["status"] == "running":
            final = dict(status="failed", run_pid=None, run_pgid=None, error=None)
        if meta.get("mode") == "write":
            final["nested_git"] = nested_git(Path(meta["writable"]))
        if final:
            store.update(job, **final)


def stop(store: JobStore, job: str) -> None:
    """Kill the current round's process group; the session is kept so the job can be asked again (R5)."""
    meta = store.meta(job)
    if meta["status"] != "running":
        raise JobError(f"job is not running: {job}")
    pgid = meta.get("run_pgid")
    if not pgid:
        raise JobError(f"job has no run_pgid yet (just started? try again shortly): {job}")
    store.update(job, stop_requested=True)
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        raise JobError(f"no running process found (pgid {pgid})") from None
