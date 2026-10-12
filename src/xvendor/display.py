import re

from rich.console import Console
from rich.markdown import Markdown
from rich.rule import Rule
from rich.text import Text

from xvendor.events import Event

VENDOR_STYLE = {"codex": "green", "agy": "blue"}
# same colours as xv-band
STATUS_STYLE = {"done": "green", "running": "yellow", "failed": "red", "timeout": "red", "stopped": "dim"}
LABEL = {"action": "run", "note": "note", "error": "error"}


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text)[:100]


class Display:
    """Line-by-line output for a round; no full-screen refresh (spec Concern 6)."""

    def __init__(self, console: Console | None = None):
        self.console = console or Console()

    def _print(self, text: Text) -> None:
        # soft_wrap: let the terminal wrap long lines instead of Rich inserting newlines
        self.console.print(text, markup=False, highlight=False, soft_wrap=True)

    def header(self, vendor: str, job: str, n: int, timeout: int) -> None:
        line = Text()
        line.append(vendor, style=VENDOR_STYLE.get(vendor, ""))
        line.append(f" | {job[-4:]} | round {n} | limit {(timeout + 59) // 60} min")
        self._print(line)
        self.console.print(Rule())

    def event(self, e: Event) -> None:
        if e.kind not in LABEL:
            return
        style = "red" if e.kind == "error" else ""
        self._print(Text(f"  {LABEL[e.kind]:<5}  {_one_line(e.text)}", style=style))

    def finish(self, status: str, secs: int, job: str, response: str, stderr_tail: list[str]) -> None:
        if response.strip():
            self._print(Text("\n[ response ]"))
            self.console.print(Markdown(response))
        line = Text("\n")
        line.append(status, style=STATUS_STYLE.get(status, ""))
        line.append(f" | {secs}s | full text: xv show {job[-4:]}")
        self._print(line)
        if status != "done":
            for s in stderr_tail:
                self._print(Text(s, style="dim"))
