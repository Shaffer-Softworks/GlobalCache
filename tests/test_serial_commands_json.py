"""Serial command JSON parsing."""

from custom_components.globalcache_itach.command_util import (
    apply_command_change,
    format_command_summary,
    imported_commands_error,
    parse_serial_commands_json,
    remove_command_at,
    serial_command_from_fields,
)


def test_parse_serial_commands_json_ok() -> None:
    cmds, err = parse_serial_commands_json(
        '[{"name": "on", "payload": "PWR1"}]'
    )
    assert err is None
    assert cmds == [{"name": "on", "payload": "PWR1"}]


def test_serial_add_rename_remove_and_slug() -> None:
    status, err = serial_command_from_fields("Status", "STATUS")
    assert err is None and status is not None
    commands, err = apply_command_change([], status, original_index=None)
    assert err is None and commands is not None
    power, err = serial_command_from_fields("Power", "PWR ON")
    assert power is not None
    commands, err = apply_command_change(commands, power, original_index=None)
    assert err is None and commands is not None
    renamed, err = serial_command_from_fields("Power On", "PWR ON")
    assert renamed is not None
    commands, err = apply_command_change(commands, renamed, original_index=1)
    assert err is None and commands is not None
    assert [cmd["name"] for cmd in commands] == ["Status", "Power On"]
    clash, err = serial_command_from_fields("power_on", "PWR OFF")
    assert clash is not None
    updated, err = apply_command_change(commands, clash, original_index=None)
    assert updated is None and err == "duplicate_command"
    assert imported_commands_error(commands) is None
    assert format_command_summary(commands, include_format=False) == "Status, Power On"
    assert remove_command_at(commands, 0)[0]["name"] == "Power On"


def test_parse_serial_commands_json_empty() -> None:
    cmds, err = parse_serial_commands_json("[]")
    assert err is None
    assert cmds == []
