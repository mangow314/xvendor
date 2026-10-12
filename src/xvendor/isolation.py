import os
import shutil
import sys
from pathlib import Path


class UnsupportedPlatform(Exception):
    pass


# Home-relative credential paths hidden from agy. Mirrors the concrete home paths in Claude Code's
# sandbox denyRead / Read() deny rules (chezmoi safetyLists, 2026-10-11); update both together.
SECRET_PATHS = (".ssh", ".aws", ".gnupg", ".config/gh", ".kube", ".git-credentials", ".npmrc", ".pgpass",
                ".netrc", ".claude/.credentials.json",
                ".codex/auth.json")  # not in Claude's list; codex's own login token

# Under ~/.gemini/antigravity-cli: config (hooks / MCP), the agentapi launcher script, built-in skills.
STATE_READONLY = ("settings.json", "bin", "builtin")


def check(vendor: str) -> None:
    """Raise UnsupportedPlatform when vendor cannot be run read-only here (R3: refuse, never degrade)."""
    if vendor == "codex":
        return
    if vendor != "agy":
        raise UnsupportedPlatform(f"no isolation defined for vendor {vendor!r}")
    if sys.platform != "linux":
        raise UnsupportedPlatform(f"agy needs bubblewrap for read-only isolation; not available on {sys.platform}")
    if shutil.which("bwrap") is None:
        raise UnsupportedPlatform("agy needs bubblewrap (bwrap) for read-only isolation; bwrap not found")


def wrap(vendor: str, argv: list[str], tmpdir: Path) -> list[str]:
    """Return argv wrapped so the vendor CLI cannot write outside its own state dirs (spec R3)."""
    check(vendor)
    if vendor == "codex":
        # codex enforces read-only itself (-s read-only / sandbox_mode)
        return list(argv)

    # agy's own --sandbox only covers shell; its file tools still write (2026-10-10). A read-only
    # mount does not stop host sockets (tmux, systemd --user) from running commands outside, so hide them.
    uid = os.getuid()
    hide = []
    for d in (f"/run/user/{uid}", f"{os.environ.get('TMUX_TMPDIR') or '/tmp'}/tmux-{uid}"):
        if os.path.isdir(d):
            hide += ["--tmpfs", d]
    # agy reads whatever it is told to (web pages can carry injected instructions), and what it reads can
    # leave with the conversation: blank out credentials. Placed before the --bind entries below.
    home = Path.home()
    for rel in SECRET_PATHS:
        p = home / rel
        if p.is_dir():
            hide += ["--tmpfs", str(p)]
        elif p.is_file():
            hide += ["--ro-bind", "/dev/null", str(p)]
    # agy only writes under ~/.gemini/antigravity-cli (measured 2026-10-12); the rest of ~/.gemini stays
    # read-only. Inside it, re-protect what a later unsandboxed agy run would load or execute.
    state = home / ".gemini" / "antigravity-cli"
    protect = []
    for rel in STATE_READONLY:
        p = str(state / rel)
        if not os.path.exists(p):
            # a missing entry would be creatable inside the writable state dir and loaded by a later
            # unsandboxed agy run; refuse instead of running unprotected (R3)
            raise UnsupportedPlatform(f"{p} is missing; run agy once interactively so it exists, then retry")
        protect += ["--ro-bind", p, p]
    tmp = str(tmpdir)
    return ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", *hide,
            "--bind", str(state), str(state), *protect, "--bind", tmp, tmp, "--setenv", "TMPDIR", tmp,
            "--unsetenv", "TMUX", "--unsetenv", "TMUX_PANE", "--die-with-parent", "--", *argv]
