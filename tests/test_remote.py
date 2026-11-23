"""Tests for command helpers (no Home Assistant runtime)."""

import json

from custom_components.globalcache_itach.command_util import (
    activity_labels_from_spec,
    apply_command_change,
    command_json_error_key,
    format_command_summary,
    imported_commands_error,
    ir_command_from_fields,
    ir_send_repeat,
    parse_commands_json,
    remove_command_at,
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
    assert command_json_error_key(err) == "bad_format"


def test_command_json_error_keys():
    assert command_json_error_key("Expecting ',' delimiter: line 4 column 3") == (
        "invalid_json"
    )
    assert command_json_error_key("commands must be a JSON array") == (
        "commands_not_array"
    )
    assert command_json_error_key("name and data are required") == "command_fields"


def test_add_replace_rename_and_remove_command():
    power, err = ir_command_from_fields("PowerOn", "172,172", "gc_pairs", freq=38000)
    assert err is None and power is not None
    assert power["freq"] == 38000
    commands, err = apply_command_change([], power, original_index=None)
    assert err is None and commands is not None
    assert [cmd["name"] for cmd in commands] == ["PowerOn"]

    again, err = ir_command_from_fields("poweron", "999", "gc_pairs")
    assert again is not None
    commands, err = apply_command_change(commands, again, original_index=None)
    assert err is None and commands is not None
    assert commands[0]["name"] == "poweron"
    assert commands[0]["data"] == "999"
    assert "freq" not in commands[0]

    off, err = ir_command_from_fields("PowerOff", "173,173", "gc_pairs")
    assert off is not None
    commands, err = apply_command_change(commands, off, original_index=None)
    assert err is None and commands is not None
    renamed, err = ir_command_from_fields("Standby", "173,173", "pronto_hex")
    assert renamed is not None and renamed["format"] == "pronto"
    commands, err = apply_command_change(commands, renamed, original_index=1)
    assert err is None and commands is not None
    assert [cmd["name"] for cmd in commands] == ["poweron", "Standby"]

    commands = remove_command_at(commands, 0)
    assert [cmd["name"] for cmd in commands] == ["Standby"]


def test_slug_collision_rejected():
    first, err = ir_command_from_fields("Volume Up", "1", "pronto")
    assert first is not None and err is None
    commands, err = apply_command_change([], first, original_index=None)
    assert commands is not None and err is None
    clash, err = ir_command_from_fields("volume_up", "2", "pronto")
    assert clash is not None
    updated, err = apply_command_change(commands, clash, original_index=None)
    assert updated is None and err == "duplicate_command"
    assert imported_commands_error([first, clash]) == "duplicate_command"


def test_import_two_object_array():
    raw = """
    [
      {"name": "PowerOn", "data": "172,172", "format": "gc_pairs"},
      {"name": "PowerOff", "data": "173,173", "format": "gc_pairs"}
    ]
    """
    cmds, err = parse_commands_json(raw)
    assert err is None and cmds is not None
    assert imported_commands_error(cmds) is None
    assert [cmd["name"] for cmd in cmds] == ["PowerOn", "PowerOff"]
    assert "PowerOn (gc_pairs)" in format_command_summary(cmds, include_format=True)


def test_ir_send_repeat_uses_command_override():
    cmd, err = ir_command_from_fields(
        "Power", "22,64", "gc_pairs", repeat=3
    )
    assert err is None and cmd is not None
    assert ir_send_repeat(cmd, 1) == 3
    blank, err = ir_command_from_fields("Power", "22,64", "gc_pairs")
    assert err is None and blank is not None
    assert ir_send_repeat(blank, 2) == 2


def test_blank_override_and_out_of_range():
    cmd, err = ir_command_from_fields("Power", "1", "pronto", freq="", repeat=None)
    assert err is None and cmd is not None
    assert "freq" not in cmd and "repeat" not in cmd
    _cmd, err = ir_command_from_fields("Power", "1", "pronto", freq=1000)
    assert _cmd is None and err == "command_override_range"
    assert format_command_summary([], include_format=True).startswith("No commands yet")
