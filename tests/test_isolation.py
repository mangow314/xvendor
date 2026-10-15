import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from xvendor import isolation
from xvendor.isolation import UnsupportedPlatform, wrap

needs_bwrap = pytest.mark.skipif(sys.platform != "linux" or shutil.which("bwrap") is None, reason="needs bwrap")


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_codex_passthrough_off_linux(tmp_path, monkeypatch, capsys, platform):
    monkeypatch.setattr(isolation.sys, "platform", platform)
    argv = ["codex", "exec", "-s", "read-only", "hi"]
    assert wrap("codex", argv, tmp_path) == argv
    assert "runs without xv isolation" in capsys.readouterr().err


def fake_codex_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    state = home / ".codex"
    (state / "hooks").mkdir(parents=True)
    (state / "config.toml").write_text("")
    (state / "local.config.toml").write_text("")
    (state / "auth.json").write_text("TOKEN")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(isolation.sys, "platform", "linux")
    monkeypatch.setattr(isolation.shutil, "which", lambda name: "/usr/bin/bwrap")
    return home, state


def test_codex_argv_structure(tmp_path, monkeypatch):
    home, state = fake_codex_home(tmp_path, monkeypatch)
    (home / ".gemini").mkdir()
    (home / ".gemini" / "oauth_creds.json").write_text("{}")
    st = str(state)
    argv = wrap("codex", ["codex", "exec", "hi"], tmp_path / "t")

    def has(*seq):
        return any(argv[i : i + len(seq)] == list(seq) for i in range(len(argv)))

    assert argv[0] == "bwrap"
    assert has("--bind", st, st)
    assert has("--ro-bind", f"{st}/config.toml", f"{st}/config.toml")
    assert has("--ro-bind", f"{st}/hooks", f"{st}/hooks")
    assert has("--ro-bind", f"{st}/local.config.toml", f"{st}/local.config.toml")
    assert has("--tmpfs", "/run")
    assert not has("--ro-bind", "/dev/null", f"{st}/auth.json")
    assert has("--ro-bind", "/dev/null", str(home / ".gemini" / "oauth_creds.json"))
    assert argv[-4:] == ["--", "codex", "exec", "hi"]


def test_codex_refused_when_required_missing(tmp_path, monkeypatch):
    home, state = fake_codex_home(tmp_path, monkeypatch)
    (state / "hooks").rmdir()
    with pytest.raises(UnsupportedPlatform, match="hooks"):
        wrap("codex", ["codex"], tmp_path)


def test_codex_refused_without_bwrap_on_linux(tmp_path, monkeypatch):
    fake_codex_home(tmp_path, monkeypatch)
    monkeypatch.setattr(isolation.shutil, "which", lambda name: None)
    with pytest.raises(UnsupportedPlatform, match="bwrap"):
        wrap("codex", ["codex"], tmp_path)


