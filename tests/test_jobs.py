import json
import os
import re
import shutil
import stat
import time
from datetime import datetime, timezone

import pytest

from xvendor.jobs import JOB_ID_RE, JobError, JobStore

META_KEYS = {
    "vendor",
    "cwd",
    "timeout",
    "no_pane",
    "profile",
    "session",
    "pane",
    "round",
    "status",
    "rc",
    "secs",
    "round_started",
    "stop_requested",
    "created",
}


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path)


def new_job(store, vendor="codex", request="hello"):
    return store.create(vendor, "/work/dir", 600, False, None, request)


# 1. data path validation


@pytest.mark.parametrize("data", ["relative/dir", "/tmp/has space", "/tmp/a$b", "/tmp/a;b", "/tmp/中文"])
def test_init_rejects_bad_data(data):
    with pytest.raises(JobError):
        JobStore(data)


def test_init_accepts_abs_path(tmp_path):
    JobStore(tmp_path / "a.b_c-d")


# 2. create


def test_create_id_format(store):
    job = new_job(store, vendor="agy")
    assert JOB_ID_RE.fullmatch(job)
    assert job.split("-")[1] == "agy"


def test_create_id_time_is_utc(store):
    before = datetime.now(timezone.utc).replace(microsecond=0)
    job = new_job(store)
    after = datetime.now(timezone.utc)
    stamp = datetime.strptime(job.split("-")[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    assert before <= stamp <= after


def test_create_request_file(store, tmp_path):
    job = new_job(store, request="line one\nline two")
    req = tmp_path / "jobs" / job / "round-1" / "request.md"
    assert req.read_text(encoding="utf-8") == "line one\nline two\n"


def test_create_makes_jobs_dir_0700(store, tmp_path):
    new_job(store)
    assert stat.S_IMODE((tmp_path / "jobs").stat().st_mode) == 0o700


def test_create_keeps_existing_jobs_dir_mode(store, tmp_path):
    (tmp_path / "jobs").mkdir()
    os.chmod(tmp_path / "jobs", 0o755)
    new_job(store)
    assert stat.S_IMODE((tmp_path / "jobs").stat().st_mode) == 0o755


# 3. new meta


def test_create_meta_fields(store):
    t0 = int(time.time())
    job = store.create("codex", "/work/dir", 900, True, "local", "q")
    m = store.meta(job)
    assert set(m) >= META_KEYS
    assert (m["vendor"], m["cwd"], m["timeout"], m["no_pane"], m["profile"]) == (
        "codex",
        "/work/dir",
        900,
        True,
        "local",
    )
    assert m["session"] is None and m["pane"] is None and m["rc"] is None and m["secs"] is None
    assert m["round"] == 1
    assert m["status"] == "running"
    assert type(m["round_started"]) is int and t0 <= m["round_started"] <= int(time.time())
    assert m["stop_requested"] is False
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", m["created"])


def test_meta_matches_file(store, tmp_path):
    job = new_job(store)
    on_disk = json.loads((tmp_path / "jobs" / job / "meta.json").read_text(encoding="utf-8"))
    assert store.meta(job) == on_disk


# 4. update


def test_update_merges_fields(store):
    job = new_job(store)
    store.update(job, status="done", rc=0, session="s1")
    m = store.meta(job)
    assert (m["status"], m["rc"], m["session"]) == ("done", 0, "s1")
    assert m["vendor"] == "codex" and m["timeout"] == 600


def test_update_is_atomic_replace(store, tmp_path, monkeypatch):
    job = new_job(store)
    calls = []
    real_replace = os.replace

    def spy(src, dst):
        calls.append((os.fspath(src), os.fspath(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    store.update(job, status="done")
    meta_path = tmp_path / "jobs" / job / "meta.json"
    assert (str(meta_path) + ".tmp", str(meta_path)) in calls
    assert not (tmp_path / "jobs" / job / "meta.json.tmp").exists()
    assert json.loads(meta_path.read_text(encoding="utf-8"))["status"] == "done"


# 5. job id validation

BAD_IDS = ["../x", "", "20261010T164006Z-codex-07b0/..", "20261010T164006Z-gpt-07b0", "20261010T164006Z-codex-07B0"]


@pytest.mark.parametrize("bad", BAD_IDS)
@pytest.mark.parametrize(
    "call",
    [
        lambda s, j: s.meta(j),
        lambda s, j: s.update(j, status="done"),
        lambda s, j: s.new_round(j, "x"),
        lambda s, j: s.round_dir(j, 1),
    ],
)
def test_bad_job_id_rejected(store, bad, call):
    new_job(store)
    with pytest.raises(JobError):
        call(store, bad)


@pytest.mark.parametrize(
    "call",
    [
        lambda s, j: s.meta(j),
        lambda s, j: s.update(j, status="done"),
        lambda s, j: s.new_round(j, "x"),
        lambda s, j: s.round_dir(j, 1),
        lambda s, j: s.resolve(j),
    ],
)
def test_missing_job_rejected(store, call):
    new_job(store)
    with pytest.raises(JobError):
        call(store, "20200101T000000Z-codex-abcd")


# 6. new_round


def test_new_round(store, tmp_path):
    job = new_job(store)
    store.update(job, status="done", rc=0, secs=12, stop_requested=True, round_started=1)
    t0 = int(time.time())
    n = store.new_round(job, "follow up")
    assert n == 2
    assert (tmp_path / "jobs" / job / "round-2" / "request.md").read_text(encoding="utf-8") == "follow up\n"
    m = store.meta(job)
    assert m["round"] == 2
    assert m["status"] == "running"
    assert m["rc"] is None and m["secs"] is None
    assert m["stop_requested"] is False
    assert type(m["round_started"]) is int and m["round_started"] >= t0
    assert store.new_round(job, "third") == 3


# 7. round_dir


def test_round_dir(store, tmp_path):
    job = new_job(store)
    assert store.round_dir(job, 1) == tmp_path / "jobs" / job / "round-1"


@pytest.mark.parametrize("n", [0, -1])
def test_round_dir_rejects_non_positive(store, n):
    job = new_job(store)
    with pytest.raises(JobError):
        store.round_dir(job, n)


# 8. resolve


def test_resolve_full_id(store):
    job = new_job(store)
    assert store.resolve(job) == job


def test_resolve_suffix(store):
    job = new_job(store)
    other = new_job(store)
    if other[-4:] == job[-4:]:
        pytest.skip("random suffix collision")
    assert store.resolve(job[-4:]) == job


def test_resolve_no_match(store):
    job = new_job(store)
    unused = next(h for h in ("0000", "ffff", "1234") if h != job[-4:])
    with pytest.raises(JobError):
        store.resolve(unused)


def test_resolve_ambiguous(store, tmp_path):
    job = new_job(store)
    twin = "20200101T000000Z-agy-" + job[-4:]
    shutil.copytree(tmp_path / "jobs" / job, tmp_path / "jobs" / twin)
    with pytest.raises(JobError):
        store.resolve(job[-4:])


# list


def test_list_sorted_by_id(store, tmp_path):
    a = new_job(store)
    early = "20200101T000000Z-codex-0000"
    shutil.copytree(tmp_path / "jobs" / a, tmp_path / "jobs" / early)
    listed = store.list()
    assert [j for j, _ in listed] == sorted([a, early])
    assert dict(listed)[a] == store.meta(a)


# --write fields


def test_create_write_meta_and_running_writable(store):
    job = store.create("codex", "/w", 600, False, None, "q", "/w")
    m = store.meta(job)
    assert (m["mode"], m["writable"]) == ("write", "/w")
    assert store.running_writable("/w")
    assert not store.running_writable("/other")
    store.update(job, status="done")
    assert not store.running_writable("/w")
    m = store.meta(new_job(store))
    assert (m["mode"], m["writable"]) == ("read", None)


def test_old_meta_without_mode_fields(store):
    job = new_job(store)
    path = store.jobs / job / "meta.json"
    m = json.loads(path.read_text())
    del m["mode"], m["writable"]
    path.write_text(json.dumps(m))
    m = store.meta(job)
    assert m.get("mode", "read") == "read" and m.get("writable") is None
    assert not store.running_writable("/work/dir")
