import json
import os
import subprocess
import time

import pytest

LINES = ['{"type":"thread.started","thread_id":"t1"}', '{"type":"turn.completed"}']


@pytest.fixture
def fixture_file(tmp_path):
    f = tmp_path / "events.jsonl"
    f.write_text("".join(line + "\n" for line in LINES), encoding="utf-8")
    return f


def run_fake(fakes_dir, name, env, *args):
    return subprocess.run([str(fakes_dir / name), *args], capture_output=True, text=True,
                          env={**os.environ, **env}, timeout=10)


@pytest.mark.parametrize("name", ["codex", "agy"])
def test_prints_fixture_lines(fakes_dir, fixture_file, name):
    proc = run_fake(fakes_dir, name, {"XV_FAKE_FIXTURE": str(fixture_file)})
    assert proc.returncode == 0
    assert proc.stdout.splitlines() == LINES


def test_no_fixture_prints_nothing(fakes_dir):
    env = {k: v for k, v in os.environ.items() if not k.startswith("XV_FAKE_")}
    proc = subprocess.run([str(fakes_dir / "codex")], capture_output=True, text=True, env=env, timeout=10)
    assert proc.returncode == 0
    assert proc.stdout == ""


def test_exit_code(fakes_dir, fixture_file):
    proc = run_fake(fakes_dir, "codex", {"XV_FAKE_FIXTURE": str(fixture_file), "XV_FAKE_RC": "3"})
    assert proc.returncode == 3
    assert proc.stdout.splitlines() == LINES


def test_argv_out(fakes_dir, tmp_path):
    out = tmp_path / "argv.json"
    proc = run_fake(fakes_dir, "agy", {"XV_FAKE_ARGV_OUT": str(out)}, "-p", "hello world", "--x")
    assert proc.returncode == 0
    assert json.loads(out.read_text(encoding="utf-8")) == ["-p", "hello world", "--x"]


def test_delay_and_line_flush(fakes_dir, fixture_file):
    env = {**os.environ, "XV_FAKE_FIXTURE": str(fixture_file), "XV_FAKE_DELAY": "0.3"}
    t0 = time.monotonic()
    with subprocess.Popen([str(fakes_dir / "codex")], stdout=subprocess.PIPE, text=True, env=env) as p:
        first = p.stdout.readline()
        t_first = time.monotonic() - t0
        rest = p.stdout.read()
        rc = p.wait(timeout=10)
    t_all = time.monotonic() - t0
    assert first.rstrip("\n") == LINES[0]
    assert rest.splitlines() == LINES[1:]
    assert rc == 0
    # first line arrives before the second delay elapses (flushed per line)
    assert t_all - t_first >= 0.2
    assert t_all >= 0.6


def test_sigterm_exits_143(fakes_dir, fixture_file):
    env = {**os.environ, "XV_FAKE_FIXTURE": str(fixture_file), "XV_FAKE_DELAY": "5"}
    with subprocess.Popen([str(fakes_dir / "codex")], stdout=subprocess.PIPE, env=env) as p:
        time.sleep(0.5)
        p.terminate()
        assert p.wait(timeout=5) == 143
