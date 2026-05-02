"""Tests for command helpers (no Home Assistant runtime)."""

import json

from custom_components.globalcache_itach.command_util import (
    activity_labels_from_spec,
    parse_commands_json,
)


def test_activity_labels_dedupe_and_sort():
    spec = {
        "commands": [
            {"name": "power"},
            {"name": "Volume Up"},
            {"name": "POWER"},
            {"name": "mute"},
        ]
    }
    assert activity_labels_from_spec(spec) == ["mute", "power", "Volume Up"]


def test_activity_labels_empty():
    assert activity_labels_from_spec({}) == []
    assert activity_labels_from_spec({"commands": []}) == []


def test_parse_commands_json_ok():
    raw = json.dumps(
        [{"name": "power", "format": "pronto", "data": "0000 006D", "freq": 38000}]
    )
    cmds, err = parse_commands_json(raw)
    assert err is None and cmds is not None
    assert cmds[0]["name"] == "power"
    assert cmds[0]["freq"] == 38000


def test_parse_commands_json_invalid_format():
    raw = json.dumps([{"name": "x", "format": "bogus", "data": "1"}])
    cmds, err = parse_commands_json(raw)
    assert cmds is None and err and "format" in err.lower()
