"""Relay switch platform (setstate / getstate)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_CONN_PORT,
    CONF_MODULE,
    CONF_RELAY_ID,
    CONF_RELAY_NAME,
    CONF_RELAYS,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import ItachCoordinator
from .entity_registry_util import (
    active_relay_unique_ids,
    async_remove_stale_entities,
    relay_unique_id,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ItachCoordinator = hass.data[DOMAIN][entry.entry_id]
    relays: list[dict[str, Any]] = list(entry.options.get(CONF_RELAYS, []))
    async_remove_stale_entities(
        hass, entry, "switch", active_relay_unique_ids(entry)
    )
    async_add_entities(
        ItachRelaySwitch(coordinator, entry, spec) for spec in relays
    )


class ItachRelaySwitch(CoordinatorEntity[ItachCoordinator], SwitchEntity):
    """One relay/contact on an iTach IP2CC or similar module."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ItachCoordinator,
        entry: ConfigEntry,
        spec: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._spec = spec
        self._relay_id = str(spec[CONF_RELAY_ID])
        self._module = int(spec[CONF_MODULE])
        self._port = int(spec[CONF_CONN_PORT])
        self._attr_unique_id = relay_unique_id(entry.entry_id, self._relay_id)
        self._attr_name = str(spec.get(CONF_RELAY_NAME, "Relay"))
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": MANUFACTURER,
            "model": entry.data.get("model") or "iTach",
            "sw_version": entry.data.get("firmware") or "",
        }
        self._attr_is_on = False
        self._apply_coordinator_state()

    def _apply_coordinator_state(self) -> None:
        states = (self.coordinator.data or {}).get("relay_states") or {}
        if self._relay_id in states:
            self._attr_is_on = bool(states[self._relay_id])

    @callback
    def _handle_coordinator_update(self) -> None:
        self._apply_coordinator_state()
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Unavailable when coordinator poll failed and we have no cached state."""
        if super().available and self._relay_id in (
            (self.coordinator.data or {}).get("relay_states") or {}
        ):
            return True
        return super().available

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = await self.coordinator.async_set_relay_state(
            self._module, self._port, True
        )
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = await self.coordinator.async_set_relay_state(
            self._module, self._port, False
        )
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
