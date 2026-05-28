"""Serial command JSON parsing."""

from custom_components.globalcache_itach.command_util import parse_serial_commands_json


def test_parse_serial_commands_json_ok() -> None:
    cmds, err = parse_serial_commands_json(
        '[{"name": "on", "payload": "PWR1"}]'
    )
    assert err is None
    assert cmds == [{"name": "on", "payload": "PWR1"}]


def test_parse_serial_commands_json_empty() -> None:
    cmds, err = parse_serial_commands_json("[]")
    assert err is None
    assert cmds == []
