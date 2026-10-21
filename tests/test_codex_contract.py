"""Contract with the real codex CLI, offline and without a login.

A wrapper script may put -c settings (e.g. model_provider) before xv's argv. codex drops every top-level -c once a
-c follows exec or resume (0.162.1, 0.163.0), so xv's argv must keep them all before exec. Probe: a top-level
provider that does not exist must still be the one codex fails to find.
"""

import os
import shutil
import subprocess

import pytest

from xvendor.vendors.codex import CodexVendor

pytestmark = pytest.mark.skipif(shutil.which("codex") is None, reason="needs the codex CLI")

SESSION = "00000000-0000-0000-0000-000000000000"


@pytest.mark.parametrize("mode", ["read", "write"])
@pytest.mark.parametrize("n", [1, 2])
def test_top_level_config_survives_xv_argv(tmp_path, n, mode):
    argv = CodexVendor().build(n, SESSION if n > 1 else None, "hi", None, mode=mode)
    argv = [argv[0], "-c", 'model_provider="xvprobe"', *argv[1:]]
    (tmp_path / "codex").mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "CODEX_HOME": str(tmp_path / "codex")}
    proc = subprocess.run(
        argv, env=env, cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30
    )
    assert "Model provider `xvprobe` not found" in proc.stdout + proc.stderr, proc.stdout + proc.stderr
