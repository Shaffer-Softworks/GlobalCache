"""Pure helpers for command metadata (no Home Assistant imports)."""

from __future__ import annotations

from typing import Any

from .const import CONF_CMD_NAME, CONF_COMMANDS


def activity_labels_from_spec(spec: dict[str, Any]) -> list[str]:
    """Labels for activity-style UIs: dedupe by case-insensitive name, stable sort."""
    seen: set[str] = set()
    labels: list[str] = []
    for cmd in spec.get(CONF_COMMANDS, []):
        raw = str(cmd.get(CONF_CMD_NAME, "")).strip()
        if not raw:
            continue
        key = raw.lower()
        if key in seen:
            continue
        seen.add(key)
        labels.append(raw)
    return sorted(labels, key=str.casefold)
