"""macOS smoke test for the sandbox-exec isolation: run by hand on a Mac with agy and codex logged in.

    uv run python tests/mac_smoke.py

Part 1 runs a probe under the exact wrap() line for agy and checks what it can and cannot touch (codex keeps
only its own Seatbelt sandbox on macOS, so there is no xv line to probe).
Part 2 runs real xv rounds (agy and codex, one follow-up each). Prints PASS/FAIL per check; exit 1 on any FAIL.
Nothing on the host is changed: write probes open existing files for append without writing, and files the
probes create live in the round's tmpdir or are removed again.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from xvendor import isolation

PROBE = r'''
import json, os, socket, subprocess, sys
spec = json.loads(sys.argv[1])
out = {}

def attempt(name, fn):
    try:
        fn()
        out[name] = "ok"
    except Exception as e:
        out[name] = f"{type(e).__name__}: {e}"

def append_open(p):
    os.close(os.open(p, os.O_WRONLY | os.O_APPEND))

def create_remove(p):
    os.close(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL))
    os.remove(p)

def connect(p):
    s = socket.socket(socket.AF_UNIX)
    try:
        s.connect(p)
    finally:
        s.close()

for name, p in spec.get("append", {}).items():
    attempt(name, lambda p=p: append_open(p))
for name, p in spec.get("create", {}).items():
    attempt(name, lambda p=p: create_remove(p))
for name, p in spec.get("read", {}).items():
    attempt(name, lambda p=p: os.listdir(p) if os.path.isdir(p) else open(p, "rb").close())
for name, p in spec.get("socket", {}).items():
    attempt(name, lambda p=p: connect(p))
attempt("dns", lambda: socket.getaddrinfo("example.com", 443))
attempt("signal_parent", lambda: os.kill(spec["outside_pid"], 0))
r = subprocess.run(["/usr/bin/osascript", "-e", 'tell application "Finder" to get name'], capture_output=True, text=True)
out["osascript"] = "ok" if r.returncode == 0 else f"rc={r.returncode} {r.stderr.strip()[:120]}"
try:  # ps is setuid root; the sandbox may refuse to exec it at all
    r = subprocess.run(["/bin/ps", "-p", str(spec["outside_pid"])], capture_output=True, text=True)
    out["ps_outside"] = "ok" if str(spec["outside_pid"]) in r.stdout else "hidden"
except OSError as e:
    out["ps_outside"] = f"ps not runnable: {e}"
out["home"] = os.environ.get("HOME")
out["tmux"] = os.environ.get("TMUX")
print(json.dumps(out))
'''

results: list[tuple[bool, str]] = []


def report(ok: bool, what: str, detail: str = "") -> None:
    results.append((ok, what))
    print(f"{'PASS' if ok else 'FAIL'}  {what}" + (f"  ({detail})" if detail else ""))


def probe(vendor: str, spec: dict, tmpdir: Path) -> dict:
    spec = {**spec, "outside_pid": os.getpid()}
    argv = isolation.wrap(vendor, [sys.executable, "-I", "-c", PROBE, json.dumps(spec)], tmpdir)
    r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        report(False, f"{vendor}: probe ran", f"rc={r.returncode}")
        print(r.stderr)  # a profile compile error names the offending expression
        print(argv[2])
        return {}
    return json.loads(r.stdout.strip().splitlines()[-1])


def expect(vendor: str, got: dict, name: str, allowed: bool) -> None:
    if name not in got:
        return
    ok = (got[name] == "ok") == allowed
    report(ok, f"{vendor}: {name} {'allowed' if allowed else 'denied'}", got[name])


def sockets() -> dict:
    out = {}
    if os.environ.get("SSH_AUTH_SOCK"):
        out["ssh_agent_socket"] = os.environ["SSH_AUTH_SOCK"]
    tmux = Path(os.environ.get("TMUX_TMPDIR") or "/private/tmp") / f"tmux-{os.getuid()}" / "default"
    if tmux.exists():
        out["tmux_socket"] = str(tmux)
    docker = Path.home() / ".docker/run/docker.sock"
    if docker.exists():
        out["docker_socket"] = str(docker)
    return out


def part1() -> None:
    home = Path.home()
    secrets = {f"read_{p.name}": str(p) for p in (home / ".ssh", home / ".gemini/oauth_creds.json") if p.exists()}
    outside = home / f"xv-smoke-{os.getpid()}"

    agy = isolation.state_dir("agy")
    with tempfile.TemporaryDirectory(prefix="xv-smoke-") as t:
        tmp = Path(t)
        fake_bin = tmp / "home" / agy.relative_to(home) / "bin"
        got = probe("agy", {
            "append": {"write_real_settings": str(agy / "settings.json"),
                       "write_real_agentapi": str(agy / "bin" / "agentapi")},
            "create": {"create_outside_home": str(outside), "create_in_real_bin": str(agy / "bin" / "xv-smoke"),
                       "create_in_real_builtin": str(agy / "builtin" / "xv-smoke"),
                       "create_in_fake_bin": str(fake_bin / "xv-smoke"),
                       "create_in_conversations": str(agy / "conversations" / f"xv-smoke-{os.getpid()}"),
                       "create_in_tmpdir": str(tmp / "x")},
            "read": secrets, "socket": sockets()}, tmp)
        if got:
            report(got["home"] == str(tmp / "home"), "agy: HOME is the fake home", got["home"])
            report(got["tmux"] is None, "agy: TMUX unset")
        for name in ("write_real_settings", "write_real_agentapi", "create_outside_home", "create_in_real_bin",
                     "create_in_real_builtin", *secrets, *sockets(), "signal_parent", "osascript"):
            expect("agy", got, name, False)
        for name in ("create_in_fake_bin", "create_in_conversations", "create_in_tmpdir", "dns"):
            expect("agy", got, name, True)
        if got:  # informational: no pid namespace on macOS (known gap)
            print(f"INFO  agy: ps sees processes outside the sandbox: {got['ps_outside']}")



def xv(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "xvendor", *args], capture_output=True, text=True, timeout=timeout)


def part2() -> None:
    agentapi = isolation.state_dir("agy") / "bin" / "agentapi"
    before = agentapi.stat().st_mtime if agentapi.exists() else None
    cwd = tempfile.mkdtemp(prefix="xv-smoke-cwd-")
    for vendor in ("agy", "codex"):
        start = subprocess.run([sys.executable, "-m", "xvendor", "start", vendor, "--no-pane", "-m",
                                "Run the shell command `echo xv-smoke-hi` and reply with its output only."],
                               capture_output=True, text=True, cwd=cwd)
        if start.returncode != 0:
            report(False, f"{vendor}: xv start", start.stderr.strip()[-400:])
            continue
        job = start.stdout.strip()
        for n, check in ((1, "xv-smoke-hi"), (2, None)):
            if n == 2:
                ask = xv("ask", job, "-m", "What exact text did the command print? Reply with that text only.")
                if ask.returncode != 0:
                    report(False, f"{vendor}: xv ask", ask.stderr.strip()[-400:])
                    break
            w = xv("wait", job)
            body = xv("show", job, str(n)).stdout
            ok = w.returncode == 0 and "xv-smoke-hi" in body
            detail = w.stdout.strip() if ok else (w.stdout.strip() + " | " + body.strip()[-300:])
            report(ok, f"{vendor}: round {n} done and answered", detail)
            if not ok:
                stderr_log = Path(w.stdout.split("response=")[-1].strip()).parent / "stderr.log"
                if stderr_log.is_file():
                    print(stderr_log.read_text(errors="replace")[-1500:])
                break
    shutil.rmtree(cwd, ignore_errors=True)
    after = agentapi.stat().st_mtime if agentapi.exists() else None
    report(before == after, "agy: host bin/agentapi unchanged", f"{before} -> {after}")


def main() -> int:
    if sys.platform != "darwin":
        print("mac_smoke.py runs on macOS only", file=sys.stderr)
        return 2
    print(f"macOS {subprocess.run(['sw_vers', '-productVersion'], capture_output=True, text=True).stdout.strip()}")
    part1()
    part2()
    failed = [w for ok, w in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed" + (f" ({time.strftime('%F %T')})"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
