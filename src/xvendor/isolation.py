# Read-only isolation for vendor CLIs. agy and codex share one bubblewrap layout (_bwrap): read-only root,
# writable own state dir with config-like entries re-protected, writable tmpdir, secrets and sockets hidden.
# codex's own inner sandbox (-s read-only) stays as a second layer. codex runs unwrapped on non-Linux.
import os
import shutil
import sys
from pathlib import Path


class UnsupportedPlatform(Exception):
    pass


# Home-relative credential paths hidden from the sandboxed vendor: SSH and GPG keys, cloud, Kubernetes and GitHub CLI
# logins, git / npm / Postgres / netrc credentials, and the Claude Code and codex login tokens.
SECRET_PATHS = (".ssh", ".aws", ".gnupg", ".config/gh", ".kube", ".git-credentials", ".npmrc", ".pgpass",
                ".netrc", ".claude/.credentials.json",
                ".codex/auth.json",
                # agy login token and conversation store; agy itself keeps them via own_state
                ".gemini/antigravity-cli/antigravity-oauth-token", ".gemini/antigravity-cli/conversations",
                # Gemini OAuth tokens and account list
                ".gemini/oauth_creds.json", ".gemini/google_accounts.json",
                # browser profiles (cookies, saved logins)
                ".mozilla", ".config/google-chrome", ".config/chromium", ".config/BraveSoftware",
                # Bitwarden CLI session / vault data
                ".config/Bitwarden CLI",
                # gcloud credentials, Docker registry logins, pass(1) password store
                ".config/gcloud", ".docker/config.json", ".password-store",
                # Claude Code account state and conversation transcripts
                ".claude.json", ".claude/projects",
                # codex conversation transcripts and prompt history
                ".codex/sessions", ".codex/history.jsonl")

# Home-relative glob patterns for per-project secret files: .env, .env.* and .envrc.
SECRET_GLOBS = ("projects/*/.env", "projects/*/.env.*", "projects/*/*/.env", "projects/*/.envrc")

# Under ~/.gemini/antigravity-cli: config (hooks / MCP), the agentapi launcher script, built-in skills.
STATE_READONLY = ("settings.json", "bin", "builtin")

# Under ~/.codex (or $CODEX_HOME): config, instructions and hook / rule / skill / plugin / agent dirs that a
# later unsandboxed codex run would load. *.config.toml profile files are added at wrap time.
CODEX_STATE_READONLY = ("config.toml", "AGENTS.md", "hooks", "rules", "skills", "plugins", "agents")
CODEX_STATE_REQUIRED = ("config.toml", "hooks")


def check(vendor: str) -> None:
    """Raise UnsupportedPlatform when vendor cannot be run read-only here (R3: refuse, never degrade)."""
    if vendor not in ("codex", "agy"):
        raise UnsupportedPlatform(f"no isolation defined for vendor {vendor!r}")
    if vendor == "codex" and sys.platform != "linux":
        return
    if sys.platform != "linux":
        raise UnsupportedPlatform(f"{vendor} needs bubblewrap for read-only isolation; not available on {sys.platform}")
    if shutil.which("bwrap") is None:
        raise UnsupportedPlatform(f"{vendor} needs bubblewrap (bwrap) for read-only isolation; bwrap not found")


def _secret_items(home: Path, own_state: Path | None) -> list[tuple[Path, list[str]]]:
    """(path, bwrap args) masking SECRET_PATHS / SECRET_GLOBS under home, skipping anything inside own_state."""
    out = []
    paths = [home / rel for rel in SECRET_PATHS]
    for pattern in SECRET_GLOBS:
        paths += [p for p in home.glob(pattern) if p.is_file()]
    for p in paths:
        if own_state is not None and p.is_relative_to(own_state):
            continue
        if p.is_dir():
            out.append((p, ["--tmpfs", str(p)]))
        elif p.is_file():
            out.append((p, ["--ro-bind", "/dev/null", str(p)]))
    return out


def _hide_secrets(home: Path, own_state: Path | None) -> list[str]:
    """bwrap args masking SECRET_PATHS / SECRET_GLOBS under home, skipping anything inside own_state."""
    return [a for _, args in _secret_items(home, own_state) for a in args]


def writable_overlaps_hidden(home: Path, writable: Path, own_state: Path) -> str | None:
    """The path that writable (already resolved) equals or sits under among own_state and the paths xv hides
    from the vendor, or None."""
    if writable.is_relative_to(own_state):
        return str(own_state)
    for rel in SECRET_PATHS:
        if writable.is_relative_to(home / rel):
            return str(home / rel)
    for pattern in SECRET_GLOBS:
        for p in home.glob(pattern):
            if writable.is_relative_to(p):
                return str(p)
    return None


def state_dir(vendor: str) -> Path:
    """The vendor's own writable state dir."""
    if vendor == "codex":
        return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return Path.home() / ".gemini" / "antigravity-cli"


