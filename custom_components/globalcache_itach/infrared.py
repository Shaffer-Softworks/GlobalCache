"""Home Assistant infrared emitter/receiver platform for Global Caché IR ports."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.infrared import (
    InfraredCommand,
    InfraredEmitterEntity,
    InfraredReceivedSignal,
    InfraredReceiverEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_MODULES, CONF_REMOTES, DOMAIN, MANUFACTURER
from .client import ItachError
from .coordinator import ItachCoordinator
from .device_util import list_ir_connectors
from .entity_registry_util import (
    infrared_emitter_unique_id,
    infrared_receiver_unique_id,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up infrared emitter and receiver entities for each IR connector."""
    coordinator: ItachCoordinator = hass.data[DOMAIN][entry.entry_id]
    modules = list(entry.data.get(CONF_DEVICE_MODULES, []) or [])
    remotes = list(entry.options.get(CONF_REMOTES, []) or [])
    connectors = list_ir_connectors(modules, remotes=remotes)
    entities: list[CoordinatorEntity] = []
    for module, port in connectors:
        entities.append(ItachInfraredEmitter(coordinator, entry, module, port))
        entities.append(ItachInfraredReceiver(coordinator, entry, module, port))
    async_add_entities(entities)


class _ItachInfraredBase(CoordinatorEntity[ItachCoordinator]):
    """Shared device info for infrared hardware entities on the gateway."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ItachCoordinator,
        entry: ConfigEntry,
        module: int,
        port: int,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._module = module
        self._port = port
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": MANUFACTURER,
            "model": entry.data.get("model") or "iTach",
            "sw_version": entry.data.get("firmware") or "",
        }

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.client.is_connected


class ItachInfraredEmitter(_ItachInfraredBase, InfraredEmitterEntity):
    """IR transmitter for one Global Caché IR connector (module:port)."""

    def __init__(
        self,
        coordinator: ItachCoordinator,
        entry: ConfigEntry,
        module: int,
        port: int,
    ) -> None:
        super().__init__(coordinator, entry, module, port)
        self._attr_unique_id = infrared_emitter_unique_id(
            entry.entry_id, module, port
        )
        self._attr_name = f"IR emitter {module}:{port}"

    async def async_send_command(self, command: InfraredCommand) -> None:
        """Send an infrared-protocols command via sendir."""
        try:
            timings = list(command.get_raw_timings())
            freq = int(getattr(command, "modulation", 0) or 0)
            await self.coordinator.async_send_infrared_timings(
                self._module,
                self._port,
                timings,
                freq,
            )
        except (HomeAssistantError, OSError, TimeoutError, ValueError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="send_infrared_failed",
                translation_placeholders={
                    "connector": f"{self._module}:{self._port}",
                    "error": str(err),
                },
            ) from err


class ItachInfraredReceiver(_ItachInfraredBase, InfraredReceiverEntity):
    """IR receiver for one connector; disabled by default (RECEIVER mode)."""

    # Enabling this entity sets set_IR RECEIVER + receiveIR; keep off so blaster
    # ports are not switched until the user opts in.
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: ItachCoordinator,
        entry: ConfigEntry,
        module: int,
        port: int,
    ) -> None:
        super().__init__(coordinator, entry, module, port)
        self._attr_unique_id = infrared_receiver_unique_id(
            entry.entry_id, module, port
        )
        self._attr_name = f"IR receiver {module}:{port}"
        self._remove_rx: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._remove_rx = self.coordinator.register_infrared_receiver(
            self._module, self._port, self._on_timings
        )
        try:
            await self.coordinator.async_enable_infrared_receive(
                self._module, self._port
            )
        except (OSError, TimeoutError, HomeAssistantError, ItachError) as err:
            _LOGGER.warning(
                "Could not enable IR receive on %s:%s: %s",
                self._module,
                self._port,
                err,
            )

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_rx is not None:
            self._remove_rx()
            self._remove_rx = None
        await self.coordinator.async_disable_infrared_receive(
            self._module, self._port
        )
        await super().async_will_remove_from_hass()

    @callback
    def _on_timings(self, timings: list[int], modulation: int | None) -> None:
        self._handle_received_signal(
            InfraredReceivedSignal(timings=timings, modulation=modulation)
        )
