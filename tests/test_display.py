import io

import pytest
from rich.console import Console

from xvendor.display import Display
from xvendor.events import Event

JOB = "20261010T164006Z-codex-07b0"


@pytest.fixture
def out():
    return io.StringIO()


@pytest.fixture
def d(out):
    return Display(Console(file=out, width=80, force_terminal=False))


def test_header(d, out):
    d.header("codex", JOB, 2, 1201)
    assert "codex | 07b0 | round 2 | limit 21 min" in out.getvalue()


def test_header_write(d, out):
    d.header("codex", JOB, 1, 600, "write", "/work/dir")
    assert "| write /work/dir" in out.getvalue()
    d.header("codex", JOB, 1, 600)
    assert out.getvalue().count("write") == 1


@pytest.mark.parametrize("kind, prefix", [("action", "  run    "), ("note", "  note   "), ("error", "  error  ")])
def test_event_prefix(d, out, kind, prefix):
    d.event(Event(kind, "hello"))
    assert out.getvalue() == f"{prefix}hello\n"


def test_done_prints_nothing(d, out):
    d.event(Event("done", ""))
    assert out.getvalue() == ""


def test_event_whitespace_and_truncation(d, out):
    d.event(Event("action", "a \n\t b " + "x" * 200))
    assert out.getvalue() == "  run    a b " + "x" * 96 + "\n"


def test_markup_left_alone(d, out):
    d.event(Event("note", "see [red]x[/red] and [bold]"))
    assert "see [red]x[/red] and [bold]" in out.getvalue()


def test_finish_with_response(d, out):
    d.finish("done", 12, JOB, "# Title\n\n- item", ["err line"])
    text = out.getvalue()
    assert "[ response ]" in text
    assert "Title" in text and "item" in text
    assert "done | 12s | full text: xv show 07b0" in text
    assert "err line" not in text


def test_finish_blank_response(d, out):
    d.finish("failed", 3, JOB, "  \n", ["boom 1", "boom 2"])
    text = out.getvalue()
    assert "[ response ]" not in text
    assert "failed | 3s" in text
    assert "boom 1" in text and "boom 2" in text


def test_no_escape_codes_when_not_tty(d, out):
    d.header("agy", JOB, 1, 600)
    d.event(Event("error", "bad"))
    d.finish("timeout", 600, JOB, "**bold** answer", ["x"])
    assert "\x1b[" not in out.getvalue()
