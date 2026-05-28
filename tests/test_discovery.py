"""Global Caché UDP discovery parsing."""

from custom_components.globalcache_itach.discovery import (
    FLEX_BEACON,
    ITACH_BEACON,
    host_from_config_url,
    normalize_unique_id,
    parse_beacon,
)


def test_parse_itach_beacon() -> None:
    info = parse_beacon(ITACH_BEACON, source_host="10.0.0.1")
    assert info is not None
    assert info.uuid == "GlobalCache_000C1E024239"
    assert info.model == "iTachIP2IR"
    assert info.host == "192.168.1.100"
    assert info.revision == "710-1001-05"
    assert info.status == "Ready"
    assert info.unique_id == "GlobalCache_000C1E024239"


def test_parse_flex_beacon() -> None:
    info = parse_beacon(FLEX_BEACON)
    assert info is not None
    assert info.uuid == "GlobalCache_000C1E04E5D9"
    assert info.model == "iTachFlexEthernet"
    assert info.host == "192.168.0.147"


def test_parse_beacon_uses_source_ip_when_config_url_missing() -> None:
    raw = b"AMXB<-UUID=GlobalCache_000C1E024239><-Model=iTachIP2IR>"
    info = parse_beacon(raw, source_host="192.168.5.20")
    assert info is not None
    assert info.host == "192.168.5.20"


def test_parse_beacon_rejects_non_amxb() -> None:
    assert parse_beacon(b"NOTAMXB") is None


def test_normalize_unique_id_from_uuid() -> None:
    assert normalize_unique_id("GlobalCache_000c1e024239") == "GlobalCache_000C1E024239"


def test_normalize_unique_id_from_dhcp_mac() -> None:
    assert normalize_unique_id("00:0C:1E:02:42:39") == "GlobalCache_000C1E024239"


def test_host_from_config_url_trailing_dot() -> None:
    assert host_from_config_url("http://192.168.1.100.") == "192.168.1.100"


def test_host_from_config_url_without_scheme() -> None:
    assert host_from_config_url("192.168.0.147") == "192.168.0.147"
