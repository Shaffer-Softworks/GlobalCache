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

from homeassistant.exceptions import ServiceValidationError

from .client import ItachClient, ItachError
from .const import (
    CONF_DEVICE_MODULES,
    CONF_COMMAND_TIMEOUT,
    CONF_CONN_PORT,
    CONF_CONNECT_TIMEOUT,
    CONF_DEFAULT_FREQ,
    CONF_DEFAULT_OFFSET,
    CONF_DEFAULT_REPEAT,
    CONF_FIXED_COMMAND_ID,
    CONF_HOST,
    CONF_ID_POLICY,
    CONF_MODULE,
    CONF_PORT,
    CONF_RELAY_ID,
    CONF_RELAYS,
    CONF_REMOTES,
    CONF_SERIAL_APPEND_CR,
    CONF_SERIAL_ID,
    serial_listen_enabled,
    CONF_SERIAL_PORTS,
    CONF_SERIAL_SETTINGS,
    DEFAULT_CARRIER_HZ,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_OFFSET,
    DEFAULT_REPEAT,
    DOMAIN,
    EVENT_IR_RECEIVED,
    EVENT_SERIAL_RECEIVED,
    ID_POLICY_AUTO,
    ID_POLICY_FIXED,
)
from .serial_session import SerialPortSession
from .device_util import ir_connectors_hint, module_accepts_ir
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
        self._serial_sessions: dict[str, SerialPortSession] = {}
        self._serial_session_keys: dict[str, tuple[Any, ...]] = {}
        self._serial_last_rx: dict[str, str] = {}
        relays = list(config_entry.options.get(CONF_RELAYS, []))
        poll_interval = timedelta(seconds=60) if relays else timedelta(minutes=10)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {self._opts[CONF_HOST]}",
            update_interval=poll_interval,
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
            "relay_states": {},
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
        relay_states: dict[str, bool] = {}
        for relay in self.config_entry.options.get(CONF_RELAYS, []):
            rid = str(relay.get(CONF_RELAY_ID, ""))
            if not rid:
                continue
            try:
                relay_states[rid] = await self.client.get_relay_state(
                    int(relay[CONF_MODULE]),
                    int(relay[CONF_CONN_PORT]),
                )
            except (TimeoutError, OSError, ItachError) as err:
                _LOGGER.debug("getstate failed for relay %s: %s", rid, err)
        out["relay_states"] = relay_states
        out["tcp_connected"] = self.client.is_connected
        return out

    async def async_shutdown(self) -> None:
        """Disconnect TCP and serial listeners."""
        if self._remove_ir_cb:
            self._remove_ir_cb()
            self._remove_ir_cb = None
        for session in self._serial_sessions.values():
            await session.stop()
        self._serial_sessions.clear()
        self._serial_session_keys.clear()
        await self.client.disconnect()

    @staticmethod
    def _serial_session_key(spec: dict[str, Any]) -> tuple[Any, ...]:
        return (
            int(spec[CONF_MODULE]),
            int(spec[CONF_CONN_PORT]),
            str(spec.get(CONF_SERIAL_SETTINGS, "")).strip(),
            bool(spec.get(CONF_SERIAL_APPEND_CR, True)),
        )

    def _make_serial_session(
        self, serial_id: str, spec: dict[str, Any]
    ) -> SerialPortSession:
        mod = int(spec[CONF_MODULE])
        port = int(spec[CONF_CONN_PORT])

        async def on_data(data: str, is_response: bool) -> None:
            await self._handle_serial_data(serial_id, spec, data, is_response)

        return SerialPortSession(
            self.client,
            serial_id=serial_id,
            module=mod,
            connector_port=port,
            settings=str(spec.get(CONF_SERIAL_SETTINGS, "")),
            append_cr=bool(spec.get(CONF_SERIAL_APPEND_CR, True)),
            connect_timeout=self._opts[CONF_CONNECT_TIMEOUT],
            command_timeout=self._opts[CONF_COMMAND_TIMEOUT],
            on_data=on_data,
        )

    async def _handle_serial_data(
        self,
        serial_id: str,
        spec: dict[str, Any],
        data: str,
        is_response: bool,
    ) -> None:
        self._serial_last_rx[serial_id] = data
        self.hass.bus.async_fire(
            EVENT_SERIAL_RECEIVED,
            {
                "config_entry_id": self.config_entry_id,
                "serial_id": serial_id,
                "module": int(spec[CONF_MODULE]),
                "port": int(spec[CONF_CONN_PORT]),
                "data": data,
                "is_response": is_response,
            },
        )
        self.async_update_listeners()

    async def async_start_serial_listeners(self) -> None:
        """Open persistent serial data connections for ports with listen enabled."""
        await self._sync_serial_sessions()

    async def _sync_serial_sessions(self) -> None:
        desired: dict[str, tuple[dict[str, Any], tuple[Any, ...]]] = {}
        for spec in self.config_entry.options.get(CONF_SERIAL_PORTS, []):
            serial_id = str(spec.get(CONF_SERIAL_ID, "")).strip()
            if not serial_id:
                continue
            if not serial_listen_enabled(spec):
                continue
            desired[serial_id] = (spec, self._serial_session_key(spec))

        for serial_id in list(self._serial_sessions):
            if serial_id not in desired:
                await self._serial_sessions.pop(serial_id).stop()
                self._serial_session_keys.pop(serial_id, None)

        for serial_id, (spec, key) in desired.items():
            if (
                serial_id in self._serial_sessions
                and self._serial_session_keys.get(serial_id) == key
            ):
                continue
            if serial_id in self._serial_sessions:
                await self._serial_sessions.pop(serial_id).stop()
            session = self._make_serial_session(serial_id, spec)
            self._serial_sessions[serial_id] = session
            self._serial_session_keys[serial_id] = key
            try:
                await session.start()
            except (TimeoutError, OSError, ItachError) as err:
                _LOGGER.warning(
                    "Serial listener %s failed to start: %s", serial_id, err
                )

    def get_serial_last_rx(self, serial_id: str) -> str | None:
        return self._serial_last_rx.get(serial_id)

    def get_serial_session(self, serial_id: str) -> SerialPortSession | None:
        return self._serial_sessions.get(serial_id)

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

    def _device_modules(self) -> list[dict[str, Any]]:
        raw = self.config_entry.data.get(CONF_DEVICE_MODULES, [])
        return list(raw) if isinstance(raw, list) else []

    def _ensure_ir_module(self, module: int, port: int) -> None:
        modules = self._device_modules()
        if module_accepts_ir(modules, module):
            return
        hint = ir_connectors_hint(modules)
        msg = (
            f"Module {module} port {port} is not an IR connector. {hint}"
        )
        raise ServiceValidationError(msg)

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
        self._ensure_ir_module(module, port)
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
        try:
            await self.client.send_sendir(
                module, port, cid, carrier, rep, off, pairs
            )
        except ItachError as err:
            raise ServiceValidationError(str(err)) from err
        _LOGGER.info(
            "IR sent %s:%s module %s port %s format=%s id=%s (%s pulse pairs)",
            self.host,
            self.port,
            module,
            port,
            fmt_n,
            cid,
            len(pairs) // 2,
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

    async def async_get_relay_state(self, module: int, port: int) -> bool:
        return await self.client.get_relay_state(module, port)

    async def async_set_relay_state(self, module: int, port: int, on: bool) -> bool:
        return await self.client.set_relay_state(module, port, on)

    async def async_get_serial_settings(self, module: int, port: int) -> str:
        return await self.client.get_serial_settings(module, port)

    async def async_set_serial_settings(
        self, module: int, port: int, settings: str
    ) -> str:
        return await self.client.set_serial_settings(module, port, settings)

    async def async_send_serial(
        self,
        spec: dict[str, Any],
        payload: str,
        *,
        append_cr: bool | None = None,
        wait_response: bool = True,
    ) -> str:
        """Send on the serial data port (listener session or one-shot)."""
        serial_id = str(spec.get(CONF_SERIAL_ID, "")).strip()
        listen = serial_listen_enabled(spec)
        session = self._serial_sessions.get(serial_id) if listen else None
        if session is not None:
            return await session.send(
                payload, append_cr=append_cr, wait_response=wait_response
            )
        mod = int(spec[CONF_MODULE])
        port = int(spec[CONF_CONN_PORT])
        settings = str(spec.get(CONF_SERIAL_SETTINGS, "")).strip()
        if settings:
            await self.client.set_serial_settings(mod, port, settings)
        use_cr = (
            append_cr
            if append_cr is not None
            else bool(spec.get(CONF_SERIAL_APPEND_CR, True))
        )
        return await self.client.send_serial_payload(
            mod, payload, append_cr=use_cr
        )
