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
    # Minimal learned-style pronto: 0000, freq word 006D (~38kHz), seq lens, one pair
    pronto = "0000 006D 0000 0002 00AC 00AC"
    freq, pairs = pronto_to_gc_sendir_tail(pronto)
    assert 30000 < freq < 50000
    assert len(pairs) == 2
    assert all(isinstance(x, int) for x in pairs)


def test_parse_gc_pairs() -> None:
    assert parse_gc_pair_string("10,20,30,40") == [10, 20, 30, 40]


def test_parse_gc_pairs_odd_raises() -> None:
    with pytest.raises(ValueError):
        parse_gc_pair_string("1,2,3")
