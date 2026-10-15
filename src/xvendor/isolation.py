# Read-only isolation for vendor CLIs. agy and codex share one bubblewrap layout (_bwrap): read-only root,
# writable own state dir with config-like entries re-protected, writable tmpdir, secrets and sockets hidden.
# codex's own inner sandbox (-s read-only) stays as a second layer. --unshare-pid gives the vendor its own pid
# namespace: leftover background processes die with bwrap and host pids (/proc/<pid>/root) are invisible.
# On macOS agy gets the same layout as a Seatbelt profile run by sandbox-exec (_seatbelt). codex runs unwrapped off
# Linux: on macOS its own -s read-only is itself a Seatbelt sandbox, and Seatbelt cannot be applied twice.
import os
import shutil
import subprocess
import sys
from pathlib import Path


class UnsupportedPlatform(Exception):
    pass


# Home-relative credential paths hidden from the sandboxed vendor: SSH and GPG keys, cloud, Kubernetes and GitHub CLI
# logins, git / npm / Postgres / netrc credentials, and the Claude Code and codex login tokens.
SECRET_PATHS = (
    ".ssh",
    ".aws",
    ".gnupg",
    ".config/gh",
    ".kube",
    ".git-credentials",
    ".npmrc",
    ".pgpass",
    ".netrc",
    ".claude/.credentials.json",
    ".codex/auth.json",
    # agy login token and conversation store; agy itself keeps them via own_state
    ".gemini/antigravity-cli/antigravity-oauth-token",
    ".gemini/antigravity-cli/conversations",
    # Gemini OAuth tokens and account list
    ".gemini/oauth_creds.json",
    ".gemini/google_accounts.json",
    # browser profiles (cookies, saved logins)
    ".mozilla",
    ".config/google-chrome",
    ".config/chromium",
    ".config/BraveSoftware",
    # Bitwarden CLI session / vault data
    ".config/Bitwarden CLI",
    # gcloud credentials, Docker registry logins, pass(1) password store
    ".config/gcloud",
    ".docker/config.json",
    ".password-store",
    # Claude Code account state and conversation transcripts
    ".claude.json",
    ".claude/projects",
    # codex conversation transcripts and prompt history
    ".codex/sessions",
    ".codex/history.jsonl",
    # macOS browser profiles and Bitwarden CLI data
    "Library/Application Support/Google/Chrome",
    "Library/Application Support/Chromium",
    "Library/Application Support/BraveSoftware",
    "Library/Application Support/Firefox",
    "Library/Cookies",
    "Library/Application Support/Bitwarden CLI",
)

# Home-relative glob patterns for per-project secret files: .env, .env.* and .envrc.
SECRET_GLOBS = ("projects/*/.env", "projects/*/.env.*", "projects/*/*/.env", "projects/*/.envrc")

# Under ~/.gemini/antigravity-cli: config (hooks / MCP) and built-in skills stay read-only. bin/ (the agentapi
# launcher script) is a per-round throwaway copy: agy rewrites bin/agentapi before every run_command, so a
# read-only bin fails every shell command, while the host's bin/ must not be changed (a later unsandboxed run loads it).
STATE_READONLY = ("settings.json", "builtin")
STATE_SHADOW = ("bin",)

# Under ~/.codex (or $CODEX_HOME): config, instructions and hook / rule / skill / plugin / agent dirs that a
# later unsandboxed codex run would load. *.config.toml profile files are added at wrap time.
CODEX_STATE_READONLY = ("config.toml", "AGENTS.md", "hooks", "rules", "skills", "plugins", "agents")
CODEX_STATE_REQUIRED = ("config.toml", "hooks")

# Only the system copy: a sandbox-exec earlier on PATH could be anything.
SANDBOX_EXEC = "/usr/bin/sandbox-exec"

# Programs that hand work to something outside the sandbox (LaunchServices, AppleEvents, launchd) or read the
# screen or clipboard. Seatbelt cannot stop the services themselves: denying their mach-lookup or appleevent-send
# left `open` and `osascript` working (measured on macOS 26.5, 2026-10-15). Denying the programs is friction against
# copy-paste payloads only; a program calling the same APIs directly is not stopped.
EXEC_DENY = (
    "/usr/bin/open",
    "/usr/bin/osascript",
    "/usr/bin/osacompile",
    "/usr/bin/automator",
    "/usr/bin/shortcuts",
    "/bin/launchctl",
    "/usr/sbin/screencapture",
    "/usr/bin/pbpaste",
    "/usr/bin/pbcopy",
)
# The one unix socket outside the writable dirs a vendor needs: DNS resolution goes through mDNSResponder.
UNIX_SOCKET_ALLOW = ("/private/var/run/mDNSResponder",)


