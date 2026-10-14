import argparse
import errno
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from xvendor import isolation, panes, runner
from xvendor.jobs import JobError, JobStore

DEFAULT_TIMEOUT = 1200
PROFILE_RE = re.compile(r"[A-Za-z0-9_-]+")


class Fail(Exception):
    """Invalid input or state: printed as `xv: <msg>`, rc 2."""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(f"xv: {message}", file=sys.stderr)
        raise SystemExit(2)


def nonneg_int(value: str) -> int:
    if not re.fullmatch(r"[0-9]+", value):
        raise argparse.ArgumentTypeError(f"must be a non-negative integer: {value!r}")
    return int(value)


def vendor_for(name: str):
    # imported lazily so `xv list` does not pay for vendor modules
    if name == "codex":
        from xvendor.vendors.codex import CodexVendor
        return CodexVendor()
    from xvendor.vendors.agy import AgyVendor
    return AgyVendor()


def add_request(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", help="read the request from a file")
    g.add_argument("-m", dest="message", help="request text")


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(prog="xv", description="Cross-vendor headless CLI jobs (codex / agy).")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("start", help="start a new job (codex|agy)")
    p.add_argument("vendor", choices=["codex", "agy"])
    add_request(p)
    p.add_argument("--cwd")
    p.add_argument("--write", metavar="DIR", help="let the vendor write inside DIR only (default cwd = DIR)")
    p.add_argument("--timeout", type=nonneg_int, default=DEFAULT_TIMEOUT)
    p.add_argument("--profile")
    p.add_argument("--no-pane", action="store_true")

    p = sub.add_parser("ask", help="send a follow-up round to a job")
    p.add_argument("job")
    add_request(p)
    p.add_argument("--timeout", type=nonneg_int)
    # accepted only to reject them with a clear message (R8)
    p.add_argument("--cwd", help=argparse.SUPPRESS)
    p.add_argument("--write", help=argparse.SUPPRESS)
    p.add_argument("--profile", help=argparse.SUPPRESS)
    p.add_argument("--no-pane", action="store_true", help=argparse.SUPPRESS)

    p = sub.add_parser("wait", help="wait for the current round to finish")
    p.add_argument("job")
    p.add_argument("--timeout", type=nonneg_int)

    p = sub.add_parser("show", help="print a round's response")
    p.add_argument("job")
    p.add_argument("round", nargs="?", type=nonneg_int)

    sub.add_parser("list", help="list jobs")

    for name, text in [("stop", "stop the current round (session kept)"),
                       ("close", "close a finished job's pane"),
                       ("watch", "follow the current round in this terminal")]:
        sub.add_parser(name, help=text).add_argument("job")

    p = sub.add_parser("_run")  # internal: one round, started by a pane or in the background
    p.add_argument("job")
    p.add_argument("round", type=nonneg_int)
    return parser


def read_request(args) -> str:
    if args.file is not None:
        try:
            req = Path(args.file).read_text(encoding="utf-8")
        except OSError as e:
            raise Fail(f"cannot read --file {args.file}: {e.strerror}") from None
    else:
        req = args.message
    if not req.strip():
        raise Fail("needs --file or -m, and the request must not be empty")
    return req.rstrip("\n")


def cmd_start(store: JobStore, args) -> int:
    req = read_request(args)
    writable = None
    if args.write is not None:
        if not Path(args.write).is_dir():
            raise Fail(f"--write does not exist: {args.write}")
        writable = Path(args.write).resolve()
        if hit := isolation.writable_overlaps_hidden(Path.home(), writable, isolation.state_dir(args.vendor)):
            raise Fail(f"--write refuses {writable}: it is under {hit}, which xv hides from the vendor")
        try:
            isolation.linked_worktree_gitdir(Path(args.write), isolation.state_dir(args.vendor))
        except ValueError as e:
            raise Fail(str(e)) from None
        try:
            if isolation.has_gitlinks(writable):
                raise Fail(f"--write refuses {writable}: its index has submodule entries (gitlinks); a vendor could plant "
                           "config in a submodule that your next git diff would run. Use a worktree of a repository "
                           "without submodules")
        except ValueError as e:
            raise Fail(f"--write: {e}") from None
    cwd = Path(args.cwd or writable or os.getcwd())
    if not cwd.is_dir():
        raise Fail(f"--cwd does not exist: {args.cwd}")
    try:
        isolation.check(args.vendor)
    except isolation.UnsupportedPlatform as e:
        raise Fail(str(e)) from None
    if args.profile is not None:
        if args.vendor != "codex":
            raise Fail("--profile is only supported for codex")
        if not PROFILE_RE.fullmatch(args.profile):
            raise Fail("invalid --profile name (letters, digits, _ and - only)")
        codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        if not (codex_home / f"{args.profile}.config.toml").is_file():
            raise Fail(f"codex profile not found: {codex_home / (args.profile + '.config.toml')}")
    if writable is not None and (busy := store.running_writable(str(writable))):
        raise Fail(f"another job is still writing {writable}: {busy}")
    job = store.create(args.vendor, str(cwd.resolve()), args.timeout, args.no_pane, args.profile, req,
                       str(writable) if writable else None)
    panes.launch(store, job, 1)
    print(job)
    return 0


def cmd_ask(store: JobStore, args) -> int:
    job = store.resolve(args.job)
    if args.profile is not None or args.cwd is not None or args.write is not None or args.no_pane:
        raise Fail("ask only takes --file, -m and --timeout; profile, cwd, write dir and pane stay as set by start")
    req = read_request(args)
    meta = store.meta(job)
    if meta["status"] == "running":
        raise Fail(f"job is still running; run xv wait {job} first")
    if not meta["session"]:
        raise Fail("job has no session id (the last round may have failed), so it cannot be asked again; start a new job")
    if meta.get("mode", "read") == "write" and (busy := store.running_writable(meta["writable"])):
        raise Fail(f"another job is still writing {meta['writable']}: {busy}")
    n = store.new_round(job, req)
    if args.timeout is not None:
        store.update(job, timeout=args.timeout)
    panes.launch(store, job, n)
    print(f"{job} round-{n}")
    return 0


def cmd_wait(store: JobStore, args) -> int:
    job = store.resolve(args.job)
    limit = args.timeout if args.timeout is not None else store.meta(job)["timeout"] + 60
    start = time.monotonic()
    while (meta := store.meta(job))["status"] == "running":
        if time.monotonic() - start >= limit:
            print(f"xv: wait timed out after {limit}s; the job is still running", file=sys.stderr)
            return 1
        time.sleep(2)
    n = meta["round"]
    secs = "" if meta["secs"] is None else meta["secs"]
    print(f"status={meta['status']} round={n} secs={secs} response={store.round_dir(job, n) / 'response.md'}")
    if meta["status"] != "done" and (error := meta.get("error")):
        print(f"xv: {error}", file=sys.stderr)
    if nested := meta.get("nested_git"):
        print("xv: nested git repositories (remove before any git command): " + ", ".join(nested), file=sys.stderr)
    return 0 if meta["status"] == "done" else 1


def cmd_show(store: JobStore, args) -> int:
    job = store.resolve(args.job)
    n = args.round if args.round is not None else store.meta(job)["round"]
    path = store.round_dir(job, n) / "response.md"
    if not path.is_file():
        raise Fail(f"round {n} has no response: {path}")
    if nested := store.meta(job).get("nested_git"):
        print("xv: nested git repositories (remove before any git command): " + ", ".join(nested), file=sys.stderr)
    sys.stdout.write(path.read_text(encoding="utf-8"))
    return 0


def cmd_list(store: JobStore, args) -> int:
    rows = [[job, m["vendor"], f"r{m['round']}", m["status"], "-" if m["secs"] is None else str(m["secs"]),
             f"write:{m['writable']}" if m.get("mode", "read") == "write" else "-"]
            for job, m in store.list()]
    if rows:
        widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
        for r in rows:
            print("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())
    return 0


def cmd_stop(store: JobStore, args) -> int:
    job = store.resolve(args.job)
    runner.stop(store, job)
    print(f"stop sent: {job} (xv wait shows it ending; then xv ask can change direction)")
    return 0


def cmd_close(store: JobStore, args) -> int:
    job = store.resolve(args.job)
    meta = store.meta(job)
    if meta["status"] == "running":
        raise Fail(f"job is still running; run xv stop {job} first")
    panes.close_pane(meta["pane"])
    store.update(job, pane=None)
    return 0


def _stderr_tail(rd: Path) -> list[str]:
    try:
        return (rd / "stderr.log").read_text(encoding="utf-8", errors="replace").splitlines()[-5:]
    except OSError:
        return []


def _finish(display, store: JobStore, job: str, n: int) -> None:
    meta = store.meta(job)
    rd = store.round_dir(job, n)
    try:
        response = (rd / "response.md").read_text(encoding="utf-8")
    except OSError:
        response = ""
    display.finish(meta["status"], meta["secs"] or 0, job, response, _stderr_tail(rd))


def cmd_run(store: JobStore, args) -> int:
    from xvendor.display import Display
    job, n = store.resolve(args.job), args.round
    meta = store.meta(job)
    # closing the pane sends SIGHUP: exit through run_round's cleanup so the vendor CLI is killed
    # and the job does not stay "running" forever (R4)
    for sig in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(sig, lambda signum, frame: sys.exit(128 + signum))
    carry = store.round_dir(job, n) / panes.CARRY
    if carry.is_file():
        sys.stdout.write(carry.read_text(encoding="utf-8"))
        sys.stdout.flush()
        carry.unlink()
    display = Display()
    display.header(meta["vendor"], job, n, meta["timeout"], meta.get("mode", "read"), meta.get("writable"))
    rc = 0
    try:
        runner.run_round(store, job, n, vendor_for(meta["vendor"]), display.event,
                         popen_env=runner.scrub_env(os.environ))
        if nested := store.meta(job).get("nested_git"):
            display.warn(f"nested git repositories under {meta['writable']}: remove them before running any git "
                         "command in the worktree: " + ", ".join(nested))
        _finish(display, store, job, n)
    except Exception as e:  # runner already marked the job failed; keep the message visible
        print(f"xv: _run failed: {e}", file=sys.stderr)
        rc = 1
    if os.environ.get("XV_PANE") == "1":
        # keep the pane for reading; the next respawn-pane -k or `xv close` ends it
        while True:
            signal.pause()
    return rc


def cmd_watch(store: JobStore, args) -> int:
    from xvendor.display import Display
    job = store.resolve(args.job)
    meta = store.meta(job)
    n = meta["round"]
    vendor = vendor_for(meta["vendor"])
    display = Display()
    display.header(meta["vendor"], job, n, meta["timeout"], meta.get("mode", "read"), meta.get("writable"))
    events = store.round_dir(job, n) / "events.jsonl"
    f = None
    buf = ""
    try:
        while True:
            if f is None and events.exists():
                f = open(events, encoding="utf-8", errors="replace")
            chunk = f.read() if f else ""
            if chunk:
                buf += chunk
                *complete, buf = buf.split("\n")
                for line in complete:
                    if (ev := vendor.parse(line)) is not None:
                        display.event(ev)
                continue
            # read meta only after draining the file: the runner writes events before the final status
            if store.meta(job)["status"] != "running":
                if f is not None and (rest := buf + f.read()):
                    for line in rest.split("\n"):
                        if (ev := vendor.parse(line)) is not None:
                            display.event(ev)
                break
            time.sleep(0.3)
    finally:
        if f is not None:
            f.close()
    _finish(display, store, job, n)
    return 0


COMMANDS = {"start": cmd_start, "ask": cmd_ask, "wait": cmd_wait, "show": cmd_show, "list": cmd_list,
            "stop": cmd_stop, "close": cmd_close, "_run": cmd_run, "watch": cmd_watch}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_usage()
        raise SystemExit(2)
    try:
        store = JobStore(Path(os.environ.get("XV_DATA") or Path.home() / ".local/share/xvendor"))
        rc = COMMANDS[args.command](store, args)
    except (Fail, JobError) as e:
        print(f"xv: {e}", file=sys.stderr)
        rc = 2
    except OSError as e:
        print(f"xv: {e}", file=sys.stderr)
        if e.errno == errno.EROFS:
            # xv chained with another command (cd, ;, &&, |) stays inside Claude Code's Bash sandbox
            print("xv: inside Claude Code's Bash sandbox, list xv in sandbox.excludedCommands and run it as "
                  "a command of its own (no cd, ;, && or |): chained calls stay sandboxed", file=sys.stderr)
        rc = 1
    except KeyboardInterrupt:
        rc = 130
    raise SystemExit(rc)
