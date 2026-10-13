import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from xvendor import isolation
from xvendor.isolation import UnsupportedPlatform, wrap

needs_bwrap = pytest.mark.skipif(sys.platform != "linux" or shutil.which("bwrap") is None, reason="needs bwrap")


def test_codex_passthrough(tmp_path):
    argv = ["codex", "exec", "-s", "read-only", "hi"]
    assert wrap("codex", argv, tmp_path) == argv


@needs_bwrap
def test_agy_argv_structure(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".netrc").write_text("x")
    state = home / ".gemini" / "antigravity-cli"
    (state / "bin").mkdir(parents=True)
    (state / "builtin").mkdir()
    (state / "settings.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    uid = os.getuid()
    tmux_root = tmp_path / "tmuxroot"
    (tmux_root / f"tmux-{uid}").mkdir(parents=True)
    monkeypatch.setenv("TMUX_TMPDIR", str(tmux_root))
    tmp = tmp_path / "agytmp"
    st = str(state)
    monkeypatch.setattr(isolation.os.path, "realpath", lambda p: p)
    hide = ["--tmpfs", "/run"] if os.path.isdir("/run") else []
    if os.path.isdir("/tmp/.X11-unix"):
        hide += ["--tmpfs", "/tmp/.X11-unix"]
    assert wrap("agy", ["agy", "-p", "hi"], tmp) == [
        "bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
        *hide, "--tmpfs", f"{tmux_root}/tmux-{uid}",
        "--tmpfs", str(home / ".ssh"), "--ro-bind", "/dev/null", str(home / ".netrc"),
        "--bind", st, st, "--ro-bind", f"{st}/settings.json", f"{st}/settings.json",
        "--ro-bind", f"{st}/bin", f"{st}/bin", "--ro-bind", f"{st}/builtin", f"{st}/builtin", "--bind", str(tmp), str(tmp), "--setenv", "TMPDIR", str(tmp),
        "--unsetenv", "TMUX", "--unsetenv", "TMUX_PANE", "--die-with-parent", "--", "agy", "-p", "hi"]


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
    monkeypatch.setattr(isolation.os.path, "realpath", lambda p: real if p == "/etc/resolv.conf" else real_realpath(p))
    monkeypatch.setattr(isolation.os.path, "isfile", lambda p: True if p == real else real_isfile(p))
    argv = wrap("agy", ["agy"], tmp_path)
    run_at = next(i for i in range(len(argv) - 1) if argv[i:i + 2] == ["--tmpfs", "/run"])
    bind_at = next(i for i in range(len(argv) - 2) if argv[i:i + 3] == ["--ro-bind", real, real])
    assert bind_at > run_at


@needs_bwrap
def test_agy_skips_missing_tmux_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path / "nowhere"))
    argv = wrap("agy", ["agy"], tmp_path)
    assert str(tmp_path / "nowhere") not in " ".join(argv)


def test_agy_rejected_on_darwin(tmp_path, monkeypatch):
    monkeypatch.setattr(isolation.sys, "platform", "darwin")
    with pytest.raises(UnsupportedPlatform, match="darwin"):
        wrap("agy", ["agy"], tmp_path)


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
    code = (f"import os; print(sorted(os.listdir({str(home / '.ssh')!r})))\n"
            f"try: print(repr(open({str(home / '.netrc')!r}).read()))\n"
            f"except PermissionError: print('denied')")
    proc = subprocess.run(wrap("agy", [sys.executable, "-c", code], tmp), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines()[0] == "[]"
    assert proc.stdout.splitlines()[1] in ("''", "denied")
    assert (home / ".ssh" / "id_test").read_text() == "PRIVATE"  # host copy untouched


@needs_bwrap
def test_real_bwrap_hides_new_secrets(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    files = {"projects/demo/.env": "SECRET=1", ".mozilla/firefox/p/cookies.sqlite": "c",
             ".gemini/oauth_creds.json": "{tok}", ".codex/sessions/x.jsonl": "s"}
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
    code = ("import os\n"
            "def rd(p):\n"
            "    try: return repr(open(p).read())\n"
            "    except PermissionError: return 'denied'\n"
            f"print(rd({str(home / 'projects/demo/.env')!r}))\n"
            f"print(rd({str(home / '.gemini/oauth_creds.json')!r}))\n"
            f"print(os.listdir({str(home / '.mozilla')!r}))\n"
            f"print(os.listdir({str(home / '.codex/sessions')!r}))")
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
    code = ("import os; print(os.listdir('/run'))\n"
            "print(os.listdir('/tmp/.X11-unix') if os.path.isdir('/tmp/.X11-unix') else [])\n"
            "print(len(open('/etc/resolv.conf').read()) >= 0)")
    proc = subprocess.run(wrap("agy", [sys.executable, "-c", code], tmp), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["[]", "[]", "True"]


@needs_bwrap
def test_real_bwrap_gemini_narrowed(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    state = home / ".gemini" / "antigravity-cli"
    (state / "bin").mkdir(parents=True)
    (state / "builtin").mkdir()
    (state / "settings.json").write_text("{}")
    (home / ".gemini" / "settings.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    tmp = tmp_path_factory.mktemp("agytmp")
    targets = {"state": state / "brain-x", "config": state / "settings.json", "bin": state / "bin" / "agentapi",
               "top": home / ".gemini" / "settings.json"}
    code = "import sys\nfor name, path in " + repr([(k, str(v)) for k, v in targets.items()]) + ":\n" \
           "    try: open(path, 'w').write('x'); print(name, 'ok')\n" \
           "    except OSError: print(name, 'denied')"
    proc = subprocess.run(wrap("agy", [sys.executable, "-c", code], tmp), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split("\n")[:4] == ["state ok", "config denied", "bin denied", "top denied"]
    assert (state / "settings.json").read_text() == "{}"


@needs_bwrap
def test_agy_refused_when_protected_entry_missing(tmp_path, monkeypatch):
    state = tmp_path / ".gemini" / "antigravity-cli"
    (state / "bin").mkdir(parents=True)
    (state / "builtin").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(UnsupportedPlatform, match="settings.json"):
        wrap("agy", ["agy"], tmp_path)
    assert not (state / "settings.json").exists()
