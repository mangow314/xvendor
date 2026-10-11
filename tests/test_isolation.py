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
    monkeypatch.setenv("HOME", str(home))
    uid = os.getuid()
    tmux_root = tmp_path / "tmuxroot"
    (tmux_root / f"tmux-{uid}").mkdir(parents=True)
    monkeypatch.setenv("TMUX_TMPDIR", str(tmux_root))
    tmp = tmp_path / "agytmp"
    gemini = str(home / ".gemini")
    hide = ["--tmpfs", f"/run/user/{uid}"] if os.path.isdir(f"/run/user/{uid}") else []
    assert wrap("agy", ["agy", "-p", "hi"], tmp) == [
        "bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
        *hide, "--tmpfs", f"{tmux_root}/tmux-{uid}",
        "--tmpfs", str(home / ".ssh"), "--ro-bind", "/dev/null", str(home / ".netrc"),
        "--bind", gemini, gemini, "--bind", str(tmp), str(tmp), "--setenv", "TMPDIR", str(tmp),
        "--unsetenv", "TMUX", "--unsetenv", "TMUX_PANE", "--die-with-parent", "--", "agy", "-p", "hi"]


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
    (home / ".gemini").mkdir()
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
