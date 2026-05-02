"""Binary diagnostic sensors for the iTach gateway."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
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
    async_add_entities([ItachTcpConnectedBinarySensor(coordinator, entry)])


class ItachTcpConnectedBinarySensor(
    CoordinatorEntity[ItachCoordinator], BinarySensorEntity
):
    """On when a TCP session to the gateway was open at the end of the last poll."""

    _attr_has_entity_name = True
    _attr_translation_key = "tcp_connected"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: ItachCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_tcp_connected"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": MANUFACTURER,
            "model": entry.data.get("model") or "iTach",
            "sw_version": entry.data.get("firmware") or "",
        }

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if not data:
            return None
        return bool(data.get("tcp_connected"))
