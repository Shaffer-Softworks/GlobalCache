"""Coordinator / gateway facade for one iTach."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .client import ItachClient, ItachError
from .const import (
    CONF_COMMAND_TIMEOUT,
    CONF_CONNECT_TIMEOUT,
    CONF_DEFAULT_FREQ,
    CONF_DEFAULT_OFFSET,
    CONF_DEFAULT_REPEAT,
    CONF_FIXED_COMMAND_ID,
    CONF_HOST,
    CONF_ID_POLICY,
    CONF_PORT,
    CONF_REMOTES,
    DEFAULT_CARRIER_HZ,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_OFFSET,
    DEFAULT_REPEAT,
    DOMAIN,
    EVENT_IR_RECEIVED,
    ID_POLICY_AUTO,
    ID_POLICY_FIXED,
)
from .pronto import parse_gc_pair_string, pronto_to_gc_sendir_tail

_LOGGER = logging.getLogger(__name__)

_FORMAT_ALIASES: dict[str, str] = {
    "pronto_hex": "pronto",
    "gc_sendir_tail": "gc_pairs",
}


def normalize_command_format(fmt: str) -> str:
    """Map UI / doc aliases to internal format keys."""
    key = fmt.strip().lower()
    return _FORMAT_ALIASES.get(key, key)


def merged_options(data: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    """Merge config entry data and options with defaults."""
    out = {
        CONF_HOST: data[CONF_HOST],
        CONF_PORT: int(data[CONF_PORT]),
        CONF_CONNECT_TIMEOUT: float(
            options.get(CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT)
        ),
        CONF_COMMAND_TIMEOUT: float(
            options.get(CONF_COMMAND_TIMEOUT, DEFAULT_COMMAND_TIMEOUT)
        ),
        CONF_DEFAULT_FREQ: int(options.get(CONF_DEFAULT_FREQ, DEFAULT_CARRIER_HZ)),
        CONF_DEFAULT_REPEAT: int(options.get(CONF_DEFAULT_REPEAT, DEFAULT_REPEAT)),
        CONF_DEFAULT_OFFSET: int(options.get(CONF_DEFAULT_OFFSET, DEFAULT_OFFSET)),
        CONF_ID_POLICY: options.get(CONF_ID_POLICY, ID_POLICY_AUTO),
        CONF_FIXED_COMMAND_ID: int(options.get(CONF_FIXED_COMMAND_ID, 1)),
    }
    return out


class ItachCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Keeps TCP client, issues sendir, occasional getdevices refresh."""

    config_entry_id: str

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        data: dict[str, Any],
        options: dict[str, Any],
    ) -> None:
        self.config_entry = config_entry
        self.config_entry_id = config_entry.entry_id
        self._data = data
        self._opts = merged_options(data, options)
        self.client = ItachClient(
            self._opts[CONF_HOST],
            self._opts[CONF_PORT],
            connect_timeout=self._opts[CONF_CONNECT_TIMEOUT],
            command_timeout=self._opts[CONF_COMMAND_TIMEOUT],
        )
        self._id_lock = asyncio.Lock()
        self._next_id = 1
        self._remove_ir_cb: Callable[[], None] | None = None
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {self._opts[CONF_HOST]}",
            update_interval=timedelta(minutes=10),
            config_entry=config_entry,
        )

    @property
    def host(self) -> str:
        return str(self._opts[CONF_HOST])

    @property
    def port(self) -> int:
        return int(self._opts[CONF_PORT])

    async def _async_update_data(self) -> dict[str, Any]:
        """Poll module list and firmware for diagnostics (not used per IR send)."""
        out: dict[str, Any] = {
            "devices_lines": [],
            "version_line": None,
            "poll_at": dt_util.utcnow(),
            "tcp_connected": False,
            "remote_count": len(self.config_entry.options.get(CONF_REMOTES, [])),
            "host": self._opts[CONF_HOST],
            "tcp_port": self._opts[CONF_PORT],
        }
        try:
            out["devices_lines"] = await self.client.getdevices()
        except (TimeoutError, OSError, ItachError) as err:
            _LOGGER.warning("getdevices failed: %s", err)
        try:
            vlines = await self.client.send_raw(
                "getversion,0",
                end_on=lambda x: x.strip().lower().startswith("version,")
                or x.strip().lower().startswith("unknowncommand"),
                timeout=min(8.0, self._opts[CONF_COMMAND_TIMEOUT]),
            )
            if vlines:
                out["version_line"] = vlines[-1].strip()
        except (TimeoutError, OSError, ItachError) as err:
            _LOGGER.debug("getversion probe failed: %s", err)
        out["tcp_connected"] = self.client.is_connected
        return out

    async def async_shutdown(self) -> None:
        """Disconnect TCP."""
        if self._remove_ir_cb:
            self._remove_ir_cb()
            self._remove_ir_cb = None
        await self.client.disconnect()

    async def async_next_command_id(self) -> int:
        async with self._id_lock:
            cid = self._next_id
            self._next_id = self._next_id + 1
            if self._next_id > 65535:
                self._next_id = 1
            return cid

    async def resolve_command_id(self) -> int:
        if self._opts[CONF_ID_POLICY] == ID_POLICY_FIXED:
            return int(self._opts[CONF_FIXED_COMMAND_ID])
        return await self.async_next_command_id()

    def build_pulse_payload(
        self,
        fmt: str,
        data: str,
        *,
        override_freq: int | None = None,
    ) -> tuple[int, list[int]]:
        fmt_n = normalize_command_format(fmt)
        if fmt_n == "pronto":
            carrier, pairs = pronto_to_gc_sendir_tail(data)
            if override_freq is not None:
                carrier = override_freq
            return carrier, pairs
        if fmt_n == "gc_pairs":
            carrier = override_freq or int(self._opts[CONF_DEFAULT_FREQ])
            return carrier, parse_gc_pair_string(data)
        msg = f"Unknown command format: {fmt}"
        raise ValueError(msg)

    async def async_send_ir_command(
        self,
        module: int,
        port: int,
        fmt: str,
        data: str,
        *,
        repeat: int | None = None,
        offset: int | None = None,
        frequency: int | None = None,
        command_id: int | None = None,
    ) -> None:
        fmt_n = normalize_command_format(fmt)
        if fmt_n == "full_sendir":
            await self.client.send_full_sendir(data)
            return
        carrier, pairs = self.build_pulse_payload(fmt_n, data, override_freq=frequency)
        rep = repeat if repeat is not None else int(self._opts[CONF_DEFAULT_REPEAT])
        off = offset if offset is not None else int(self._opts[CONF_DEFAULT_OFFSET])
        cid = (
            command_id
            if command_id is not None
            else await self.resolve_command_id()
        )
        await self.client.send_sendir(
            module, port, cid, carrier, rep, off, pairs
        )

    def enable_ir_receive_events(self) -> None:
        """Fire HA events for unsolicited IR lines (receiveIR / learner output)."""

        async def _cb(line: str) -> None:
            self.hass.bus.async_fire(
                EVENT_IR_RECEIVED,
                {"config_entry_id": self.config_entry_id, "line": line},
            )

        if self._remove_ir_cb is None:
            self._remove_ir_cb = self.client.add_ir_received_callback(_cb)
