"""Pure helpers for command metadata (no Home Assistant imports)."""

from __future__ import annotations

import json
from typing import Any

from .const import (
    CONF_CMD_DATA,
    CONF_CMD_FORMAT,
    CONF_CMD_FREQ,
    CONF_CMD_ID,
    CONF_CMD_NAME,
    CONF_CMD_OFFSET,
    CONF_CMD_REPEAT,
    CONF_COMMANDS,
    CONF_SERIAL_PAYLOAD,
)

# Button entity ids slug the command name the same way (lowercase, spaces to "_").
_FORMAT_ALIASES = {
    "pronto": "pronto",
    "pronto_hex": "pronto",
    "gc_pairs": "gc_pairs",
    "gc_sendir_tail": "gc_pairs",
    "full_sendir": "full_sendir",
}
_OVERRIDE_RANGES: dict[str, tuple[int, int]] = {
    CONF_CMD_FREQ: (15000, 500000),
    CONF_CMD_REPEAT: (1, 50),
    CONF_CMD_OFFSET: (1, 383),
    CONF_CMD_ID: (0, 65535),
}

EMPTY_IR_COMMAND_SUMMARY = (
    "No commands yet. Add a command, import a JSON array, or save and add "
    "codes later with Learn IR."
)
EMPTY_SERIAL_COMMAND_SUMMARY = (
    "No preset buttons yet. Add one, import a JSON array, or save with none."
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


def rewrite_sendir_connector(sendir_line: str, module: int, port: int) -> str:
    """Replace module:port in a ``sendir,...`` line (learner always reports 1:1)."""
    text = sendir_line.strip()
    if not text.lower().startswith("sendir,"):
        msg = "Expected a sendir line"
        raise ValueError(msg)
    parts = text.split(",", 2)
    if len(parts) < 3:
        msg = "Malformed sendir line"
        raise ValueError(msg)
    return f"sendir,{int(module)}:{int(port)},{parts[2]}"


def ir_send_repeat(cmd: dict[str, Any], remote_repeat: int) -> int:
    """sendir repeat: the command override, or the remote's repeat when blank."""
    raw = cmd.get(CONF_CMD_REPEAT)
    if raw is None or raw == "":
        return int(remote_repeat)
    return int(raw)


def command_button_slug(name: str) -> str:
    """Slug used in remote and serial button unique ids."""
    return name.strip().lower().replace(" ", "_")


def command_json_error_key(message: str) -> str:
    """Map a parser message to an options-flow translation key."""
    text = message.lower()
    if "json array" in text:
        return "commands_not_array"
    if "format must be" in text:
        return "bad_format"
    if "name and" in text or "must be an object" in text:
        return "command_fields"
    return "invalid_json"


def canonical_ir_format(fmt: str) -> str | None:
    """Return a form format, or None when *fmt* is not a known IR format."""
    return _FORMAT_ALIASES.get(fmt.strip().lower())


def format_command_summary(
    commands: list[dict[str, Any]], *, include_format: bool
) -> str:
    """One line for the command-list step. Empty lists get a fixed hint."""
    if not commands:
        if include_format:
            return EMPTY_IR_COMMAND_SUMMARY
        return EMPTY_SERIAL_COMMAND_SUMMARY
    parts: list[str] = []
    for cmd in commands:
        name = _command_name(cmd) or "(unnamed)"
        if include_format:
            fmt = str(cmd.get(CONF_CMD_FORMAT, "pronto")).strip() or "pronto"
            parts.append(f"{name} ({fmt})")
        else:
            parts.append(name)
    return ", ".join(parts)


def coerce_optional_int(value: Any) -> tuple[int | None, str | None]:
    """Parse a blank-or-integer override. Blank is (None, None)."""
    if value is None or value == "":
        return None, None
    if isinstance(value, bool):
        return None, "command_override_range"
    if isinstance(value, int):
        return value, None
    if isinstance(value, float):
        if not value.is_integer():
            return None, "command_override_range"
        return int(value), None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None, None
        try:
            parsed = float(text)
        except ValueError:
            return None, "command_override_range"
        if not parsed.is_integer():
            return None, "command_override_range"
        return int(parsed), None
    return None, "command_override_range"


def ir_command_from_fields(
    name: str,
    data: str,
    fmt: str,
    freq: Any = None,
    repeat: Any = None,
    offset: Any = None,
    command_id: Any = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Build one IR command dict, or (None, translation key)."""
    clean_name = name.strip()
    clean_data = data.strip()
    if not clean_name or not clean_data:
        return None, "command_fields"
    canonical = canonical_ir_format(fmt)
    if canonical is None:
        return None, "bad_format"
    cmd: dict[str, Any] = {
        CONF_CMD_NAME: clean_name,
        CONF_CMD_DATA: clean_data,
        CONF_CMD_FORMAT: canonical,
    }
    for key, value in (
        (CONF_CMD_FREQ, freq),
        (CONF_CMD_REPEAT, repeat),
        (CONF_CMD_OFFSET, offset),
        (CONF_CMD_ID, command_id),
    ):
        number, err = coerce_optional_int(value)
        if err:
            return None, err
        if number is None:
            continue
        low, high = _OVERRIDE_RANGES[key]
        if not low <= number <= high:
            return None, "command_override_range"
        cmd[key] = number
    return cmd, None


def serial_command_from_fields(
    name: str, payload: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Build one serial preset, or (None, translation key)."""
    clean_name = name.strip()
    clean_payload = payload.strip()
    if not clean_name or not clean_payload:
        return None, "command_fields"
    return {CONF_CMD_NAME: clean_name, CONF_SERIAL_PAYLOAD: clean_payload}, None


def apply_command_change(
    commands: list[dict[str, Any]],
    new_cmd: dict[str, Any],
    *,
    original_index: int | None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Insert, replace, or rename one command.

    Adding the same name (case-insensitive) replaces that command. A different
    name is appended. Editing keeps the original position. A second command
    whose button slug matches another is rejected.
    """
    updated = [dict(cmd) for cmd in commands]
    if original_index is not None and not 0 <= original_index < len(updated):
        original_index = None
    new_name = _command_name(new_cmd)
    if not command_button_slug(new_name):
        return None, "command_fields"
    stored = dict(new_cmd)
    stored[CONF_CMD_NAME] = new_name

    if original_index is None:
        same_index = _find_command_index(updated, new_name)
        if same_index is not None:
            if _slug_taken(updated, new_name, skip_index=same_index):
                return None, "duplicate_command"
            updated[same_index] = stored
            return updated, None
        if _slug_taken(updated, new_name, skip_index=None):
            return None, "duplicate_command"
        updated.append(stored)
        return updated, None

    if _slug_taken(updated, new_name, skip_index=original_index):
        return None, "duplicate_command"
    updated[original_index] = stored
    return updated, None


def remove_command_at(commands: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    """Return a copy of *commands* without *index*."""
    updated = [dict(cmd) for cmd in commands]
    if 0 <= index < len(updated):
        del updated[index]
    return updated


def imported_commands_error(commands: list[dict[str, Any]]) -> str | None:
    """Reject an imported list whose button slugs collide."""
    seen: set[str] = set()
    for cmd in commands:
        slug = command_button_slug(_command_name(cmd))
        if not slug:
            return "command_fields"
        if slug in seen:
            return "duplicate_command"
        seen.add(slug)
    return None


def _command_name(cmd: dict[str, Any]) -> str:
    return str(cmd.get(CONF_CMD_NAME, "")).strip()


def _find_command_index(commands: list[dict[str, Any]], name: str) -> int | None:
    key = name.strip().lower()
    for index, cmd in enumerate(commands):
        if _command_name(cmd).lower() == key:
            return index
    return None


def _slug_taken(
    commands: list[dict[str, Any]], name: str, *, skip_index: int | None
) -> bool:
    slug = command_button_slug(name)
    if not slug:
        return False
    for index, cmd in enumerate(commands):
        if index == skip_index:
            continue
        if command_button_slug(_command_name(cmd)) == slug:
            return True
    return False
