"""Diagnostic sensors (disabled by default)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import ItachCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ItachCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ItachGatewayDiagnosticSensor(coordinator, entry)])


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
            "getdevices_response": "\n".join(lines) if lines else None,
            "getversion_line": data.get("version_line"),
        }
