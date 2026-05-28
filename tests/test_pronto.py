"""Tests for Pronto to Global Caché conversion."""

import pytest

from custom_components.globalcache_itach.pronto import (
    parse_gc_pair_string,
    pronto_to_gc_sendir_tail,
)


def test_pronto_short_header_only_raises() -> None:
    with pytest.raises(ValueError):
        pronto_to_gc_sendir_tail("0000 006D 0000 0000")


def test_pronto_minimal_pairs() -> None:
    # 0000, freq 006D (~38 kHz), 1 pair in seq1, 0 in seq2
    pronto = "0000 006D 0001 0000 00AC 00AC"
    freq, pairs = pronto_to_gc_sendir_tail(pronto)
    assert 30000 < freq < 50000
    assert pairs == [0xAC, 0xAC]


def test_parse_gc_pairs() -> None:
    assert parse_gc_pair_string("10,20,30,40") == [10, 20, 30, 40]


def test_parse_gc_pairs_odd_raises() -> None:
    with pytest.raises(ValueError):
        parse_gc_pair_string("1,2,3")


def test_pronto_respects_sequence_lengths() -> None:
    # Header + 2 pairs in seq1 + 1 pair in seq2; trailing garbage must be ignored.
    pronto = "0000 006D 0002 0001 00AC 00AC 0010 0020 FFFF FFFF"
    _freq, pairs = pronto_to_gc_sendir_tail(pronto)
    assert len(pairs) == 6  # three on/off pairs total


def test_pronto_tv_on_sample() -> None:
    pronto = (
        "0000 006d 0022 0002 0157 00ac 0015 0015 0015 0015 0015 0040 0015 0015 "
        "0015 0015 0015 0015 0015 0015 0015 0015 0015 0040 0015 0040 0015 0015 "
        "0015 0040 0015 0040 0015 0040 0015 0040 0015 0040 0015 0015 0015 0015 "
        "0015 0040 0015 0015 0015 0015 0015 0015 0015 0040 0015 0040 0015 0040 "
        "0015 0040 0015 0015 0015 0040 0015 0040 0015 0040 0015 0015 0015 0015 "
        "0015 0689 0157 0056 0015 0e94"
    )
    _freq, pairs = pronto_to_gc_sendir_tail(pronto)
    assert len(pairs) == 72  # (34 + 2) on/off pairs
    assert pairs[0:2] == [0x0157, 0x00AC]
    assert max(pairs) > 1000