@needs_bwrap
def test_real_bwrap_codex_state(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    state = home / ".codex"
    (state / "hooks").mkdir(parents=True)
    (state / "sessions").mkdir()
    (state / "config.toml").write_text("")
    (state / "auth.json").write_text("TOKEN")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    tmp = tmp_path_factory.mktemp("codextmp")
    code = (
        "import os\n"
        "def w(p):\n"
        "    try: open(p, 'w').write('x'); return 'ok'\n"
        "    except OSError: return 'denied'\n"
        f"print(w({str(state / 'sessions/new')!r}))\n"
        f"print(w({str(state / 'config.toml')!r}))\n"
        f"print(w({str(state / 'hooks/x')!r}))\n"
        f"print(open({str(state / 'auth.json')!r}).read())\n"
        "print(os.listdir('/run'))"
    )
    proc = subprocess.run(wrap("codex", [sys.executable, "-c", code], tmp), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["ok", "denied", "denied", "TOKEN", "[]"]


@needs_bwrap
def test_agy_argv_structure(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".netrc").write_text("x")
    state = home / ".gemini" / "antigravity-cli"
    (state / "bin").mkdir(parents=True)
    (state / "bin" / "agentapi").write_text("#!/bin/sh\nexec x\n")
    (state / "builtin").mkdir()
    (state / "settings.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    uid = os.getuid()
    tmux_root = tmp_path / "tmuxroot"
    (tmux_root / f"tmux-{uid}").mkdir(parents=True)
    monkeypatch.setenv("TMUX_TMPDIR", str(tmux_root))
    tmp = tmp_path / "agytmp"
    st = str(state)
    monkeypatch.setattr(isolation.os.path, "realpath", lambda p, **kw: p)
    hide = ["--tmpfs", "/run"] if os.path.isdir("/run") else []
    if os.path.isdir("/tmp/.X11-unix"):
        hide += ["--tmpfs", "/tmp/.X11-unix"]
    assert wrap("agy", ["agy", "-p", "hi"], tmp) == [
        "bwrap",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        *hide,
        "--tmpfs",
        f"{tmux_root}/tmux-{uid}",
        "--tmpfs",
        str(home / ".ssh"),
        "--ro-bind",
        "/dev/null",
        str(home / ".netrc"),
        "--bind",
        st,
        st,
        "--ro-bind",
        f"{st}/settings.json",
        f"{st}/settings.json",
        "--ro-bind",
        f"{st}/builtin",
        f"{st}/builtin",
        "--bind",
        f"{tmp}/shadow/bin",
        f"{st}/bin",
        "--bind",
        str(tmp),
        str(tmp),
        "--setenv",
        "TMPDIR",
        str(tmp),
        "--unsetenv",
        "TMUX",
        "--unsetenv",
        "TMUX_PANE",
        "--unshare-pid",
        "--die-with-parent",
        "--",
        "agy",
        "-p",
        "hi",
    ]
    assert (tmp / "shadow" / "bin").is_dir()
    assert (tmp / "shadow" / "bin" / "agentapi").read_text() == (state / "bin" / "agentapi").read_text()


@needs_bwrap
def test_agy_x11_tmpfs_follows_host_dir(tmp_path, monkeypatch):
    real_isdir = os.path.isdir
    monkeypatch.setattr(isolation.os.path, "isdir", lambda p: True if p == "/tmp/.X11-unix" else real_isdir(p))
    argv = wrap("agy", ["agy"], tmp_path)
    assert "/tmp/.X11-unix" in argv and argv[argv.index("/tmp/.X11-unix") - 1] == "--tmpfs"
    monkeypatch.setattr(isolation.os.path, "isdir", lambda p: False if p == "/tmp/.X11-unix" else real_isdir(p))
    assert "/tmp/.X11-unix" not in wrap("agy", ["agy"], tmp_path)


@needs_bwrap
def test_resolv_rebound_when_under_run(tmp_path, monkeypatch):
    real = "/run/systemd/resolve/stub-resolv.conf"
    real_realpath, real_isfile = os.path.realpath, os.path.isfile
    monkeypatch.setattr(
        isolation.os.path, "realpath", lambda p, **kw: real if p == "/etc/resolv.conf" else real_realpath(p, **kw)
    )
    monkeypatch.setattr(isolation.os.path, "isfile", lambda p: True if p == real else real_isfile(p))
    argv = wrap("agy", ["agy"], tmp_path)
    run_at = next(i for i in range(len(argv) - 1) if argv[i : i + 2] == ["--tmpfs", "/run"])
    bind_at = next(i for i in range(len(argv) - 2) if argv[i : i + 3] == ["--ro-bind", real, real])
    assert bind_at > run_at


@needs_bwrap
def test_agy_skips_missing_tmux_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path / "nowhere"))
    argv = wrap("agy", ["agy"], tmp_path)
    assert str(tmp_path / "nowhere") not in " ".join(argv)


def test_agy_rejected_on_win32(tmp_path, monkeypatch):
    monkeypatch.setattr(isolation.sys, "platform", "win32")
    with pytest.raises(UnsupportedPlatform, match="win32"):
        wrap("agy", ["agy"], tmp_path)


def on_darwin(tmp_path, monkeypatch):
    monkeypatch.setattr(isolation.sys, "platform", "darwin")
    sbx = tmp_path / "sandbox-exec"
    sbx.write_text("")
    monkeypatch.setattr(isolation, "SANDBOX_EXEC", str(sbx))
    return str(sbx)


def seatbelt_params(argv):
    """-D KEY=path pairs of a sandbox-exec line, as {path: [keys]}."""
    out = {}
    for i, a in enumerate(argv[: argv.index("--")]):
        if a == "-D":
            key, path = argv[i + 1].split("=", 1)
            out.setdefault(path, []).append(key)
    return out


def fake_agy_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    state = home / ".gemini" / "antigravity-cli"
    (state / "bin").mkdir(parents=True)
    (state / "bin" / "agentapi").write_text("orig")
    (state / "builtin").mkdir()
    (state / "conversations").mkdir()
    (state / "settings.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    return home, state


def test_seatbelt_rejected_without_sandbox_exec(tmp_path, monkeypatch):
    monkeypatch.setattr(isolation.sys, "platform", "darwin")
    monkeypatch.setattr(isolation, "SANDBOX_EXEC", str(tmp_path / "nope"))
    with pytest.raises(UnsupportedPlatform, match="nope"):
        wrap("agy", ["agy"], tmp_path)


def test_seatbelt_agy_layout(tmp_path, monkeypatch):
    sbx = on_darwin(tmp_path, monkeypatch)
    home, state = fake_agy_home(tmp_path, monkeypatch)
    (home / ".ssh").mkdir()
    (home / ".gemini" / "oauth_creds.json").write_text("{}")
    tmp = tmp_path / "agytmp"
    tmp.mkdir()
    argv = wrap("agy", ["agy", "-p", "hi"], tmp)
    assert argv[:2] == [sbx, "-p"]
    profile = argv[2]
    params = seatbelt_params(argv)
    # writable: state + tmpdir; re-protected: settings.json, builtin, the host bin; hidden: secrets
    assert {"W0", "U0"} <= set(params[str(state)]) and {"W1", "U1"} <= set(params[str(tmp)])
    for name in ("settings.json", "builtin", "bin"):
        assert params[str(state / name)][0].startswith("R")
    assert params[str(home / ".ssh")][0].startswith("S")
    assert params[str(home / ".gemini" / "oauth_creds.json")][0].startswith("S")
    assert profile.index("(deny file-write*)") < profile.index("(allow file-write*") < profile.index('(param "R0")')
    assert "(deny network-outbound (remote unix-socket))" in profile
    assert '(deny process-exec (literal "/usr/bin/open")' in profile
    fake = tmp / "home"
    assert argv[argv.index("--") + 1 :] == [
        "/usr/bin/env",
        "-u",
        "TMUX",
        "-u",
        "TMUX_PANE",
        f"TMPDIR={tmp}",
        f"HOME={fake}",
        "agy",
        "-p",
        "hi",
    ]
    fstate = fake / ".gemini" / "antigravity-cli"
    # bin is a copy, everything else points back at the real path, and the login keychain is reachable
    assert not (fstate / "bin").is_symlink() and (fstate / "bin" / "agentapi").read_text() == "orig"
    assert (fstate / "settings.json").readlink() == state / "settings.json"
    assert (fstate / "conversations").readlink() == state / "conversations"
    assert (fake / ".gemini" / "oauth_creds.json").readlink() == home / ".gemini" / "oauth_creds.json"
    assert (fake / "Library" / "Keychains").readlink() == home / "Library" / "Keychains"
    wrap("agy", ["agy"], tmp)  # a second wrap in the same tmpdir rebuilds the fake home


def test_seatbelt_agy_refused_when_bin_missing(tmp_path, monkeypatch):
    on_darwin(tmp_path, monkeypatch)
    state = tmp_path / "home" / ".gemini" / "antigravity-cli"
    (state / "builtin").mkdir(parents=True)
    (state / "settings.json").write_text("{}")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    with pytest.raises(UnsupportedPlatform, match="bin"):
        wrap("agy", ["agy"], tmp_path / "t")


def test_agy_rejected_without_bwrap(tmp_path, monkeypatch):
    monkeypatch.setattr(isolation.sys, "platform", "linux")
    monkeypatch.setattr(isolation.shutil, "which", lambda name: None)
    with pytest.raises(UnsupportedPlatform, match="bwrap"):
        wrap("agy", ["agy"], tmp_path)


def test_unknown_vendor_rejected(tmp_path):
    with pytest.raises(UnsupportedPlatform):
        wrap("gpt", ["gpt"], tmp_path)


def probe(path):
    return [sys.executable, "-c", f"open({str(path)!r}, 'w').write('x')"]


@needs_bwrap
def test_real_bwrap_blocks_writes_outside(tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    tmp = tmp_path_factory.mktemp("agytmp")
    target = outside / "xv-probe"
    proc = subprocess.run(wrap("agy", probe(target), tmp), capture_output=True, text=True, timeout=30)
    assert proc.returncode != 0
    assert not target.exists()

    inside = tmp / "xv-probe"
    proc = subprocess.run(wrap("agy", probe(inside), tmp), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert inside.read_text() == "x"


@needs_bwrap
def test_real_bwrap_hides_secrets(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_test").write_text("PRIVATE")
    (home / ".netrc").write_text("machine x password p")
    state = home / ".gemini" / "antigravity-cli"
    for d in ("bin", "builtin"):
        (state / d).mkdir(parents=True, exist_ok=True)
    (state / "settings.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    tmp = tmp_path_factory.mktemp("agytmp")
    # a file masked with /dev/null is mounted nodev by bwrap, so reading it fails instead of returning ""
    code = (
        f"import os; print(sorted(os.listdir({str(home / '.ssh')!r})))\n"
        f"try: print(repr(open({str(home / '.netrc')!r}).read()))\n"
        f"except PermissionError: print('denied')"
    )
    proc = subprocess.run(wrap("agy", [sys.executable, "-c", code], tmp), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines()[0] == "[]"
    assert proc.stdout.splitlines()[1] in ("''", "denied")
    assert (home / ".ssh" / "id_test").read_text() == "PRIVATE"  # host copy untouched


@needs_bwrap
def test_real_bwrap_hides_new_secrets(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    files = {
        "projects/demo/.env": "SECRET=1",
        ".mozilla/firefox/p/cookies.sqlite": "c",
        ".gemini/oauth_creds.json": "{tok}",
        ".codex/sessions/x.jsonl": "s",
    }
    for rel, body in files.items():
        f = home / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    state = home / ".gemini" / "antigravity-cli"
    for d in ("bin", "builtin"):
        (state / d).mkdir(parents=True, exist_ok=True)
    (state / "settings.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    tmp = tmp_path_factory.mktemp("agytmp")
    code = (
        "import os\n"
        "def rd(p):\n"
        "    try: return repr(open(p).read())\n"
        "    except PermissionError: return 'denied'\n"
        f"print(rd({str(home / 'projects/demo/.env')!r}))\n"
        f"print(rd({str(home / '.gemini/oauth_creds.json')!r}))\n"
        f"print(os.listdir({str(home / '.mozilla')!r}))\n"
        f"print(os.listdir({str(home / '.codex/sessions')!r}))"
    )
    proc = subprocess.run(wrap("agy", [sys.executable, "-c", code], tmp), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.splitlines()
    assert out[0] in ("''", "denied")
    assert out[1] in ("''", "denied")
    assert out[2] == "[]"
    assert out[3] == "[]"
    for rel, body in files.items():
        assert (home / rel).read_text() == body  # host copies untouched


def test_own_state_not_hidden(tmp_path):
    home = tmp_path
    own = home / ".gemini" / "antigravity-cli"
    (own / "sub").mkdir(parents=True)
    (home / ".codex").mkdir()
    (home / ".codex" / "auth.json").write_text("{}")
    args = isolation._hide_secrets(home, own_state=own)
    assert not any(a.startswith(str(own)) for a in args)
    assert str(home / ".codex" / "auth.json") in isolation._hide_secrets(home, own_state=None)


@needs_bwrap
def test_real_bwrap_hides_run_and_x11(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    state = home / ".gemini" / "antigravity-cli"
    for d in ("bin", "builtin"):
        (state / d).mkdir(parents=True, exist_ok=True)
    (state / "settings.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    tmp = tmp_path_factory.mktemp("agytmp")
    code = (
        "import os; print(os.listdir('/run'))\n"
        "print(os.listdir('/tmp/.X11-unix') if os.path.isdir('/tmp/.X11-unix') else [])\n"
        "print(len(open('/etc/resolv.conf').read()) >= 0)"
    )
    proc = subprocess.run(wrap("agy", [sys.executable, "-c", code], tmp), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["[]", "[]", "True"]


@needs_bwrap
def test_real_bwrap_gemini_narrowed(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    state = home / ".gemini" / "antigravity-cli"
    (state / "bin").mkdir(parents=True)
    (state / "bin" / "agentapi").write_text("orig")
    (state / "builtin").mkdir()
    (state / "settings.json").write_text("{}")
    (home / ".gemini" / "settings.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    tmp = tmp_path_factory.mktemp("agytmp")
    targets = {
        "state": state / "brain-x",
        "config": state / "settings.json",
        "bin": state / "bin" / "agentapi",
        "top": home / ".gemini" / "settings.json",
    }
    code = (
        "import sys\nfor name, path in " + repr([(k, str(v)) for k, v in targets.items()]) + ":\n"
        "    try: open(path, 'w').write('x'); print(name, 'ok')\n"
        "    except OSError: print(name, 'denied')"
    )
    proc = subprocess.run(wrap("agy", [sys.executable, "-c", code], tmp), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split("\n")[:4] == ["state ok", "config denied", "bin ok", "top denied"]
    assert (state / "settings.json").read_text() == "{}"
    assert (state / "bin" / "agentapi").read_text() == "orig"


@needs_bwrap
def test_real_bwrap_agy_bin_shadow_discarded(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    state = home / ".gemini" / "antigravity-cli"
    (state / "bin").mkdir(parents=True)
    (state / "bin" / "agentapi").write_text("orig")
    (state / "builtin").mkdir()
    (state / "settings.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    tmp = tmp_path_factory.mktemp("agytmp")
    path = str(state / "bin" / "agentapi")
    code = f"open({path!r}, 'w').write('changed')\nprint(open({path!r}).read())"
    proc = subprocess.run(wrap("agy", [sys.executable, "-c", code], tmp), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "changed"
    assert (state / "bin" / "agentapi").read_text() == "orig"
    assert (tmp / "shadow" / "bin" / "agentapi").read_text() == "changed"


@needs_bwrap
def test_agy_refused_when_protected_entry_missing(tmp_path, monkeypatch):
    state = tmp_path / ".gemini" / "antigravity-cli"
    (state / "bin").mkdir(parents=True)
    (state / "builtin").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(UnsupportedPlatform, match="settings.json"):
        wrap("agy", ["agy"], tmp_path)
    assert not (state / "settings.json").exists()
    state2 = tmp_path / "h2" / ".gemini" / "antigravity-cli"
    (state2 / "builtin").mkdir(parents=True)
    (state2 / "settings.json").write_text("{}")
    monkeypatch.setenv("HOME", str(tmp_path / "h2"))
    with pytest.raises(UnsupportedPlatform, match="bin"):
        wrap("agy", ["agy"], tmp_path)


def _agy_token_home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    (home / ".codex" / "hooks").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text("")
    state = home / ".gemini" / "antigravity-cli"
    (state / "bin").mkdir(parents=True)
    (state / "builtin").mkdir()
    (state / "conversations").mkdir()
    (state / "conversations" / "c1").write_text("x")
    (state / "settings.json").write_text("{}")
    (state / "antigravity-oauth-token").write_text("AGYTOKEN")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    code = (
        "import os\n"
        "try: print(open(" + repr(str(state / "antigravity-oauth-token")) + ").read() or 'EMPTY')\n"
        "except OSError: print('DENIED')\n"
        "print(sorted(os.listdir(" + repr(str(state / "conversations")) + ")))"
    )
    return code, tmp_path_factory.mktemp("vtmp")


@needs_bwrap
def test_real_bwrap_codex_cannot_read_agy_token(tmp_path_factory, monkeypatch):
    code, tmp = _agy_token_home(tmp_path_factory, monkeypatch)
    proc = subprocess.run(wrap("codex", [sys.executable, "-c", code], tmp), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines()[0] in ("EMPTY", "DENIED")
    assert proc.stdout.splitlines()[1] == "[]"


@needs_bwrap
def test_real_bwrap_agy_keeps_own_token(tmp_path_factory, monkeypatch):
    code, tmp = _agy_token_home(tmp_path_factory, monkeypatch)
    proc = subprocess.run(wrap("agy", [sys.executable, "-c", code], tmp), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["AGYTOKEN", "['c1']"]


# --write

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def make_worktree(base: Path, name="wt") -> tuple[Path, Path]:
    """A repo main/ with one commit and a linked worktree base/<name>; returns (worktree, its gitdir)."""
    main, wt = base / "main", base / name
    ident = ["-c", "user.name=t", "-c", "user.email=t@x"]
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    (main / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(main), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(main), *ident, "commit", "-q", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", f"../{name}", "-b", f"lane-{name}"], check=True)
    return wt, main / ".git" / "worktrees" / name


def seq_at(argv, *seq):
    return [i for i in range(len(argv)) if argv[i : i + len(seq)] == list(seq)]


@needs_git
def test_write_argv_binds_dir(tmp_path, monkeypatch):
    home, state = fake_codex_home(tmp_path, monkeypatch)
    wt, gitdir = make_worktree(tmp_path)
    argv = wrap("codex", ["codex", "exec", "hi"], tmp_path / "t", wt)
    at = seq_at(argv, "--bind", str(wt), str(wt))
    assert len(at) == 1
    assert at[0] > argv.index(str(state))
    assert at[0] < argv.index(str(tmp_path / "t"))
    assert wrap("codex", ["codex"], tmp_path / "t").count("--bind") == 2  # state + tmp only


@needs_git
def test_seatbelt_write_dir(tmp_path, monkeypatch):
    fake_agy_home(tmp_path, monkeypatch)
    on_darwin(tmp_path, monkeypatch)
    wt, gitdir = make_worktree(tmp_path)
    params = seatbelt_params(wrap("agy", ["agy"], tmp_path / "t", wt))
    assert "W2" in params[str(wt)]
    assert params[str(wt / ".git")][0].startswith("R")
    assert not any(Path(p) == gitdir or gitdir in Path(p).parents for p in params)


@needs_git
def test_write_argv_gitdir_readonly(tmp_path, monkeypatch):
    fake_codex_home(tmp_path, monkeypatch)
    wt, gitdir = make_worktree(tmp_path)
    argv = wrap("codex", ["codex"], tmp_path / "t", wt)
    binds = [argv[i + 1] for i, a in enumerate(argv) if a == "--bind"]
    assert not any(b == str(gitdir) or Path(b) in gitdir.parents or str(gitdir) in b for b in binds)
    ro = seq_at(argv, "--ro-bind", str(wt / ".git"), str(wt / ".git"))
    assert len(ro) == 1 and ro[0] > seq_at(argv, "--bind", str(wt), str(wt))[0]
    assert not seq_at(argv, "--ro-bind", str(gitdir / "commondir"), str(gitdir / "commondir"))
    print(argv)


@needs_git
def test_linked_worktree_gitdir_ok_and_refusals(tmp_path):
    wt, gitdir = make_worktree(tmp_path)
    assert isolation.linked_worktree_gitdir(wt) == gitdir.resolve()
    subprocess.run(
        ["git", "init", "-q", "--separate-git-dir", str(tmp_path / "sep.git"), str(tmp_path / "sepwt")], check=True
    )
    plain = tmp_path / "plain"
    plain.mkdir()
    for bad in (tmp_path / "sepwt", plain, tmp_path / "main"):
        with pytest.raises(ValueError, match="git worktree add"):
            isolation.linked_worktree_gitdir(bad)


def test_writable_overlaps_hidden(tmp_path):
    home = tmp_path / "home"
    own = home / ".codex"
    for d in (own / "x", home / ".ssh" / "y", home / "projects" / "demo" / "wt", home / "ok"):
        d.mkdir(parents=True)
    (home / "projects" / "demo" / ".env").write_text("S=1")
    assert isolation.writable_overlaps_hidden(home, own / "x", own) == str(own)
    assert isolation.writable_overlaps_hidden(home, home / ".ssh" / "y", own) == str(home / ".ssh")
    assert isolation.writable_overlaps_hidden(home, home / "projects" / "demo" / "wt", own) is None
    assert isolation.writable_overlaps_hidden(home, home / "ok", own) is None


@needs_bwrap
@needs_git
def test_real_bwrap_write_dir_only(tmp_path_factory):
    base = tmp_path_factory.mktemp("repo")
    work, _ = make_worktree(base)
    outside = tmp_path_factory.mktemp("outside")
    tmp = tmp_path_factory.mktemp("agytmp")
    proc = subprocess.run(wrap("agy", probe(work / "xv-probe"), tmp, work), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert (work / "xv-probe").read_text() == "x"
    proc = subprocess.run(
        wrap("agy", probe(outside / "xv-probe"), tmp, work), capture_output=True, text=True, timeout=30
    )
    assert proc.returncode != 0
    assert not (outside / "xv-probe").exists()


@needs_bwrap
@needs_git
def test_real_bwrap_worktree_status_ok_commit_denied(tmp_path_factory, monkeypatch):
    base = tmp_path_factory.mktemp("repo")
    wt, _ = make_worktree(base)
    main = base / "main"
    _, tmp = _agy_token_home(tmp_path_factory, monkeypatch)
    g = "git -c safe.directory='*'"
    script = (
        f"cd {base}\n"
        "echo hi > wt/hello.txt; echo write=$?\n"
        f"{g} -C wt status --porcelain > {tmp}/st.out 2>&1; echo status=$?; sed 's/^/ST:/' {tmp}/st.out\n"
        f"{g} -C wt diff --stat >/dev/null 2>&1; echo diff=$?\n"
        f"{g} -C wt add hello.txt >/dev/null 2>&1; echo add=$?\n"
        f"{g} -C wt -c user.name=t -c user.email=t@x commit -qam x >/dev/null 2>&1; echo commit=$?\n"
        "echo x >> main/a.txt 2>/dev/null; echo mainwrite=$?\n"
        "ls main/.git/hooks >/dev/null 2>&1; echo hooks=$?\n"
    )
    proc = subprocess.run(wrap("agy", ["sh", "-c", script], tmp, wt), capture_output=True, text=True, timeout=60)
    print(proc.stdout, proc.stderr)
    rc = dict(l.split("=", 1) for l in proc.stdout.splitlines() if "=" in l and not l.startswith("ST:"))
    assert rc["write"] == "0", proc.stdout
    assert rc["status"] == "0" and "ST:?? hello.txt" in proc.stdout, proc.stdout
    assert rc["diff"] == "0", proc.stdout
    assert rc["add"] != "0", proc.stdout
    assert rc["commit"] != "0", proc.stdout
    assert rc["mainwrite"] != "0", proc.stdout
    assert rc["hooks"] == "0", proc.stdout
    host = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True, check=True)
    assert "?? hello.txt" in host.stdout
    log = subprocess.run(["git", "-C", str(main), "log", "--oneline"], capture_output=True, text=True, check=True)
    assert len(log.stdout.splitlines()) == 1


@needs_bwrap
@needs_git
def test_real_bwrap_worktree_pointers_readonly(tmp_path_factory, monkeypatch):
    base = tmp_path_factory.mktemp("repo")
    wt, gitdir = make_worktree(base)
    _, tmp = _agy_token_home(tmp_path_factory, monkeypatch)
    pointer = (wt / ".git").read_text()
    commondir = (gitdir / "commondir").read_text()
    script = (
        f"echo x > {wt}/.git 2>/dev/null; echo dotgit=$?\n"
        f"echo x > {gitdir}/commondir 2>/dev/null; echo commondir=$?\n"
        f"touch {gitdir}/xv-index-probe 2>/dev/null; echo probe=$?\n"
        f"git -c safe.directory='*' -C {wt} status --porcelain >/dev/null 2>&1; echo status=$?\n"
    )
    proc = subprocess.run(wrap("agy", ["sh", "-c", script], tmp, wt), capture_output=True, text=True, timeout=60)
    rc = dict(l.split("=", 1) for l in proc.stdout.splitlines() if "=" in l)
    assert rc["dotgit"] != "0" and rc["commondir"] != "0", proc.stdout
    assert rc["probe"] != "0" and rc["status"] == "0", proc.stdout
    assert (wt / ".git").read_text() == pointer and (gitdir / "commondir").read_text() == commondir


@needs_bwrap
@needs_git
def test_real_bwrap_write_dir_keeps_env_hidden(tmp_path_factory, monkeypatch):
    _, tmp = _agy_token_home(tmp_path_factory, monkeypatch)
    home = Path(os.environ["HOME"])
    (home / "projects").mkdir()
    repo = home / "projects" / "demo"
    repo.mkdir()
    wt, _ = make_worktree(home / "projects", "demo-wt")
    (wt / ".env").write_text("SECRET=1")
    script = f"cat {wt}/.env 2>/dev/null; echo cat=$?\necho hi > {wt}/other.txt; echo write=$?\n"
    proc = subprocess.run(wrap("agy", ["sh", "-c", script], tmp, wt), capture_output=True, text=True, timeout=60)
    assert "SECRET" not in proc.stdout, proc.stdout
    assert "write=0" in proc.stdout, proc.stdout
    assert (wt / "other.txt").read_text() == "hi\n"
    assert (wt / ".env").read_text() == "SECRET=1"


@needs_bwrap
@needs_git
def test_real_bwrap_gitdir_readonly_status_ok(tmp_path_factory, monkeypatch):
    base = tmp_path_factory.mktemp("repo")
    wt, gitdir = make_worktree(base)
    _, tmp = _agy_token_home(tmp_path_factory, monkeypatch)
    g = "git -c safe.directory='*'"
    script = (
        f"cd {base}\n"
        "echo hi > wt/hello.txt; echo write=$?\n"
        f"{g} -C wt status --porcelain > {tmp}/st.out 2>&1; echo status=$?; sed 's/^/ST:/' {tmp}/st.out\n"
        f"{g} -C wt diff --stat >/dev/null 2>&1; echo diff=$?\n"
        f"{g} -C wt add hello.txt >/dev/null 2>&1; echo add=$?\n"
        f"{g} -C wt -c user.name=t -c user.email=t@x commit -qam x >/dev/null 2>&1; echo commit=$?\n"
        f"echo 'ref: refs/heads/main' > {gitdir}/HEAD 2>/dev/null; echo head=$?\n"
        f"touch {gitdir}/config.worktree 2>/dev/null; echo cfgwt=$?\n"
        f"cd wt && git init -q sub && git -C sub config core.fsmonitor 'touch {base}/MARK'; cd ..\n"
        f"{g} -C wt add sub >/dev/null 2>&1; echo addsub=$?\n"
    )
    proc = subprocess.run(wrap("agy", ["sh", "-c", script], tmp, wt), capture_output=True, text=True, timeout=60)
    print(proc.stdout, proc.stderr)
    rc = dict(l.split("=", 1) for l in proc.stdout.splitlines() if "=" in l and not l.startswith("ST:"))
    assert rc["write"] == "0", proc.stdout
    assert rc["status"] == "0" and "ST:?? hello.txt" in proc.stdout, proc.stdout
    assert rc["diff"] == "0", proc.stdout
    for k in ("add", "commit", "head", "cfgwt", "addsub"):
        assert rc[k] != "0", proc.stdout
    host = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True, check=True)
    assert "?? hello.txt" in host.stdout
    ls = subprocess.run(["git", "-C", str(wt), "ls-files"], capture_output=True, text=True, check=True)
    assert "sub" not in ls.stdout
    assert not (base / "MARK").exists() and not (wt / "MARK").exists() and not (wt / "sub" / "MARK").exists()


@needs_git
def test_linked_worktree_rejects_repo_inside_dir(tmp_path):
    wt, _ = make_worktree(tmp_path)
    shutil.move(str(tmp_path / "main"), str(wt / "main"))
    (wt / ".git").write_text("gitdir: main/.git/worktrees/wt\n")
    (wt / "main" / ".git" / "worktrees" / "wt" / "gitdir").write_text(f"{wt / '.git'}\n")
    with pytest.raises(ValueError, match="lies inside"):
        isolation.linked_worktree_gitdir(wt)


@needs_git
def test_linked_worktree_rejects_symlink_pointer(tmp_path):
    wt, _ = make_worktree(tmp_path)
    (wt / ".git").rename(wt / "pointer")
    os.symlink("pointer", wt / ".git")
    with pytest.raises(ValueError, match="symlink"):
        isolation.linked_worktree_gitdir(wt)


@needs_git
def test_hide_after_resolves_symlinked_home(tmp_path, monkeypatch):
    home, state = fake_codex_home(tmp_path, monkeypatch)
    real = home / "real"
    real.mkdir()
    os.symlink(real, home / "projects")
    (real / "demo").mkdir()
    wt, _ = make_worktree(real, "demo-wt")
    (wt / ".env").write_text("S=1")
    wt = (home / "projects" / "demo-wt").resolve()
    argv = wrap("codex", ["codex"], tmp_path / "t", wt)
    hide = seq_at(argv, "--ro-bind", "/dev/null", str(wt / ".env"))
    assert len(hide) == 1 and hide[0] > seq_at(argv, "--bind", str(wt), str(wt))[0]
    (real / "ssh" / "x").mkdir(parents=True)
    os.symlink(real / "ssh", home / ".ssh")
    assert isolation.writable_overlaps_hidden(home, (home / ".ssh" / "x").resolve(), state) is not None
    assert isolation.writable_overlaps_hidden(home, state.parent, state) is not None


def test_nested_git_listed(tmp_path):
    wt = tmp_path / "wt"
    (wt / ".git").mkdir(parents=True)
    (wt / "sub" / ".git" / "x" / ".git").mkdir(parents=True)
    (wt / "deep" / "mod").mkdir(parents=True)
    (wt / "deep" / "mod" / ".git").write_text("gitdir: elsewhere\n")
    assert isolation.nested_git(wt) == [str(wt / "deep" / "mod" / ".git"), str(wt / "sub" / ".git")]


@needs_git
def test_linked_worktree_rejects_symlink_in_pointer_path(tmp_path):
    wt, gitdir = make_worktree(tmp_path)
    original = (wt / ".git").read_text()
    os.symlink(gitdir.parent, wt / "link")
    (wt / ".git").write_text("gitdir: link/wt\n")
    with pytest.raises(ValueError, match="inside"):
        isolation.linked_worktree_gitdir(wt)
    (wt / ".git").write_text(original)
    assert isolation.linked_worktree_gitdir(wt) == gitdir.resolve()


@needs_git
def test_linked_worktree_rejects_repo_in_state(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    ident = ["-c", "user.name=t", "-c", "user.email=t@x"]
    repo = state / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), *ident, "commit", "-q", "-m", "init"], check=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt), "-b", "lane"], check=True)
    with pytest.raises(ValueError, match="vendor state dir"):
        isolation.linked_worktree_gitdir(wt, own_state=state)
    assert isolation.linked_worktree_gitdir(wt) is not None


@needs_git
def test_linked_worktree_rejects_state_inside_gitdir(tmp_path):
    wt, gitdir = make_worktree(tmp_path)
    with pytest.raises(ValueError, match="state dir"):
        isolation.linked_worktree_gitdir(wt, own_state=gitdir / "codexhome")


@needs_git
def test_linked_worktree_rejects_symlink_chain_into_dir(tmp_path):
    wt, gitdir = make_worktree(tmp_path)
    original = (wt / ".git").read_text()
    os.symlink(gitdir.parent, wt / "link")
    (tmp_path / "outside").mkdir()
    os.symlink(wt / "link", tmp_path / "outside" / "entry")
    (wt / ".git").write_text(f"gitdir: {tmp_path}/outside/entry/wt\n")
    with pytest.raises(ValueError, match="inside"):
        isolation.linked_worktree_gitdir(wt)
    os.symlink(gitdir.parent, tmp_path / "outside" / "direct")  # chain never passes through DIR
    (wt / ".git").write_text(f"gitdir: {tmp_path}/outside/direct/wt\n")
    assert isolation.linked_worktree_gitdir(wt) == gitdir.resolve()
    (wt / ".git").write_text(original)


@needs_git
def test_linked_worktree_rejects_symlink_in_expanded_target(tmp_path):
    wt, gitdir = make_worktree(tmp_path)
    original = (wt / ".git").read_text()
    os.symlink(tmp_path / "main" / ".git", wt / "link")
    (tmp_path / "outside").mkdir()
    os.symlink(wt / "link" / "worktrees", tmp_path / "outside" / "entry")
    (wt / ".git").write_text(f"gitdir: {tmp_path}/outside/entry/wt\n")
    with pytest.raises(ValueError, match="inside"):
        isolation.linked_worktree_gitdir(wt)
    (tmp_path / "outside" / "entry").unlink()
    os.symlink(tmp_path / "main" / ".git" / "worktrees", tmp_path / "outside" / "entry")  # not via DIR
    assert isolation.linked_worktree_gitdir(wt) == gitdir.resolve()
    (wt / ".git").write_text(original)


@needs_git
def test_linked_worktree_accepts_symlinked_projects_dir(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    wt, gitdir = make_worktree(real)
    os.symlink(real, tmp_path / "projects")
    assert (wt / ".git").read_text().startswith(f"gitdir: {real}/main/.git/worktrees/")
    assert isolation.linked_worktree_gitdir(wt) == gitdir.resolve()
    assert isolation.linked_worktree_gitdir(tmp_path / "projects" / "wt") == gitdir.resolve()


@needs_git
def test_linked_worktree_rejects_dotdot_after_component(tmp_path):
    wt, gitdir = make_worktree(tmp_path)
    (wt / "sub").mkdir()
    (wt / ".git").write_text("gitdir: sub/../../main/.git/worktrees/wt\n")
    with pytest.raises(ValueError, match="'..'"):
        isolation.linked_worktree_gitdir(wt)
    (wt / ".git").write_text("gitdir: ../main/.git/worktrees/wt\n")
    assert isolation.linked_worktree_gitdir(wt) == gitdir.resolve()


@needs_bwrap
@needs_git
def test_real_bwrap_unshare_pid_kills_stragglers(tmp_path_factory, monkeypatch):
    base = tmp_path_factory.mktemp("repo")
    wt, _ = make_worktree(base)
    _, tmp = _agy_token_home(tmp_path_factory, monkeypatch)
    script = f"setsid -f sh -c 'sleep 1; echo late > {wt}/late.txt' >/dev/null 2>&1; exit 0"
    argv = wrap("agy", ["sh", "-c", script], tmp, wt)
    subprocess.run(argv, capture_output=True, text=True, timeout=30)
    time.sleep(2)
    assert not (wt / "late.txt").exists()
    # control: without --unshare-pid the straggler survives and writes (the test can tell the difference)
    argv.remove("--unshare-pid")
    subprocess.run(argv, capture_output=True, text=True, timeout=30)
    time.sleep(2)
    assert (wt / "late.txt").exists()


@needs_git
def test_walk_symlinks_dotdot_then_relative_link(tmp_path):
    wt, gitdir = make_worktree(tmp_path)
    os.symlink(tmp_path / "main" / ".git", wt / "link")
    (tmp_path / "out" / "d").mkdir(parents=True)
    os.symlink("../wt/link/worktrees", tmp_path / "out" / "s")
    os.symlink("d/../s", tmp_path / "out" / "entry")
    (wt / ".git").write_text(f"gitdir: {tmp_path}/out/entry/wt\n")
    with pytest.raises(ValueError, match="inside"):
        isolation.linked_worktree_gitdir(wt)


@needs_git
def test_walk_refuses_real_dir_inside_writable(tmp_path):
    wt, gitdir = make_worktree(tmp_path)
    (wt / "d").mkdir()
    (tmp_path / "out").mkdir()
    os.symlink("../wt/d/../../main/.git/worktrees", tmp_path / "out" / "pent")
    (wt / ".git").write_text(f"gitdir: {tmp_path}/out/pent/wt\n")
    with pytest.raises(ValueError, match="inside"):
        isolation.linked_worktree_gitdir(wt)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can enter any directory")
def test_nested_git_marks_unreadable(tmp_path):
    wt = tmp_path / "wt"
    (wt / ".git").mkdir(parents=True)
    (wt / "ok" / ".git").mkdir(parents=True)
    (wt / "locked").mkdir()
    (wt / "locked").chmod(0o000)
    try:
        found = isolation.nested_git(wt)
    finally:
        (wt / "locked").chmod(0o755)
    assert f"{wt / 'locked'} (could not be entered)" in found
    assert str(wt / "ok" / ".git") in found
