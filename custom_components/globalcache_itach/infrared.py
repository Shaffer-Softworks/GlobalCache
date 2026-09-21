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
from homeassistant.components.infrared.entity import InfraredDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_MODULES, CONF_REMOTES, DOMAIN, MANUFACTURER
from .client import ItachError
from .coordinator import ItachCoordinator
from .device_util import list_ir_connectors, supports_ir_receiver
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
    """Set up infrared emitters for IR jacks; receivers only if in RECEIVER mode."""
    from .const import DEFAULT_COMMAND_TIMEOUT

    coordinator: ItachCoordinator = hass.data[DOMAIN][entry.entry_id]
    modules = list(entry.data.get(CONF_DEVICE_MODULES, []) or [])
    remotes = list(entry.options.get(CONF_REMOTES, []) or [])
    connectors = list_ir_connectors(
        modules,
        remotes=remotes,
        legacy_model=entry.data.get("model"),
    )
    # RECEIVER / receiveIR are Global Connect only (Unified TCP API §4.4.1).
    if supports_ir_receiver(entry.data.get("model")):
        receiver_ports = await _async_receiver_connectors(coordinator, connectors)
    else:
        receiver_ports = []

    entities: list[ItachInfraredEmitter | ItachInfraredReceiver] = []
    active_uids: set[str] = set()
    for module, port in connectors:
        entities.append(ItachInfraredEmitter(coordinator, entry, module, port))
        active_uids.add(infrared_emitter_unique_id(entry.entry_id, module, port))
    for module, port in receiver_ports:
        entities.append(ItachInfraredReceiver(coordinator, entry, module, port))
        active_uids.add(infrared_receiver_unique_id(entry.entry_id, module, port))

    # Drop previously created receivers when ports are not in RECEIVER mode
    # (or when the product does not support RECEIVER — e.g. iTach / GC-100 / Flex).
    async_remove_stale_infrared_entities(hass, entry, active_uids)
    async_add_entities(entities)


async def _async_receiver_connectors(
    coordinator: ItachCoordinator,
    connectors: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Return connectors currently configured for IR receive (set_IR RECEIVER)."""
    from .const import DEFAULT_COMMAND_TIMEOUT

    receivers: list[tuple[int, int]] = []
    timeout = min(8.0, float(DEFAULT_COMMAND_TIMEOUT))
    for module, port in connectors:
        try:
            lines = await coordinator.client.send_raw(
                f"get_IR,{module}:{port}",
                end_on=lambda l: l.strip().upper().startswith("IR,")
                or l.strip().lower().startswith("unknowncommand"),
                timeout=timeout,
            )
        except (TimeoutError, OSError, ItachError) as err:
            _LOGGER.debug("get_IR %s:%s failed: %s", module, port, err)
            continue
        mode = ""
        for line in lines:
            parts = [p.strip() for p in line.strip().split(",")]
            if len(parts) >= 3 and parts[0].upper() == "IR":
                mode = parts[2].upper()
                break
        if mode == "RECEIVER":
            receivers.append((module, port))
    if not receivers:
        _LOGGER.debug(
            "No IR ports in RECEIVER mode on %s "
            "(Global Connect: set_IR RECEIVER on a jack with an IR receiver cable)",
            coordinator.host,
        )
    return receivers


def async_remove_stale_infrared_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    active_unique_ids: set[str],
) -> None:
    """Remove infrared registry rows that are no longer active for this entry."""
    from homeassistant.helpers import entity_registry as er

    from .entity_registry_util import is_infrared_unique_id

    registry = er.async_get(hass)
    entry_id = entry.entry_id
    for entity_entry in er.async_entries_for_config_entry(registry, entry_id):
        uid = entity_entry.unique_id
        if not uid or uid in active_unique_ids:
            continue
        if not is_infrared_unique_id(entry_id, uid):
            continue
        _LOGGER.warning(
            "Removing unconfigured infrared entity %s (unique_id=%s)",
            entity_entry.entity_id,
            uid,
        )
        registry.async_remove(entity_entry.entity_id)


def _gateway_device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer=MANUFACTURER,
        model=entry.data.get("model") or "iTach",
        sw_version=entry.data.get("firmware") or "",
    )


class ItachInfraredEmitter(InfraredEmitterEntity):
    """IR transmitter for one Global Caché IR connector (module:port)."""

    _attr_has_entity_name = True
    _attr_device_class = InfraredDeviceClass.EMITTER
    _attr_icon = "mdi:led-on"
    _attr_translation_key = "infrared_emitter"

    def __init__(
        self,
        coordinator: ItachCoordinator,
        entry: ConfigEntry,
        module: int,
        port: int,
    ) -> None:
        """Initialize emitter for module:port."""
        super().__init__()
        self.coordinator = coordinator
        self._module = module
        self._port = port
        self._attr_unique_id = infrared_emitter_unique_id(
            entry.entry_id, module, port
        )
        self._attr_name = f"IR emitter {module}:{port}"
        self._attr_device_info = _gateway_device_info(entry)
        self._attr_available = coordinator.client.is_connected

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        available = self.coordinator.client.is_connected
        if available == self._attr_available:
            return
        self._attr_available = available
        self.async_write_ha_state()

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


class ItachInfraredReceiver(InfraredReceiverEntity):
    """IR receiver for one connector; disabled by default (RECEIVER mode)."""

    _attr_has_entity_name = True
    _attr_device_class = InfraredDeviceClass.RECEIVER
    _attr_icon = "mdi:led-off"
    _attr_translation_key = "infrared_receiver"
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
        """Initialize receiver for module:port."""
        super().__init__()
        self.coordinator = coordinator
        self._module = module
        self._port = port
        self._attr_unique_id = infrared_receiver_unique_id(
            entry.entry_id, module, port
        )
        self._attr_name = f"IR receiver {module}:{port}"
        self._attr_device_info = _gateway_device_info(entry)
        self._attr_available = coordinator.client.is_connected
        self._remove_rx: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )
        self._remove_rx = self.coordinator.register_infrared_receiver(
            self._module, self._port, self._on_timings
        )

        async def _enable() -> None:
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

        # Do not block infrared platform setup on TCP timeouts.
        self.hass.async_create_task(_enable())

    @callback
    def _handle_coordinator_update(self) -> None:
        available = self.coordinator.client.is_connected
        if available == self._attr_available:
            return
        self._attr_available = available
        self.async_write_ha_state()

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
