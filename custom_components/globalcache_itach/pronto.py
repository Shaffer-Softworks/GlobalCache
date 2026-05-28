"""Convert Pronto hex (learned format) to Global Caché sendir pulse pairs.

Pronto timebase: one Pronto unit = 0.241246 microseconds (per Global Caché / Pronto conventions).
Algorithm aligned with common IR tooling and the legacy Home Assistant itach stack (Pronto learned).
"""

from __future__ import annotations

PRONTO_US_PER_UNIT = 0.241246


def pronto_to_gc_sendir_tail(pronto_hex: str) -> tuple[int, list[int]]:
    """Return (carrier_hz, pulse_pairs) for sendir after module:port,id,freq,repeat,offset.

    Expects learned Pronto with first word 0000. Raises ValueError on unsupported input.
    """
    cleaned = pronto_hex.replace(",", " ").split()
    codes = [int(x, 16) for x in cleaned if x]
    if len(codes) < 4:
        msg = "Pronto code needs at least four 16-bit words"
        raise ValueError(msg)
    if codes[0] != 0:
        msg = "Only learned Pronto (leading 0000) is supported"
        raise ValueError(msg)
    freq_word = codes[1]
    if freq_word == 0:
        carrier_hz = 38000
    else:
        carrier_hz = int(round(1_000_000 / (freq_word * PRONTO_US_PER_UNIT)))
        carrier_hz = max(15000, min(500_000, carrier_hz))
    seq1_len = codes[2]
    seq2_len = codes[3]
    pairs: list[int] = []
    idx = 4

    def append_pairs(pair_count: int) -> None:
        nonlocal idx
        for _ in range(pair_count):
            if idx + 1 >= len(codes):
                msg = "Pronto data ended before sequence length was satisfied"
                raise ValueError(msg)
            on_u = codes[idx]
            off_u = codes[idx + 1]
            # iTach sendir expects Pronto timing words as pulse counts (see HA core
            # itach / Global Caché iConvert), not microsecond-derived values.
            on_pulses = max(1, int(on_u))
            off_pulses = max(1, int(off_u))
            pairs.extend((on_pulses, off_pulses))
            idx += 2

    append_pairs(seq1_len)
    append_pairs(seq2_len)
    if not pairs:
        msg = "No timing pairs found after Pronto header"
        raise ValueError(msg)
    return carrier_hz, pairs


def parse_gc_pair_string(data: str) -> list[int]:
    """Parse '1,2,3,4' into positive integers for sendir tail."""
    parts = [p.strip() for p in data.replace("\n", ",").split(",") if p.strip()]
    out: list[int] = []
    for p in parts:
        v = int(p, 10)
        if v < 1 or v > 65535:
            msg = f"Pulse value out of range: {v}"
            raise ValueError(msg)
        out.append(v)
    if len(out) % 2:
        msg = "GC pair string must contain an even number of integers"
        raise ValueError(msg)
    return out
