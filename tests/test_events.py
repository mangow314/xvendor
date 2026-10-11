import dataclasses

import pytest

from xvendor.events import Event


def test_fields_readable():
    e = Event("action", "rg foo")
    assert e.kind == "action"
    assert e.text == "rg foo"


def test_frozen():
    e = Event("note", "hi")
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.text = "changed"


def test_equal_when_fields_equal():
    assert Event("done", "") == Event("done", "")
    assert Event("error", "x") != Event("error", "y")
