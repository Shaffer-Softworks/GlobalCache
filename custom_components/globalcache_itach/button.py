"""Button entities for IR remote commands and serial presets."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_CMD_DATA,
    CONF_CMD_FORMAT,
    CONF_CMD_NAME,
    CONF_COMMANDS,
    CONF_CONN_PORT,
    CONF_IR_COUNT,
    CONF_MODULE,
    CONF_REMOTE_ID,
    CONF_REMOTE_NAME,
    CONF_REMOTES,
    CONF_SERIAL_COMMANDS,
    CONF_SERIAL_ID,
    CONF_SERIAL_NAME,
    CONF_SERIAL_PAYLOAD,
    CONF_SERIAL_PORTS,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import ItachCoordinator
from .device_util import (
    gateway_device_identifiers,
    gateway_via_device,
    remote_device_identifiers,
)
from .entity_registry_util import (
    active_remote_button_unique_ids,
    active_serial_button_unique_ids,
    async_remove_stale_entities,
    remote_button_unique_id,
    serial_button_unique_id,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ItachCoordinator = hass.data[DOMAIN][entry.entry_id]
    active = active_serial_button_unique_ids(entry) | active_remote_button_unique_ids(
        entry
    )
    async_remove_stale_entities(hass, entry, "button", active)
    entities: list[ButtonEntity] = []
    for spec in entry.options.get(CONF_REMOTES, []):
        remote_id = str(spec.get(CONF_REMOTE_ID, "")).strip()
        if not remote_id:
            continue
        mod = int(spec[CONF_MODULE])
        port = int(spec[CONF_CONN_PORT])
        base_repeat = int(spec.get(CONF_IR_COUNT, 1))
        for cmd in spec.get(CONF_COMMANDS, []):
            name = str(cmd.get(CONF_CMD_NAME, "")).strip()
            data = str(cmd.get(CONF_CMD_DATA, "")).strip()
            if not name or not data:
                continue
            entities.append(
                ItachRemoteCommandButton(
                    coordinator,
                    entry,
                    spec,
                    remote_id,
                    mod,
                    port,
                    base_repeat,
                    name,
                    cmd,
                )
            )
    for spec in entry.options.get(CONF_SERIAL_PORTS, []):
        port_name = str(spec.get(CONF_SERIAL_NAME, "Serial"))
        sid = spec[CONF_SERIAL_ID]
        mod = int(spec[CONF_MODULE])
        port = int(spec[CONF_CONN_PORT])
        for cmd in spec.get(CONF_SERIAL_COMMANDS, []):
            name = str(cmd.get(CONF_CMD_NAME, "")).strip()
            payload = str(cmd.get(CONF_SERIAL_PAYLOAD, "")).strip()
            if not name or not payload:
                continue
            entities.append(
                ItachSerialButton(
                    coordinator,
                    entry,
                    spec,
                    sid,
                    port_name,
                    mod,
                    port,
                    name,
                    payload,
                )
            )
    async_add_entities(entities)


class ItachRemoteCommandButton(CoordinatorEntity[ItachCoordinator], ButtonEntity):
    """Send one configured IR command (replaces activity dropdown on device page)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ItachCoordinator,
        entry: ConfigEntry,
        spec: dict[str, Any],
        remote_id: str,
        module: int,
        port: int,
        base_repeat: int,
        command_name: str,
        cmd: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._spec = spec
        self._module = module
        self._port = port
        self._base_repeat = base_repeat
        self._command_name = command_name
        self._cmd = cmd
        self._attr_unique_id = remote_button_unique_id(
            entry.entry_id, remote_id, command_name
        )
        self._attr_name = command_name
        self._attr_device_info = {
            "identifiers": remote_device_identifiers(entry.entry_id, remote_id),
            "name": str(spec.get(CONF_REMOTE_NAME, "Remote")),
            "manufacturer": MANUFACTURER,
            "model": "IR remote",
            "via_device": gateway_via_device(entry.entry_id),
        }

    async def async_press(self) -> None:
        _LOGGER.info("IR button pressed: %s", self._command_name)
        fmt = str(self._cmd.get(CONF_CMD_FORMAT, "pronto"))
        data = str(self._cmd[CONF_CMD_DATA])
        freq = self._cmd.get("freq")
        offset = self._cmd.get("offset")
        cid = self._cmd.get("command_id")
        try:
            await self.coordinator.async_send_ir_command(
                self._module,
                self._port,
                fmt,
                data,
                repeat=self._base_repeat,
                offset=int(offset) if offset is not None else None,
                frequency=int(freq) if freq is not None else None,
                command_id=int(cid) if cid is not None else None,
            )
        except (ServiceValidationError, OSError, TimeoutError) as err:
            raise HomeAssistantError(str(err)) from err


class ItachSerialButton(CoordinatorEntity[ItachCoordinator], ButtonEntity):
    """Fire a preconfigured serial payload."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ItachCoordinator,
        entry: ConfigEntry,
        spec: dict[str, Any],
        serial_id: str,
        port_name: str,
        module: int,
        port: int,
        command_name: str,
        payload: str,
    ) -> None:
        super().__init__(coordinator)
        self._spec = spec
        self._payload = payload
        self._attr_unique_id = serial_button_unique_id(
            entry.entry_id, str(serial_id), command_name
        )
        self._attr_name = f"{port_name} {command_name}"
        self._attr_device_info = {
            "identifiers": gateway_device_identifiers(entry.entry_id),
            "name": entry.title,
            "manufacturer": MANUFACTURER,
            "model": entry.data.get("model") or "iTach",
            "sw_version": entry.data.get("firmware") or "",
        }
        self._attr_extra_state_attributes = {
            "module": module,
            "port": port,
            "payload": payload,
        }

    async def async_press(self) -> None:
        await self.coordinator.async_send_serial(self._spec, self._payload)
