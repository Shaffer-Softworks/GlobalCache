"""Pure helpers for command metadata (no Home Assistant imports)."""

from __future__ import annotations

import json
from typing import Any

from .const import (
    CONF_CMD_DATA,
    CONF_CMD_FORMAT,
    CONF_CMD_NAME,
    CONF_COMMANDS,
    CONF_SERIAL_PAYLOAD,
)


def parse_commands_json(raw: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Validate options-flow commands JSON. Returns (commands, None) or (None, error_message)."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as err:
        return None, str(err)
    if not isinstance(parsed, list):
        return None, "commands must be a JSON array"
    commands: list[dict[str, Any]] = []
    try:
        for item in parsed:
            if not isinstance(item, dict):
                raise ValueError("each command must be an object")
            name = str(item.get(CONF_CMD_NAME, "")).strip()
            data = str(item.get(CONF_CMD_DATA, "")).strip()
            fmt = str(item.get(CONF_CMD_FORMAT, "pronto")).strip().lower()
            allowed = {
                "pronto",
                "pronto_hex",
                "gc_pairs",
                "gc_sendir_tail",
                "full_sendir",
            }
            if fmt not in allowed:
                raise ValueError(
                    "format must be pronto, gc_pairs, or full_sendir "
                    "(aliases pronto_hex, gc_sendir_tail)"
                )
            if not name or not data:
                raise ValueError("name and data are required")
            cmd: dict[str, Any] = {
                CONF_CMD_NAME: name,
                CONF_CMD_DATA: data,
                CONF_CMD_FORMAT: fmt,
            }
            for key in ("freq", "repeat", "offset", "command_id"):
                if key in item and item[key] is not None:
                    cmd[key] = item[key]
            commands.append(cmd)
    except ValueError as err:
        return None, str(err)
    return commands, None


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


def parse_serial_commands_json(
    raw: str,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Validate serial preset JSON: [{name, payload}, ...]. Empty array is allowed."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as err:
        return None, str(err)
    if not isinstance(parsed, list):
        return None, "commands must be a JSON array"
    commands: list[dict[str, Any]] = []
    try:
        for item in parsed:
            if not isinstance(item, dict):
                raise ValueError("each command must be an object")
            name = str(item.get(CONF_CMD_NAME, "")).strip()
            payload = str(item.get(CONF_SERIAL_PAYLOAD, "")).strip()
            if not name or not payload:
                raise ValueError("name and payload are required")
            commands.append({CONF_CMD_NAME: name, CONF_SERIAL_PAYLOAD: payload})
    except ValueError as err:
        return None, str(err)
    return commands, None
