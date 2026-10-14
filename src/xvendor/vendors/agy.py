import json

from xvendor.events import Event


def _load(line: str) -> dict | None:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


class AgyVendor:
    name = "agy"

    def build(self, round: int, session: str | None, prompt: str, profile: str | None,
              mode: str = "read") -> list[str]:
        if profile:
            raise ValueError("--profile is only supported for codex")
        argv = ["agy", "-p", prompt, "--output-format", "stream-json", "--dangerously-skip-permissions"]
        if round > 1:
            argv += ["--conversation", session]
        return argv

    def parse(self, line: str) -> Event | None:
        ev = _load(line)
        if ev is None:
            return None
        step = ev.get("step_update") or {}
        if (ev.get("event") == "step_update" and step.get("step_type") == "tool"
                and step.get("state") == "ACTIVE"):
            params = (step.get("tool_info") or {}).get("parameters") or {}
            text = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
            return Event("action", f"{step.get('tool_name')}  {text}")
        if ev.get("event") == "result":
            return Event("done", "")
        return None

    def result(self, lines: list[str]) -> tuple[str | None, str, bool]:
        session, response, ok = None, "", False
        for line in lines:
            ev = _load(line)
            if ev is None:
                continue
            if ev.get("event") == "init" and session is None:
                session = ev.get("conversation_id")
            elif ev.get("event") == "result":
                res = ev.get("result") or {}
                response = res.get("response") or ""
                ok = ok or res.get("status") == "SUCCESS"
        return session, response, ok
