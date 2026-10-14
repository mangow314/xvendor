from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from xvendor.events import Event


class Vendor(Protocol):
    name: str

    def build(self, round: int, session: str | None, prompt: str,
              profile: str | None, mode: str) -> list[str]: ...

    def parse(self, line: str) -> Event | None: ...

    def result(self, lines: list[str]) -> tuple[str | None, str, bool]: ...
        # (session, response, ok)