def wrap(vendor: str, argv: list[str], tmpdir: Path, writable: Path | None = None) -> list[str]:
    """Return argv wrapped so the vendor CLI cannot write outside its own state dirs (spec R3),
    plus writable when given (--write mode)."""
    check(vendor)
    if vendor == "codex":
        if sys.platform != "linux":
            print(f"xv: codex runs without xv isolation on {sys.platform}", file=sys.stderr)
            return list(argv)
        state = state_dir("codex")
        protect = list(CODEX_STATE_READONLY)
        extra = sorted(p.name for p in state.glob("*.config.toml")) if state.is_dir() else []
        return _bwrap("codex", argv, tmpdir, state, protect + extra, CODEX_STATE_REQUIRED, state, writable)
    state = state_dir("agy")
    return _bwrap("agy", argv, tmpdir, state, STATE_READONLY, STATE_READONLY, state, writable)


def linked_worktree_gitdir(writable: Path) -> Path:
    """The gitdir of the linked worktree at writable (its index lives there). Raise ValueError unless writable/.git
    is a pointer file to <repo>/.git/worktrees/<name> whose commondir leads back to <repo>/.git."""
    def bad(why: str) -> ValueError:
        return ValueError(f"--write needs a linked worktree (git worktree add): {why}")
    dotgit = writable / ".git"
    if not dotgit.is_file():
        raise bad(f"{dotgit} is not a worktree pointer")
    text = dotgit.read_text(encoding="utf-8", errors="replace").strip()
    if not text.startswith("gitdir:"):
        raise bad(f"{dotgit} has no gitdir line")
    gitdir = (writable / text.removeprefix("gitdir:").strip()).resolve()
    if not gitdir.is_dir():
        raise bad(f"{dotgit} points at {gitdir}, which is not a directory")
    if gitdir.parent.name != "worktrees" or gitdir.parent.parent.name != ".git":
        raise bad(f"{gitdir} is not under <repo>/.git/worktrees/")
    commondir = gitdir / "commondir"
    if not commondir.is_file():
        raise bad(f"{commondir} is missing")
    if (gitdir / commondir.read_text(encoding="utf-8", errors="replace").strip()).resolve() != gitdir.parent.parent:
        raise bad(f"{commondir} does not lead back to {gitdir.parent.parent}")
    return gitdir


def _bwrap(vendor: str, argv: list[str], tmpdir: Path, state: Path, readonly_entries, required_entries,
           own_state: Path, writable: Path | None = None) -> list[str]:
    """Shared bwrap line: read-only root, hidden sockets and secrets, writable state dir with its
    config-like entries re-protected, writable tmpdir. Layout measured for agy 2026-10-12."""
    # A read-only mount does not stop connect() to path-based unix sockets, so hide the whole of /run:
    # docker.sock, the system and user D-Bus, systemd --user and the like all live there. Same for the X11
    # socket dir and the tmux socket dir, which sit under /tmp.
    uid = os.getuid()
    hide = []
    if os.path.isdir("/run"):
        hide += ["--tmpfs", "/run"]
        # /etc/resolv.conf may be a symlink into /run (systemd-resolved); re-expose just that file so DNS
        # still works. bwrap mounts in order, so this bind lands on top of the tmpfs above.
        real = os.path.realpath("/etc/resolv.conf")
        if real.startswith("/run/") and os.path.isfile(real):
            hide += ["--ro-bind", real, real]
    if os.path.isdir("/tmp/.X11-unix"):
        hide += ["--tmpfs", "/tmp/.X11-unix"]
    d = f"{os.environ.get('TMUX_TMPDIR') or '/tmp'}/tmux-{uid}"
    if os.path.isdir(d):
        hide += ["--tmpfs", d]
    # the vendor reads whatever it is told to (web pages can carry injected instructions), and what it
    # reads can leave with the conversation: blank out credentials. Placed before the --bind entries below.
    # secrets that sit inside the writable dir (a worktree under ~/projects/<x>/) are masked after the --bind below
    hide_after = []
    for path, args in _secret_items(Path.home(), own_state):
        if writable is not None and path.is_relative_to(writable):
            hide_after += args
        else:
            hide += args
    # Inside the writable state dir, re-protect what a later unsandboxed run would load or execute.
    protect = []
    for rel in readonly_entries:
        p = str(state / rel)
        if not os.path.exists(p):
            if rel in required_entries:
                # a missing entry would be creatable inside the writable state dir and loaded by a later
                # unsandboxed run; refuse instead of running unprotected (R3)
                raise UnsupportedPlatform(f"{p} is missing; create it (or run {vendor} once) so it exists, then retry")
            continue
        protect += ["--ro-bind", p, p]
    tmp = str(tmpdir)
    write = []
    if writable is not None:
        # F2: the vendor must not rewrite the .git pointer / commondir to aim the worktree at a git dir of its own
        # (core.fsmonitor and friends would then run on the host's next git command), so those stay read-only
        # inside the writable dirs; the --ro-bind entries have to come after both --bind entries.
        gitdir = linked_worktree_gitdir(writable)
        for d in (writable, gitdir):
            write += ["--bind", str(d), str(d)]
        for f in (writable / ".git", gitdir / "commondir", gitdir / "gitdir", gitdir / "config.worktree"):
            if f.is_file():
                write += ["--ro-bind", str(f), str(f)]
        write += hide_after
    return ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", *hide,
            "--bind", str(state), str(state), *protect, *write, "--bind", tmp, tmp, "--setenv", "TMPDIR", tmp,
            "--unsetenv", "TMUX", "--unsetenv", "TMUX_PANE", "--die-with-parent", "--", *argv]
