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
    """Parse ``device,module,port_count,TYPE`` lines from getdevices.

    Real GC/iTach firmware uses a space before TYPE (``device,1,3 IR``);
    normalize that to comma form before splitting.
    """
    modules: list[dict[str, Any]] = []
    for line in lines:
        normalized = _normalize_device_line(line)
        parts = [p.strip() for p in normalized.split(",")]
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


def list_ir_connectors(
    modules: list[dict[str, Any]],
    *,
    remotes: list[dict[str, Any]] | None = None,
    legacy_model: str | None = None,
) -> list[tuple[int, int]]:
    """IR (module, port) pairs from getdevices, else unique remote connectors, else 1:1.

    ``legacy_model`` accepts older entries that stored a raw getdevices line as model
    (e.g. ``device,1,3 IR``).
    """
    from .const import CONF_CONN_PORT, CONF_MODULE
    from .infrared_util import ir_connectors_from_modules

    connectors = ir_connectors_from_modules(modules)
    if not connectors and legacy_model:
        connectors = ir_connectors_from_modules(
            parse_getdevices_lines([_normalize_device_line(legacy_model)])
        )
    if connectors:
        return connectors
    seen: set[tuple[int, int]] = set()
    out: list[tuple[int, int]] = []
    for spec in remotes or []:
        try:
            key = (int(spec[CONF_MODULE]), int(spec[CONF_CONN_PORT]))
        except (KeyError, TypeError, ValueError):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out or [(1, 1)]


def _normalize_device_line(raw: str) -> str:
    """Turn ``device,1,3 IR`` into ``device,1,3,IR`` for parse_getdevices_lines."""
    text = raw.strip()
    if not text.lower().startswith("device,"):
        return text
    # "device,1,3 IR" / "device,1,3, IR"
    parts = [p.strip() for p in text.replace(",", " ").split() if p.strip()]
    if len(parts) >= 4 and parts[0].lower() == "device":
        return ",".join(parts[:4])
    return text


def gateway_via_device(entry_id: str) -> tuple[str, str]:
    """Single identifier tuple for the gateway hub device (not a set)."""
    from .const import DOMAIN

    return (DOMAIN, entry_id)


def gateway_device_identifiers(entry_id: str) -> set[tuple[str, str]]:
    """Primary hub device identifier set."""
    return {gateway_via_device(entry_id)}


def remote_device_identifiers(entry_id: str, remote_id: str) -> set[tuple[str, str, str]]:
    """Per-remote subdevice under the gateway."""
    from .const import DOMAIN

    return {(DOMAIN, entry_id, remote_id)}


def async_register_remote_devices(
    hass: HomeAssistant, entry: ConfigEntry, *, via_device_id: str
) -> None:
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
            via_device_id=via_device_id,
        )


def async_cleanup_stale_remote_devices(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Remove HA devices for remotes no longer present in integration options."""
    from homeassistant.helpers import device_registry as dr

    from .const import CONF_REMOTE_ID, CONF_REMOTES, DOMAIN

    registry = dr.async_get(hass)
    entry_id = entry.entry_id
    active_remote_ids = {
        str(spec.get(CONF_REMOTE_ID, "")).strip()
        for spec in entry.options.get(CONF_REMOTES, [])
        if str(spec.get(CONF_REMOTE_ID, "")).strip()
    }
    for device in list(dr.async_entries_for_config_entry(registry, entry_id)):
        remote_id: str | None = None
        for ident in device.identifiers:
            if (
                len(ident) == 3
                and ident[0] == DOMAIN
                and ident[1] == entry_id
            ):
                remote_id = str(ident[2])
                break
        if remote_id is None or remote_id in active_remote_ids:
            continue
        registry.async_remove_device(device.id)


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
