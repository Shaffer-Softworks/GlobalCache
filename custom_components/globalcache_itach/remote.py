"""Remote platform."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from homeassistant.components.remote import (
    ATTR_NUM_REPEATS,
    DEFAULT_NUM_REPEATS,
    RemoteEntity,
    RemoteEntityFeature,
)

try:
    from homeassistant.components.remote import ATTR_ACTIVITY
except ImportError:  # pragma: no cover
    ATTR_ACTIVITY = "activity"
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .command_util import activity_labels_from_spec
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
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import ItachCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ItachCoordinator = hass.data[DOMAIN][entry.entry_id]
    remotes: list[dict[str, Any]] = list(entry.options.get(CONF_REMOTES, []))
    entities = [
        ItachRemoteEntity(coordinator, entry, spec) for spec in remotes
    ]
    async_add_entities(entities)


class ItachRemoteEntity(CoordinatorEntity[ItachCoordinator], RemoteEntity):
    """One configured remote (emitter + command table)."""

    def __init__(
        self,
        coordinator: ItachCoordinator,
        entry: ConfigEntry,
        spec: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        # RemoteEntityFeature only defines LEARN_COMMAND, DELETE_COMMAND, ACTIVITY
        # in core; TURN_ON/TURN_OFF are not part of it (they come from ToggleEntity).
        feats = RemoteEntityFeature(0)
        if hasattr(RemoteEntityFeature, "STOP"):
            feats |= RemoteEntityFeature.STOP
        activities = activity_labels_from_spec(spec)
        if activities:
            feats |= RemoteEntityFeature.ACTIVITY
        self._attr_supported_features = feats
        self._entry = entry
        self._spec = spec
        self._attr_unique_id = f"{entry.entry_id}_{spec[CONF_REMOTE_ID]}"
        self._attr_name = str(spec.get(CONF_REMOTE_NAME, "Remote"))
        self._attr_activity_list = activities if activities else None
        self._attr_current_activity = None
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": MANUFACTURER,
            "model": entry.data.get("model") or "iTach",
            "sw_version": entry.data.get("firmware") or "",
        }
        self._cmd_index: dict[str, dict[str, Any]] = {}
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        self._cmd_index.clear()
        for cmd in self._spec.get(CONF_COMMANDS, []):
            name = str(cmd.get(CONF_CMD_NAME, "")).strip().lower()
            if name:
                self._cmd_index[name] = cmd

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        num = int(kwargs.get(ATTR_NUM_REPEATS, DEFAULT_NUM_REPEATS) or 1)
        mod = int(self._spec[CONF_MODULE])
        port = int(self._spec[CONF_CONN_PORT])
        base_repeat = int(self._spec.get(CONF_IR_COUNT, 1))
        repeat_send = max(1, base_repeat * max(1, num))
        for cmd_name in command:
            key = cmd_name.strip().lower()
            cmd = self._cmd_index.get(key)
            if cmd is None:
                msg = f"Unknown command: {cmd_name}"
                raise ServiceValidationError(msg)
            fmt = str(cmd.get(CONF_CMD_FORMAT, "pronto"))
            data = str(cmd[CONF_CMD_DATA])
            freq = cmd.get("freq")
            offset = cmd.get("offset")
            cid = cmd.get("command_id")
            await self.coordinator.async_send_ir_command(
                mod,
                port,
                fmt,
                data,
                repeat=repeat_send,
                offset=int(offset) if offset is not None else None,
                frequency=int(freq) if freq is not None else None,
                command_id=int(cid) if cid is not None else None,
            )
            self._attr_current_activity = cmd_name.strip()
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        activity = kwargs.get(ATTR_ACTIVITY)
        if activity:
            await self.async_send_command([str(activity)])
            return
        if "on" in self._cmd_index:
            await self.async_send_command(["on"])
        else:
            _LOGGER.debug("No ON command for %s", self.name)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if "off" in self._cmd_index:
            await self.async_send_command(["off"])
        else:
            _LOGGER.debug("No OFF command for %s", self.name)

    async def async_stop(self) -> None:
        """Halt IR on this connector (``stopir``)."""
        mod = int(self._spec[CONF_MODULE])
        port = int(self._spec[CONF_CONN_PORT])
        await self.coordinator.client.send_raw(
            f"stopir,{mod}:{port}",
            end_on=lambda l: l.strip().lower().startswith("stopir"),
            timeout=10.0,
        )
