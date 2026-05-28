"""Serial text entity — send ad-hoc payloads and show last response."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_CONN_PORT,
    CONF_MODULE,
    CONF_SERIAL_ID,
    CONF_SERIAL_NAME,
    CONF_SERIAL_PORTS,
    CONF_SERIAL_SETTINGS,
    DOMAIN,
    EVENT_SERIAL_RECEIVED,
    MANUFACTURER,
)
from .coordinator import ItachCoordinator
from .entity_registry_util import (
    active_serial_text_unique_ids,
    async_remove_stale_entities,
    serial_text_unique_id,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ItachCoordinator = hass.data[DOMAIN][entry.entry_id]
    ports: list[dict[str, Any]] = list(entry.options.get(CONF_SERIAL_PORTS, []))
    async_remove_stale_entities(
        hass, entry, "text", active_serial_text_unique_ids(entry)
    )
    async_add_entities(
        ItachSerialTextEntity(coordinator, entry, spec) for spec in ports
    )


class ItachSerialTextEntity(CoordinatorEntity[ItachCoordinator], TextEntity):
    """Send arbitrary ASCII on a configured serial connector."""

    _attr_has_entity_name = True
    _attr_mode = TextMode.TEXT
    _attr_native_max = 255
    _attr_native_min = 0

    def __init__(
        self,
        coordinator: ItachCoordinator,
        entry: ConfigEntry,
        spec: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._spec = spec
        self._attr_unique_id = serial_text_unique_id(
            entry.entry_id, str(spec[CONF_SERIAL_ID])
        )
        self._attr_name = str(spec.get(CONF_SERIAL_NAME, "Serial"))
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": MANUFACTURER,
            "model": entry.data.get("model") or "iTach",
            "sw_version": entry.data.get("firmware") or "",
        }
        self._attr_native_value = ""
        self._serial_id = str(spec[CONF_SERIAL_ID])

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(
                EVENT_SERIAL_RECEIVED, self._handle_serial_received
            )
        )

    @callback
    def _handle_serial_received(self, event: Event) -> None:
        if event.data.get("config_entry_id") != self.coordinator.config_entry_id:
            return
        if event.data.get("serial_id") != self._serial_id:
            return
        if event.data.get("is_response"):
            return
        data = event.data.get("data")
        if isinstance(data, str) and data:
            self._attr_native_value = data
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "module": int(self._spec[CONF_MODULE]),
            "port": int(self._spec[CONF_CONN_PORT]),
            "serial_settings": self._spec.get(CONF_SERIAL_SETTINGS),
            "data_tcp_port": self.coordinator.port + int(self._spec[CONF_MODULE]),
        }

    async def async_set_value(self, value: str) -> None:
        try:
            response = await self.coordinator.async_send_serial(self._spec, value)
        except (TimeoutError, OSError) as err:
            _LOGGER.warning("Serial send failed for %s: %s", self.name, err)
            raise
        self._attr_native_value = response or value
        self.async_write_ha_state()
