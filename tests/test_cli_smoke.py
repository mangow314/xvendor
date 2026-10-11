import subprocess


def test_help_exits_zero():
    proc = subprocess.run(["xv", "--help"], capture_output=True, text=True)
    assert proc.returncode == 0
