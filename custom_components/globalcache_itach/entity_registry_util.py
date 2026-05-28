"""Remove entity registry rows that are no longer in integration options."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_CMD_DATA,
    CONF_CMD_NAME,
    CONF_COMMANDS,
    CONF_RELAY_ID,
    CONF_RELAYS,
    CONF_REMOTE_ID,
    CONF_REMOTES,
    CONF_SERIAL_COMMANDS,
    CONF_SERIAL_ID,
    serial_listen_enabled,
    CONF_SERIAL_PAYLOAD,
    CONF_SERIAL_PORTS,
)

_LOGGER = logging.getLogger(__name__)

# Fixed diagnostic/gateway entities (not options-driven).
_GATEWAY_UNIQUE_SUFFIXES: frozenset[str] = frozenset(
    {
        "_gateway_diagnostics",
        "_last_poll",
        "_remote_count",
        "_tcp_connected",
    }
)


def is_options_managed_unique_id(entry_id: str, unique_id: str | None) -> bool:
    """True for remotes/relays/serial entities created from integration options."""
    if not unique_id or not unique_id.startswith(f"{entry_id}_"):
        return False
    if any(unique_id.endswith(suffix) for suffix in _GATEWAY_UNIQUE_SUFFIXES):
        return False
    if unique_id.startswith(f"{entry_id}_relay_"):
        return True
    if unique_id.startswith(f"{entry_id}_serial_"):
        return True
    # Remote: {entry_id}_{remote_id}
    return True


def unique_id_matches_platform(entry_id: str, unique_id: str, platform: str) -> bool:
    """Match HA component platform name to our unique_id layout."""
    if not is_options_managed_unique_id(entry_id, unique_id):
        return False
    if platform == "remote":
        return (
            "_relay_" not in unique_id
            and "_serial_" not in unique_id
            and "_btn_" not in unique_id
        )
    if platform in ("switch", "relay"):
        return unique_id.startswith(f"{entry_id}_relay_")
    if platform == "text":
        return (
            unique_id.startswith(f"{entry_id}_serial_")
            and "_btn_" not in unique_id
            and not unique_id.endswith("_rx")
        )
    if platform == "button":
        return "_btn_" in unique_id and unique_id.startswith(f"{entry_id}_")
    if platform == "sensor":
        return unique_id.endswith("_rx") and unique_id.startswith(
            f"{entry_id}_serial_"
        )
    return False


def async_remove_stale_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    platform: str,
    active_unique_ids: set[str],
) -> None:
    """Drop registry entities for this platform that are not in *active_unique_ids*."""
    registry = er.async_get(hass)
    entry_id = entry.entry_id
    for entity_entry in er.async_entries_for_config_entry(registry, entry_id):
        uid = entity_entry.unique_id
        if not uid or uid in active_unique_ids:
            continue
        if not unique_id_matches_platform(entry_id, uid, platform):
            continue
        _LOGGER.info(
            "Removing unconfigured %s entity %s (unique_id=%s)",
            platform,
            entity_entry.entity_id,
            uid,
        )
        registry.async_remove(entity_entry.entity_id)


def remote_unique_id(entry_id: str, remote_id: str) -> str:
    return f"{entry_id}_{remote_id}"


def relay_unique_id(entry_id: str, relay_id: str) -> str:
    return f"{entry_id}_relay_{relay_id}"


def serial_text_unique_id(entry_id: str, serial_id: str) -> str:
    return f"{entry_id}_serial_{serial_id}"


def serial_button_unique_id(entry_id: str, serial_id: str, command_name: str) -> str:
    slug = command_name.strip().lower().replace(" ", "_")
    return f"{entry_id}_serial_{serial_id}_btn_{slug}"


def remote_button_unique_id(
    entry_id: str, remote_id: str, command_name: str
) -> str:
    slug = command_name.strip().lower().replace(" ", "_")
    return f"{entry_id}_{remote_id}_btn_{slug}"


def serial_rx_unique_id(entry_id: str, serial_id: str) -> str:
    return f"{entry_id}_serial_{serial_id}_rx"


def active_remote_unique_ids(entry: ConfigEntry) -> set[str]:
    return {
        remote_unique_id(entry.entry_id, str(spec[CONF_REMOTE_ID]))
        for spec in entry.options.get(CONF_REMOTES, [])
    }


def active_relay_unique_ids(entry: ConfigEntry) -> set[str]:
    return {
        relay_unique_id(entry.entry_id, str(spec[CONF_RELAY_ID]))
        for spec in entry.options.get(CONF_RELAYS, [])
    }


def active_serial_text_unique_ids(entry: ConfigEntry) -> set[str]:
    return {
        serial_text_unique_id(entry.entry_id, str(spec[CONF_SERIAL_ID]))
        for spec in entry.options.get(CONF_SERIAL_PORTS, [])
    }


def active_serial_rx_unique_ids(entry: ConfigEntry) -> set[str]:
    return {
        serial_rx_unique_id(entry.entry_id, str(spec[CONF_SERIAL_ID]))
        for spec in entry.options.get(CONF_SERIAL_PORTS, [])
        if serial_listen_enabled(spec)
        and str(spec.get(CONF_SERIAL_ID, "")).strip()
    }


def active_remote_button_unique_ids(entry: ConfigEntry) -> set[str]:
    ids: set[str] = set()
    for spec in entry.options.get(CONF_REMOTES, []):
        rid = str(spec.get(CONF_REMOTE_ID, "")).strip()
        if not rid:
            continue
        for cmd in spec.get(CONF_COMMANDS, []):
            name = str(cmd.get(CONF_CMD_NAME, "")).strip()
            data = str(cmd.get(CONF_CMD_DATA, "")).strip()
            if name and data:
                ids.add(remote_button_unique_id(entry.entry_id, rid, name))
    return ids


def active_serial_button_unique_ids(entry: ConfigEntry) -> set[str]:
    ids: set[str] = set()
    for spec in entry.options.get(CONF_SERIAL_PORTS, []):
        sid = str(spec[CONF_SERIAL_ID])
        for cmd in spec.get(CONF_SERIAL_COMMANDS, []):
            name = str(cmd.get(CONF_CMD_NAME, "")).strip()
            payload = str(cmd.get(CONF_SERIAL_PAYLOAD, "")).strip()
            if name and payload:
                ids.add(serial_button_unique_id(entry.entry_id, sid, name))
    return ids


def all_active_configured_unique_ids(entry: ConfigEntry) -> set[str]:
    """Union of unique_ids for remotes, relays, and serial entities from options."""
    active: set[str] = set()
    active |= active_relay_unique_ids(entry)
    active |= active_serial_text_unique_ids(entry)
    active |= active_serial_button_unique_ids(entry)
    active |= active_remote_button_unique_ids(entry)
    active |= active_serial_rx_unique_ids(entry)
    return active


def async_cleanup_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove options-driven entities that are no longer configured."""
    active = all_active_configured_unique_ids(entry)
    registry = er.async_get(hass)
    entry_id = entry.entry_id
    for entity_entry in er.async_entries_for_config_entry(registry, entry_id):
        uid = entity_entry.unique_id
        if not uid or uid in active:
            continue
        if not is_options_managed_unique_id(entry_id, uid):
            continue
        _LOGGER.info(
            "Removing unconfigured entity %s (unique_id=%s)",
            entity_entry.entity_id,
            uid,
        )
        registry.async_remove(entity_entry.entity_id)
