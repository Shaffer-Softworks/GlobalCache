"""Parse getdevices output and map Global Caché error codes."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_UNKNOWN_HINTS: dict[str, str] = {
    "2": "Invalid module address.",
    "3": "Invalid module address.",
    "11": "Relay command sent to a connector that is not a relay module.",
    "21": (
        "IR command sent to a connector that is not an IR module. "
        "On GC-100-12, IR emitters are usually modules 4 and 5 (ports 1–3). "
        "Edit the remote under integration options and fix module/port."
    ),
    "23": "Command not supported on this module type.",
}

_UNKNOWN_RE = re.compile(r"^unknowncommand,?\s*(\d+)?", re.IGNORECASE)


def parse_getdevices_lines(lines: list[str]) -> list[dict[str, Any]]:
    """Parse ``device,module,port_count,TYPE`` lines from getdevices."""
    modules: list[dict[str, Any]] = []
    for line in lines:
        parts = [p.strip() for p in line.strip().split(",")]
        if len(parts) < 4 or parts[0].lower() != "device":
            continue
        try:
            module = int(parts[1])
            port_count = int(parts[2])
        except ValueError:
            continue
        kind = parts[3].upper()
        modules.append({"module": module, "ports": port_count, "type": kind})
    return modules


def infer_product_label(modules: list[dict[str, Any]], firmware: str) -> str:
    """Best-effort model string for the device registry."""
    types = {m["type"] for m in modules}
    if any("RELAY" in t for t in types) and any("SERIAL" in t for t in types):
        if "3.2-12" in firmware or sum(1 for m in modules if m["type"] == "IR") >= 2:
            return "GC-100-12"
        return "GC-100"
    for m in modules:
        if "IP2IR" in m["type"] or m["type"] == "IR":
            return "iTach IP2IR"
    return "iTach"


def module_accepts_ir(modules: list[dict[str, Any]], module: int) -> bool:
    if not modules:
        return True
    for entry in modules:
        if entry["module"] == module:
            return "IR" in str(entry["type"])
    return False


def default_ir_module(modules: list[dict[str, Any]]) -> int:
    for entry in modules:
        if "IR" in str(entry["type"]):
            return int(entry["module"])
    return 1


def ir_connectors_hint(modules: list[dict[str, Any]]) -> str:
    ir = [m for m in modules if "IR" in str(m["type"])]
    if not ir:
        return "Run get_devices to see which modules support IR on your hardware."
    bits = [
        f"module {m['module']} ({m['ports']} port(s), {m['type']})" for m in ir
    ]
    return "IR connectors on this device: " + ", ".join(bits) + "."


def gateway_via_device(entry_id: str) -> tuple[str, str]:
    """Single identifier tuple for ``via_device`` (not a set)."""
    from .const import DOMAIN

    return (DOMAIN, entry_id)


def gateway_device_identifiers(entry_id: str) -> set[tuple[str, str]]:
    """Primary hub device identifier set."""
    return {gateway_via_device(entry_id)}


def remote_device_identifiers(entry_id: str, remote_id: str) -> set[tuple[str, str, str]]:
    """Per-remote subdevice under the gateway."""
    from .const import DOMAIN

    return {(DOMAIN, entry_id, remote_id)}


def async_register_remote_devices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create one HA device per configured remote (via the gateway)."""
    from homeassistant.helpers import device_registry as dr

    from .const import CONF_REMOTE_ID, CONF_REMOTE_NAME, CONF_REMOTES, MANUFACTURER

    registry = dr.async_get(hass)
    entry_id = entry.entry_id
    for spec in entry.options.get(CONF_REMOTES, []):
        remote_id = str(spec.get(CONF_REMOTE_ID, "")).strip()
        if not remote_id:
            continue
        registry.async_get_or_create(
            config_entry_id=entry_id,
            identifiers=remote_device_identifiers(entry_id, remote_id),
            name=str(spec.get(CONF_REMOTE_NAME, "Remote")),
            manufacturer=MANUFACTURER,
            model="IR remote",
            via_device=gateway_via_device(entry_id),
        )


def format_unknown_command(line: str) -> str:
    """Turn ``unknowncommand,N`` into a short explanation for logs/UI."""
    text = line.strip()
    match = _UNKNOWN_RE.match(text)
    if not match:
        return text
    code = match.group(1)
    if code and code in _UNKNOWN_HINTS:
        return f"{text} — {_UNKNOWN_HINTS[code]}"
    return text
