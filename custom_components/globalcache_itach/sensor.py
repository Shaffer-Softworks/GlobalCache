"""Diagnostic sensors for the iTach gateway."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_REMOTES, DOMAIN, MANUFACTURER
from .coordinator import ItachCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ItachCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            ItachGatewayDiagnosticSensor(coordinator, entry),
            ItachLastPollSensor(coordinator, entry),
            ItachRemoteCountSensor(coordinator, entry),
        ]
    )


class ItachGatewayDiagnosticSensor(
    CoordinatorEntity[ItachCoordinator], SensorEntity
):
    """Exposes last getdevices / getversion poll (entity off by default)."""

    _attr_has_entity_name = True
    _attr_translation_key = "gateway_diagnostics"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: ItachCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_gateway_diagnostics"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": MANUFACTURER,
            "model": entry.data.get("model") or "iTach",
            "sw_version": entry.data.get("firmware") or "",
        }

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data or {}
        if data.get("version_line"):
            return "ok"
        if data.get("devices_lines"):
            return "partial"
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        lines = data.get("devices_lines") or []
        return {
            "host": data.get("host") or self._entry.data.get("host"),
            "tcp_port": data.get("tcp_port") or self._entry.data.get("port"),
            "tcp_connected": data.get("tcp_connected"),
            "poll_at": data.get("poll_at").isoformat()
            if isinstance(data.get("poll_at"), datetime)
            else None,
            "configured_remotes": len(self._entry.options.get(CONF_REMOTES, [])),
            "getdevices_response": "\n".join(lines) if lines else None,
            "getversion_line": data.get("version_line"),
        }


class ItachLastPollSensor(CoordinatorEntity[ItachCoordinator], SensorEntity):
    """When the coordinator last finished talking to the gateway (UTC)."""

    _attr_has_entity_name = True
    _attr_translation_key = "last_poll"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: ItachCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_last_poll"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": MANUFACTURER,
            "model": entry.data.get("model") or "iTach",
            "sw_version": entry.data.get("firmware") or "",
        }

    @property
    def native_value(self) -> datetime | None:
        data = self.coordinator.data or {}
        ts = data.get("poll_at")
        return ts if isinstance(ts, datetime) else None


class ItachRemoteCountSensor(CoordinatorEntity[ItachCoordinator], SensorEntity):
    """Number of configured remotes (from integration options)."""

    _attr_has_entity_name = True
    _attr_translation_key = "configured_remotes"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: ItachCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_remote_count"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": MANUFACTURER,
            "model": entry.data.get("model") or "iTach",
            "sw_version": entry.data.get("firmware") or "",
        }

    @property
    def native_value(self) -> int:
        return len(self._entry.options.get(CONF_REMOTES, []))
