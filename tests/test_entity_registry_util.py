"""Entity unique_id helpers."""

from custom_components.globalcache_itach.const import serial_listen_enabled
from custom_components.globalcache_itach.entity_registry_util import (
    active_remote_button_unique_ids,
    active_remote_unique_ids,
    active_serial_button_unique_ids,
    active_serial_rx_unique_ids,
    is_options_managed_unique_id,
    remote_button_unique_id,
    serial_button_unique_id,
    serial_rx_unique_id,
    unique_id_matches_platform,
)


def test_serial_listen_enabled_legacy_key() -> None:
    assert serial_listen_enabled({"monitor_incoming": False}) is False
    assert serial_listen_enabled({"listen": False}) is False
    assert serial_listen_enabled({}) is True


def test_serial_button_unique_id() -> None:
    uid = serial_button_unique_id("abc", "sid1", "Power On")
    assert uid == "abc_serial_sid1_btn_power_on"


def test_remote_button_unique_id() -> None:
    uid = remote_button_unique_id("entry1", "r1", "Power On")
    assert uid == "entry1_r1_btn_power_on"


def test_options_managed_unique_id() -> None:
    eid = "01ENTRY"
    assert is_options_managed_unique_id(
        eid, f"{eid}_serial_abc_btn_status"
    )
    assert not is_options_managed_unique_id(
        eid, f"{eid}_tcp_connected"
    )
    assert unique_id_matches_platform(
        eid, f"{eid}_serial_abc", "text"
    )
    assert unique_id_matches_platform(
        eid, f"{eid}_serial_abc_btn_x", "button"
    )
    assert unique_id_matches_platform(
        eid, f"{eid}_r1_btn_on", "button"
    )
    assert not unique_id_matches_platform(
        eid, f"{eid}_r1_btn_on", "remote"
    )
    assert unique_id_matches_platform(
        eid, f"{eid}_serial_abc_rx", "sensor"
    )
    assert serial_rx_unique_id("entry1", "s1") == "entry1_serial_s1_rx"


def test_active_sets_from_options() -> None:
    class FakeEntry:
        entry_id = "entry1"
        options = {
            "remotes": [
                {
                    "remote_id": "r1",
                    "name": "TV",
                    "module": 1,
                    "port": 1,
                    "commands": [
                        {
                            "name": "on",
                            "format": "pronto",
                            "data": "0000",
                        }
                    ],
                }
            ],
            "serial_ports": [
                {
                    "serial_id": "s1",
                    "name": "RS232",
                    "module": 1,
                    "port": 1,
                    "monitor_incoming": True,
                    "commands": [{"name": "status", "payload": "ST"}],
                }
            ],
        }

    assert active_remote_unique_ids(FakeEntry()) == {"entry1_r1"}  # type: ignore[arg-type]
    assert active_serial_button_unique_ids(FakeEntry()) == {  # type: ignore[arg-type]
        "entry1_serial_s1_btn_status"
    }
    assert active_serial_rx_unique_ids(FakeEntry()) == {"entry1_serial_s1_rx"}  # type: ignore[arg-type]
    assert active_remote_button_unique_ids(FakeEntry()) == {"entry1_r1_btn_on"}  # type: ignore[arg-type]
