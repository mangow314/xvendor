from dataclasses import dataclass
from typing import Literal

Kind = Literal["action", "note", "error", "done", "model"]


@dataclass(frozen=True)
class Event:
    kind: Kind
    text: str