def check(vendor: str) -> None:
    """Raise UnsupportedPlatform when vendor cannot be run read-only here (R3: refuse, never degrade)."""
    if vendor not in ("codex", "agy"):
        raise UnsupportedPlatform(f"no isolation defined for vendor {vendor!r}")
    if vendor == "codex" and sys.platform != "linux":
        return
    if sys.platform == "darwin":
        if not os.path.exists(SANDBOX_EXEC):
            raise UnsupportedPlatform(f"{vendor} needs {SANDBOX_EXEC} for read-only isolation; not found")
        return
    if sys.platform != "linux":
        raise UnsupportedPlatform(f"{vendor} needs bubblewrap for read-only isolation; not available on {sys.platform}")
    if shutil.which("bwrap") is None:
        raise UnsupportedPlatform(f"{vendor} needs bubblewrap (bwrap) for read-only isolation; bwrap not found")


def _secret_items(home: Path, own_state: Path | None) -> list[tuple[Path, list[str]]]:
    """(path, bwrap args) masking SECRET_PATHS / SECRET_GLOBS under home, skipping anything inside own_state."""
    out = []
    home = home.resolve()
    if own_state is not None:
        own_state = own_state.resolve()
    paths = [home / rel for rel in SECRET_PATHS]
    for pattern in SECRET_GLOBS:
        paths += [p for p in home.glob(pattern) if p.is_file()]
    for p in paths:
        p = p.resolve()  # a symlinked dir must mask the real path the vendor can also reach
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
    state = own_state.resolve()
    if writable.is_relative_to(state) or state.is_relative_to(writable):
        return str(own_state)
    return _under_hidden(home, writable)


def _under_hidden(home: Path, path: Path) -> str | None:
    """The SECRET_PATHS / SECRET_GLOBS entry that path (already resolved) equals or sits under, or None."""
    home = home.resolve()
    for rel in SECRET_PATHS:
        if path.is_relative_to((home / rel).resolve()):
            return str(home / rel)
    for pattern in SECRET_GLOBS:
        for p in home.glob(pattern):
            if path.is_relative_to(p.resolve()):
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
    if sys.platform == "darwin":
        return _seatbelt(
            "agy",
            argv,
            tmpdir,
            state,
            STATE_READONLY + STATE_SHADOW,
            STATE_READONLY + STATE_SHADOW,
            writable,
            fake_home=True,
        )
    return _bwrap(
        "agy",
        argv,
        tmpdir,
        state,
        STATE_READONLY,
        STATE_READONLY + STATE_SHADOW,
        state,
        writable,
        shadow_entries=STATE_SHADOW,
    )


def _walk_symlinks_outside(start: Path, forbidden: Path) -> None:
    """Resolve start component by component (like realpath); raise ValueError if any symlink met on the way
    lives inside forbidden (resolved), because the vendor could re-point it."""
    parts = list(Path(os.path.abspath(start)).parts)  # ('/', 'a', 'b', ...)
    cur = Path(parts.pop(0))
    hops = 0
    while parts:
        part = parts.pop(0)
        if part == ".":
            continue
        if part == "..":
            cur = cur.parent  # cur is always a physical path here, so .parent is the real parent
            if Path(os.path.realpath(cur)).is_relative_to(forbidden):
                raise ValueError(f"{cur} lies inside {forbidden} on the gitdir path")
            continue
        cur = cur / part
        if Path(os.path.realpath(cur)).is_relative_to(forbidden):
            raise ValueError(f"{cur} lies inside {forbidden} on the gitdir path")  # real dir or symlink alike
        while cur.is_symlink():
            hops += 1
            if hops > 40:
                raise ValueError(f"too many symlinks resolving {start}")
            if Path(os.path.realpath(cur.parent)).is_relative_to(forbidden):
                raise ValueError(f"{cur} is a symlink inside {forbidden} on the gitdir path")
            target = os.readlink(cur)
            if os.path.isabs(target):
                parts = list(Path(target).parts)[1:] + parts
                cur = Path("/")
            else:
                parts = list(Path(target).parts) + parts
                cur = cur.parent
            break  # the pushed-back parts are consumed by the outer loop


