"""tmux backend (spec R6); outside tmux the round runs as a detached background process.

Every tmux call passes a list, and the pane command is given as several arguments, so tmux execs it
directly without `sh -c` (tmux(1), "shell-command [argument ...]", verified on tmux 3.7c).
"""

import os
import subprocess
import sys

from xvendor.jobs import JobStore

CARRY = "pane-carry.txt"  # previous round's visible pane text, printed and removed by the next _run


def in_tmux() -> bool:
    return bool(os.environ.get("TMUX"))


def _tmux(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=True, text=True)


def pane_alive(pane: str | None) -> bool:
    """True only for a live pane carrying our @xv mark: tmux reuses pane ids after a server restart."""
    if not pane:
        return False
    proc = _tmux("display-message", "-p", "-t", pane, "#{@xv}")
    return proc.returncode == 0 and proc.stdout.strip() == "1"


def _run_cmd(job: str, n: int) -> list[str]:
    return [sys.executable, "-m", "xvendor", "_run", job, str(n)]


def launch(store: JobStore, job: str, n: int) -> None:
    meta = store.meta(job)
    cwd = meta["cwd"]
    if not in_tmux() or meta["no_pane"]:
        subprocess.Popen(
            _run_cmd(job, n),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return

    # a new pane gets the tmux server's environment, not ours
    env = ["-e", f"XV_DATA={store.jobs.parent}", "-e", "XV_PANE=1"]
    if pane_alive(meta["pane"]):
        # respawn-pane clears the visible screen without moving it into history, so the end of the
        # previous round would be lost when scrolling back; save it for the next _run to print first
        shown = _tmux("capture-pane", "-p", "-e", "-t", meta["pane"]).stdout.rstrip()
        if shown:
            (store.round_dir(job, n) / CARRY).write_text(shown + "\n", encoding="utf-8")
        _tmux("respawn-pane", "-k", "-t", meta["pane"], "-c", cwd, *env, *_run_cmd(job, n))
        return

    # stack all xv panes in one right-hand column: split the caller once (40%), then split the last xv pane
    caller = os.environ.get("TMUX_PANE", "")
    listed = _tmux("list-panes", "-t", caller, "-F", "#{pane_id} #{@xv}").stdout.split("\n")
    marked = [line.split()[0] for line in listed if line.endswith(" 1")]
    split = ["-v", "-t", marked[-1]] if marked else ["-h", "-l", "40%", "-t", caller]
    proc = _tmux("split-window", "-d", *split, "-P", "-F", "#{pane_id}", "-c", cwd, *env, *_run_cmd(job, n))
    if proc.returncode != 0:
        raise RuntimeError(f"tmux split-window failed: {proc.stderr.strip()}")
    pane = proc.stdout.strip()
    _tmux("set-option", "-p", "-t", pane, "@xv", "1")
    store.update(job, pane=pane)


def close_pane(pane: str | None) -> None:
    if pane_alive(pane):
        _tmux("kill-pane", "-t", pane)
