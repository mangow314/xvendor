import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

JOB_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-(codex|agy)-[0-9a-f]{4}$")
DATA_RE = re.compile(r"[A-Za-z0-9/._-]+")
SUFFIX_RE = re.compile(r"[0-9a-f]{4}")


class JobError(Exception):
    pass


class JobStore:
    def __init__(self, data: Path):
        raw = os.fspath(data)
        if not os.path.isabs(raw) or not DATA_RE.fullmatch(raw):
            raise JobError(f"invalid data dir: {raw!r}")
        self.jobs = Path(raw) / "jobs"

    def _dir(self, job: str) -> Path:
        if not isinstance(job, str) or not JOB_ID_RE.fullmatch(job):
            raise JobError(f"invalid job id: {job!r}")
        d = self.jobs / job
        if not (d / "meta.json").is_file():
            raise JobError(f"no such job: {job}")
        return d

    @staticmethod
    def _write_meta(d: Path, meta: dict) -> None:
        tmp = d / "meta.json.tmp"
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, d / "meta.json")

    def create(self, vendor: str, cwd: str, timeout: int, no_pane: bool,
               profile: str | None, request: str, writable: str | None = None) -> str:
        if not self.jobs.exists():
            self.jobs.mkdir(parents=True)
            os.chmod(self.jobs, 0o700)
        now = datetime.now(timezone.utc)
        job = f"{now:%Y%m%dT%H%M%SZ}-{vendor}-{secrets.token_hex(2)}"
        d = self.jobs / job
        (d / "round-1").mkdir(parents=True)
        (d / "round-1" / "request.md").write_text(request + "\n", encoding="utf-8")
        self._write_meta(d, {
            "vendor": vendor, "cwd": cwd, "timeout": timeout, "no_pane": no_pane, "profile": profile,
            "mode": "write" if writable else "read", "writable": writable,
            "session": None, "pane": None, "round": 1, "status": "running", "rc": None, "secs": None,
            "round_started": int(time.time()), "stop_requested": False, "run_pid": None,
            "created": f"{now:%Y-%m-%dT%H:%M:%SZ}",
        })
        return job

    def meta(self, job: str) -> dict:
        return json.loads((self._dir(job) / "meta.json").read_text(encoding="utf-8"))

    def update(self, job: str, **fields) -> None:
        d = self._dir(job)
        meta = self.meta(job)
        meta.update(fields)
        self._write_meta(d, meta)

    def updated(self, job: str) -> float:
        """Last meta write: a finished round's end, or a later close."""
        return (self._dir(job) / "meta.json").stat().st_mtime

    def new_round(self, job: str, request: str) -> int:
        n = self.meta(job)["round"] + 1
        rd = self._dir(job) / f"round-{n}"
        rd.mkdir()
        (rd / "request.md").write_text(request + "\n", encoding="utf-8")
        self.update(job, round=n, status="running", rc=None, secs=None,
                    round_started=int(time.time()), stop_requested=False)
        return n

    def round_dir(self, job: str, n: int) -> Path:
        d = self._dir(job)
        if type(n) is not int or n < 1:
            raise JobError(f"invalid round: {n!r}")
        return d / f"round-{n}"

    def list(self) -> list[tuple[str, dict]]:
        if not self.jobs.is_dir():
            return []
        ids = sorted(p.name for p in self.jobs.iterdir()
                     if JOB_ID_RE.fullmatch(p.name) and (p / "meta.json").is_file())
        return [(job, self.meta(job)) for job in ids]

    def running_writable(self, path: str) -> str | None:
        """The running job that holds path as its --write dir, or None."""
        for job, m in self.list():
            if m["status"] == "running" and m.get("writable") == path:
                return job
        return None

    def resolve(self, ref: str) -> str:
        if JOB_ID_RE.fullmatch(ref):
            self._dir(ref)
            return ref
        if not SUFFIX_RE.fullmatch(ref):
            raise JobError(f"invalid job ref: {ref!r}")
        matches = [p.name for p in self.jobs.glob(f"*-{ref}") if JOB_ID_RE.fullmatch(p.name)] \
            if self.jobs.is_dir() else []
        if len(matches) != 1:
            raise JobError(f"job ref {ref} matches {len(matches)} jobs")
        return matches[0]