def linked_worktree_gitdir(writable: Path, own_state: Path | None = None) -> Path:
    """The gitdir of the linked worktree at writable (its index lives there). Raise ValueError unless writable/.git
    is a pointer file to <repo>/.git/worktrees/<name> whose commondir leads back to <repo>/.git."""

    def bad(why: str) -> ValueError:
        return ValueError(f"--write needs a linked worktree (git worktree add): {why}")

    dotgit = writable / ".git"
    if dotgit.is_symlink():
        raise bad(f"{dotgit} is a symlink")
    if not dotgit.is_file():
        raise bad(f"{dotgit} is not a worktree pointer")
    text = dotgit.read_text(encoding="utf-8", errors="replace").strip()
    if not text.startswith("gitdir:"):
        raise bad(f"{dotgit} has no gitdir line")
    ptext = text.removeprefix("gitdir:").strip()
    parts = list(Path(ptext).parts)
    while parts and parts[0] == "..":
        parts.pop(0)
    if ".." in parts:
        raise bad(f"{dotgit} has a '..' after a real path component")
    raw = Path(os.path.join(writable, ptext))
    try:
        _walk_symlinks_outside(raw, writable.resolve())
    except ValueError as e:
        raise bad(str(e)) from None
    gitdir = raw.resolve()
    if not gitdir.is_dir():
        raise bad(f"{dotgit} points at {gitdir}, which is not a directory")
    if gitdir.parent.name != "worktrees" or gitdir.parent.parent.name != ".git":
        raise bad(f"{gitdir} is not under <repo>/.git/worktrees/")
    commondir = gitdir / "commondir"
    if not commondir.is_file():
        raise bad(f"{commondir} is missing")
    if (gitdir / commondir.read_text(encoding="utf-8", errors="replace").strip()).resolve() != gitdir.parent.parent:
        raise bad(f"{commondir} does not lead back to {gitdir.parent.parent}")
    common = gitdir.parent.parent
    if common.is_relative_to(writable.resolve()):
        raise bad(f"{common} lies inside {writable}: the repository itself would become writable")
    if own_state is not None and common.is_relative_to(own_state.resolve()):
        raise bad(f"{common} lies inside the vendor state dir {own_state}, which is writable for the vendor")
    if own_state is not None and own_state.resolve().is_relative_to(common):
        raise bad(
            f"the vendor state dir {own_state} lies inside {common}, which would make the repository metadata writable"
        )
    if hit := _under_hidden(Path.home(), common):
        raise bad(f"{common} lies inside {hit}, which xv hides from the vendor")
    return gitdir


def has_gitlinks(writable: Path) -> bool:
    """True when the index of writable has submodule entries (mode 160000). Raise ValueError if git fails."""
    ls = subprocess.run(["git", "-C", str(writable), "ls-files", "-s"], capture_output=True, text=True)
    if ls.returncode != 0:
        first = ls.stderr.strip().splitlines()[:1]
        raise ValueError(f"git ls-files failed in {writable}: {first[0] if first else ''}")
    return any(line.startswith("160000 ") for line in ls.stdout.splitlines())


def nested_git(writable: Path) -> list[str]:
    """Every .git directory or file under writable other than writable/.git itself (sorted), plus directories
    that could not be entered. Does not descend into a .git directory and does not follow symlinks."""
    top = writable / ".git"
    found: list[str] = []
    errs: list[OSError] = []
    for root, dirs, files in os.walk(writable, followlinks=False, onerror=errs.append):
        for d in list(dirs):
            if d == ".git":
                dirs.remove(d)
                if Path(root) / d != top:
                    found.append(str(Path(root) / d))
        found += [str(Path(root) / f) for f in files if f == ".git" and Path(root) / f != top]
    found += [f"{e.filename} (could not be entered)" for e in errs if e.filename]
    return sorted(found)


def _bwrap(
    vendor: str,
    argv: list[str],
    tmpdir: Path,
    state: Path,
    readonly_entries,
    required_entries,
    own_state: Path,
    writable: Path | None = None,
    shadow_entries=(),
) -> list[str]:
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
    # Entries the vendor must be able to write but whose host copy must not change: bind a throwaway copy.
    for rel in shadow_entries:
        src = state / rel
        if not os.path.exists(src):
            if rel in required_entries:
                raise UnsupportedPlatform(
                    f"{src} is missing; create it (or run {vendor} once) so it exists, then retry"
                )
            continue
        dst = tmpdir / "shadow" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(dst, ignore_errors=True)  # a tmpdir reused by a second wrap() starts from a fresh copy
        shutil.copytree(src, dst, symlinks=True)
        protect += ["--bind", str(dst), str(src)]
    tmp = str(tmpdir)
    write = []
    if writable is not None:
        # The whole gitdir stays read-only (part of the ro root): index, HEAD and config.worktree cannot change, so
        # the vendor cannot add (gitlinks included), commit, switch branches or plant config the host's git would
        # read. DIR/.git is ro-bound back after the --bind of DIR.
        linked_worktree_gitdir(writable, own_state)  # validate only
        write += ["--bind", str(writable), str(writable), "--ro-bind", str(writable / ".git"), str(writable / ".git")]
        write += hide_after
    return [
        "bwrap",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        *hide,
        "--bind",
        str(state),
        str(state),
        *protect,
        *write,
        "--bind",
        tmp,
        tmp,
        "--setenv",
        "TMPDIR",
        tmp,
        "--unsetenv",
        "TMUX",
        "--unsetenv",
        "TMUX_PANE",
        "--unshare-pid",
        "--die-with-parent",
        "--",
        *argv,
    ]


