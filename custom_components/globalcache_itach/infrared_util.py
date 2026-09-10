"""Convert between HA infrared signed-µs timings and Global Caché sendir pulse pairs."""

from __future__ import annotations

import math
import re
from typing import Any

# Global Caché requires every on/off duration to be at least ~80 µs.
_MIN_DURATION_US = 80
# NEC (and similar) frames end on a mark with no trailing space in
# infrared-protocols. sendir needs an off pulse; ~80 µs truncates the frame
# so receivers never decode. Use a typical end-of-frame gap (~40 ms).
_DEFAULT_TRAILING_GAP_US = 40_000

_SENDIR_RE = re.compile(
    r"^sendir,(\d+):(\d+),(\d+),(\d+),(\d+),(\d+),(.+)$",
    re.IGNORECASE,
)


def _period_us(frequency_hz: int) -> float:
    freq = max(15_000, min(500_000, int(frequency_hz)))
    return 1_000_000.0 / freq


def _min_pulses(frequency_hz: int) -> int:
    return max(1, int(math.ceil(_MIN_DURATION_US / _period_us(frequency_hz))))


def us_to_pulses(duration_us: int, frequency_hz: int) -> int:
    """Convert a microsecond duration to a GC carrier-period pulse count."""
    pulses = int(round(abs(int(duration_us)) / _period_us(frequency_hz)))
    return max(_min_pulses(frequency_hz), min(65_535, pulses))


def pulses_to_us(pulses: int, frequency_hz: int) -> int:
    """Convert a GC pulse count back to microseconds."""
    return max(1, int(round(abs(int(pulses)) * _period_us(frequency_hz))))


def us_timings_to_gc_pairs(timings: list[int], frequency_hz: int) -> list[int]:
    """Flatten signed µs timings (mark+, space-) into GC on/off pulse pairs.

    A trailing mark-only timing is padded with a ~40 ms off pulse so sendir
    always receives an even number of values and IR receivers see a frame gap
    (infrared-protocols NEC ends on a mark with no trailing space).
    """
    if not timings:
        msg = "IR command has no timings"
        raise ValueError(msg)
    pairs = [us_to_pulses(t, frequency_hz) for t in timings]
    if len(pairs) % 2 == 1:
        pairs.append(us_to_pulses(_DEFAULT_TRAILING_GAP_US, frequency_hz))
    if len(pairs) > 520:
        msg = f"Too many pulse values for sendir ({len(pairs)} > 520)"
        raise ValueError(msg)
    return pairs


def gc_pairs_to_us_timings(pairs: list[int], frequency_hz: int) -> list[int]:
    """Convert GC on/off pulse counts to signed µs timings (mark+, space-)."""
    if not pairs:
        msg = "No pulse pairs"
        raise ValueError(msg)
    if len(pairs) % 2:
        msg = "GC pulse list must contain an even number of values"
        raise ValueError(msg)
    out: list[int] = []
    for i, pulse in enumerate(pairs):
        us = pulses_to_us(pulse, frequency_hz)
        out.append(us if i % 2 == 0 else -us)
    return out


def parse_sendir_line(line: str) -> dict[str, Any] | None:
    """Parse a Global Caché ``sendir,...`` line into module/port/freq/pairs.

    Returns None if the line is not a sendir frame.
    """
    text = line.strip()
    match = _SENDIR_RE.match(text)
    if not match:
        return None
    module = int(match.group(1))
    port = int(match.group(2))
    command_id = int(match.group(3))
    frequency = int(match.group(4))
    repeat = int(match.group(5))
    offset = int(match.group(6))
    tail = match.group(7).strip()
    pairs: list[int] = []
    for part in tail.split(","):
        token = part.strip()
        if not token:
            continue
        # Compressed GC format uses letters; expand is out of scope — skip line.
        if not token.isdigit():
            return None
        pairs.append(int(token, 10))
    if len(pairs) < 2 or len(pairs) % 2:
        return None
    return {
        "module": module,
        "port": port,
        "command_id": command_id,
        "frequency": frequency,
        "repeat": repeat,
        "offset": offset,
        "pairs": pairs,
    }


def ir_connectors_from_modules(modules: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """Return (module, port) for every IR connector reported by getdevices."""
    connectors: list[tuple[int, int]] = []
    for entry in modules:
        if "IR" not in str(entry.get("type", "")):
            continue
        module = int(entry["module"])
        ports = max(1, int(entry.get("ports", 1)))
        for port in range(1, ports + 1):
            connectors.append((module, port))
    return connectors
