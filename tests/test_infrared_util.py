"""Tests for HA infrared ↔ Global Caché timing conversion."""

import pytest

from custom_components.globalcache_itach.device_util import (
    list_ir_connectors,
    parse_getdevices_lines,
)
from custom_components.globalcache_itach.infrared_util import (
    gc_pairs_to_us_timings,
    ir_connectors_from_modules,
    parse_sendir_line,
    us_timings_to_gc_pairs,
)


def test_us_to_gc_roundtrip_approx() -> None:
    freq = 38000
    # NEC-like leader + bit + end mark (odd length; needs trailing gap)
    timings = [9000, -4500, 562, -562, 562, -1687, 562]
    pairs = us_timings_to_gc_pairs(timings, freq)
    assert len(pairs) % 2 == 0
    assert pairs[0] > 100  # ~9000 µs at 38 kHz
    # Trailing pad must be a frame gap (~40 ms), not the ~80 µs minimum
    assert pairs[-1] > 1000
    back = gc_pairs_to_us_timings(pairs, freq)
    assert back[0] > 0 and back[1] < 0
    # Round-trip within one carrier period (~26 µs)
    assert abs(abs(back[0]) - 9000) < 40
    assert abs(abs(back[-1]) - 40_000) < 40


def test_us_empty_raises() -> None:
    with pytest.raises(ValueError):
        us_timings_to_gc_pairs([], 38000)


def test_parse_sendir_line() -> None:
    line = "sendir,1:2,1,36429,1,1,95,34,15,17"
    parsed = parse_sendir_line(line)
    assert parsed is not None
    assert parsed["module"] == 1
    assert parsed["port"] == 2
    assert parsed["frequency"] == 36429
    assert parsed["pairs"] == [95, 34, 15, 17]


def test_parse_sendir_compressed_returns_none() -> None:
    assert parse_sendir_line("sendir,1:1,1,38000,1,1,4,5A8,9") is None


def test_ir_connectors_from_modules() -> None:
    mods = parse_getdevices_lines(
        [
            "device,3,3,RELAY",
            "device,4,3,IR",
            "device,5,2,IR",
            "endlistdevices",
        ]
    )
    assert ir_connectors_from_modules(mods) == [
        (4, 1),
        (4, 2),
        (4, 3),
        (5, 1),
        (5, 2),
    ]


def test_list_ir_connectors_fallback_remotes() -> None:
    remotes = [
        {"module": 1, "port": 2, "remote_id": "a"},
        {"module": 1, "port": 2, "remote_id": "b"},
        {"module": 1, "port": 3, "remote_id": "c"},
    ]
    assert list_ir_connectors([], remotes=remotes) == [(1, 2), (1, 3)]


def test_list_ir_connectors_default() -> None:
    assert list_ir_connectors([]) == [(1, 1)]