def _fake_home(tmpdir: Path, state: Path) -> Path:
    """Per-round HOME for agy on macOS, standing in for bwrap's bind mounts (agy has no flag for its state dir):
    each entry of the real ~/.gemini and of the state dir is a symlink back to the real path, and the profile
    decides what may be written there; bin/ is a throwaway copy instead. Library/Keychains points at the real
    one so agy still finds its login token (the Security framework looks it up under $HOME)."""
    home = tmpdir / "home"
    shutil.rmtree(home, ignore_errors=True)  # a tmpdir reused by a second wrap() starts fresh
    fstate = home / state.relative_to(Path.home())
    fstate.mkdir(parents=True)
    for e in state.parent.iterdir():
        if e.name != state.name:
            (fstate.parent / e.name).symlink_to(e)
    for e in state.iterdir():
        if e.name in STATE_SHADOW:
            shutil.copytree(e, fstate / e.name, symlinks=True)
        else:
            (fstate / e.name).symlink_to(e)
    (home / "Library").mkdir()
    (home / "Library" / "Keychains").symlink_to(Path.home() / "Library" / "Keychains")
    return home


def _seatbelt(
    vendor: str,
    argv: list[str],
    tmpdir: Path,
    state: Path,
    readonly_entries,
    required_entries,
    writable: Path | None = None,
    fake_home: bool = False,
) -> list[str]:
    """sandbox-exec line for macOS, the _bwrap layout as a Seatbelt profile. Paths go in as -D parameters, so the
    profile text needs no quoting, and are resolved first because Seatbelt matches the resolved path."""
    protect = []
    for rel in readonly_entries:
        p = state / rel
        if not os.path.exists(p):
            if rel in required_entries:
                raise UnsupportedPlatform(f"{p} is missing; create it (or run {vendor} once) so it exists, then retry")
            continue
        protect.append(p)
    allow = [state, tmpdir]
    if writable is not None:
        linked_worktree_gitdir(writable, state)  # validate only; the gitdir is outside every allowed path
        allow.append(writable)
        protect.append(writable / ".git")
    hide = [p for p, _ in _secret_items(Path.home(), state)]
    env = ["/usr/bin/env", "-u", "TMUX", "-u", "TMUX_PANE", f"TMPDIR={tmpdir}"]
    if fake_home:
        env.append(f"HOME={_fake_home(tmpdir, state)}")

    params: list[str] = []

    def refs(key: str, paths, form: str = "{}") -> str:
        out = []
        for i, p in enumerate(paths):
            params.extend(["-D", f"{key}{i}={os.path.realpath(p)}"])
            out.append(form.format(f'(subpath (param "{key}{i}"))'))
        return " ".join(out)

    # Later rules win: writes are denied, then allowed under the writable dirs, then denied again for the
    # protected entries inside them.
    rules = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        f'(allow file-write* (subpath "/dev") {refs("W", allow)})',
    ]
    if protect:
        rules.append(f"(deny file-write* {refs('R', protect)})")
    if hide:
        rules.append(f"(deny file-read* file-write* {refs('S', hide)})")
    # A read-only file system does not stop connect() to a unix socket (docker, tmux, ssh-agent): deny them all
    # but DNS and the writable dirs.
    sockets = " ".join(f'(remote unix-socket (subpath "{s}"))' for s in UNIX_SOCKET_ALLOW)
    rules += [
        "(deny network-outbound (remote unix-socket))",
        f"(allow network-outbound {sockets} {refs('U', allow, '(remote unix-socket {})')})",
        "(deny process-exec " + " ".join(f'(literal "{x}")' for x in EXEC_DENY) + ")",
        # no pid namespace here: at least keep the vendor away from processes outside its sandbox
        "(deny signal)",
        "(allow signal (target same-sandbox))",
        "(deny process-info*)",
        "(allow process-info* (target same-sandbox))",
    ]
    return [SANDBOX_EXEC, "-p", "\n".join(rules), *params, "--", *env, *argv]
