import json
from datetime import datetime

from xvendor.events import Event
from xvendor.isolation import state_dir

SHELL_PREFIXES = ("/bin/bash -lc ", "/bin/sh -lc ")
QUOTES = ("'", '"')


def _load(line: str) -> dict | None:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _unwrap(command: str) -> str:
    for prefix in SHELL_PREFIXES:
        command = command.removeprefix(prefix)
    if command[:1] in QUOTES:
        command = command[1:]
    if command[-1:] in QUOTES:
        command = command[:-1]
    return command


class CodexVendor:
    name = "codex"

    def build(self, round: int, session: str | None, prompt: str, profile: str | None, mode: str = "read") -> list[str]:
        sandbox = "workspace-write" if mode == "write" else "read-only"
        # -p must precede exec: `codex exec resume -p` is rejected (2026-10-10)
        pf = ["-p", profile] if profile else []
        # codex's own bwrap makes <root>/.git read-only for every writable root; /tmp and TMPDIR are read-only under xv so the mount point cannot be created (2026-10-14)
        roots = (
            [
                "-c",
                "sandbox_workspace_write.exclude_slash_tmp=true",
                "-c",
                "sandbox_workspace_write.exclude_tmpdir_env_var=true",
                "-c",
                "sandbox_workspace_write.writable_roots=[]",
            ]
            if mode == "write"
            else []
        )
        if round == 1:
            return ["codex", *pf, "exec", "-s", sandbox, *roots, "--skip-git-repo-check", "--json", prompt]
        # resume does not inherit round 1's -s read-only (2026-10-10), so pass it every round
        return [
            "codex",
            *pf,
            "exec",
            "resume",
            "-c",
            f'sandbox_mode="{sandbox}"',
            *roots,
            "--skip-git-repo-check",
            "--json",
            session,
            prompt,
        ]

    def parse(self, line: str) -> Event | None:
        ev = _load(line)
        if ev is None:
            return None
        kind = ev.get("type")
        item = ev.get("item") or {}
        match kind, item.get("type"):
            case "item.started", "command_execution":
                return Event("action", _unwrap(item.get("command", "")))
            case "item.completed", "agent_message":
                return Event("note", item.get("text", ""))
            case (("error" | "turn.failed"), _):
                return Event("error", ev.get("message") or (ev.get("error") or {}).get("message") or line)
            case "turn.completed", _:
                return Event("done", "")
        return None

    def model(self, session: str, since: int) -> str | None:
        """Model and effort of the round started at epoch `since`, from codex's rollout file (the --json stream
        carries neither). The rollout holds every round's turn_context, so older ones are skipped by timestamp;
        None until this round's is written. effort is absent from it when codex uses the model default."""
        for path in (state_dir("codex") / "sessions").glob(f"*/*/*/rollout-*-{session}.jsonl"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
            ctx = None
            for line in text.splitlines():
                ev = _load(line) or {}
                if ev.get("type") != "turn_context":
                    continue
                try:
                    ts = datetime.fromisoformat(ev.get("timestamp") or "").timestamp()
                except ValueError:
                    continue
                if ts >= since:
                    ctx = ev.get("payload") or {}
            if ctx and ctx.get("model"):
                return f"{ctx['model']} | effort {ctx.get('effort') or 'default'}"
        return None

    def result(self, lines: list[str]) -> tuple[str | None, str, bool]:
        session, response, ok = None, "", False
        for line in lines:
            ev = _load(line)
            if ev is None:
                continue
            kind = ev.get("type")
            item = ev.get("item") or {}
            if kind == "thread.started" and session is None:
                session = ev.get("thread_id")
            elif kind == "item.completed" and item.get("type") == "agent_message":
                response = item.get("text", "")
            elif kind == "turn.completed":
                ok = True
        return session, response, ok
