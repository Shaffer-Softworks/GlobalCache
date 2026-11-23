"""Config and options flow."""

from __future__ import annotations

import copy
import json
import logging
import uuid
from typing import Any, Self

import voluptuous as vol
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .client import ItachClient, ItachError
from .command_util import (
    apply_command_change,
    canonical_ir_format,
    command_json_error_key,
    format_command_summary,
    imported_commands_error,
    ir_command_from_fields,
    parse_commands_json,
    parse_serial_commands_json,
    remove_command_at,
    rewrite_sendir_connector,
    serial_command_from_fields,
)
from .device_util import (
    default_ir_module,
    firmware_from_getversion_lines,
    infer_product_label,
    ir_connectors_hint,
    is_getversion_reply,
    parse_getdevices_lines,
    prefer_discovery_model,
)
from .discovery import (
    BeaconInfo,
    async_scan_beacons,
    normalize_unique_id,
)
from .options_menu import (
    IR_FORMAT_LABELS,
    build_command_list_menu,
    build_options_init_menu,
)
from .const import (
    _LEGACY_SERIAL_LISTEN,
    CONF_CMD_DATA,
    CONF_CMD_FORMAT,
    CONF_CMD_FREQ,
    CONF_CMD_ID,
    CONF_CMD_NAME,
    CONF_CMD_OFFSET,
    CONF_CMD_REPEAT,
    CONF_COMMAND_TIMEOUT,
    CONF_COMMANDS,
    CONF_CONN_PORT,
    CONF_CONNECT_TIMEOUT,
    CONF_DEFAULT_FREQ,
    CONF_DEFAULT_OFFSET,
    CONF_DEFAULT_REPEAT,
    CONF_DEVICE_MODULES,
    CONF_DEVICE_NAME,
    CONF_FIXED_COMMAND_ID,
    CONF_HOST,
    CONF_ID_POLICY,
    CONF_IR_COUNT,
    CONF_MODULE,
    CONF_PORT,
    CONF_REMOTE_ID,
    CONF_REMOTE_NAME,
    CONF_RELAY_ID,
    CONF_RELAY_NAME,
    CONF_RELAYS,
    CONF_REMOTES,
    CONF_SERIAL_APPEND_CR,
    CONF_SERIAL_COMMANDS,
    CONF_SERIAL_ID,
    CONF_SERIAL_LISTEN,
    CONF_SERIAL_NAME,
    CONF_SERIAL_PORTS,
    CONF_SERIAL_PAYLOAD,
    CONF_SERIAL_SETTINGS,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_SERIAL_SETTINGS,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_CARRIER_HZ,
    DEFAULT_OFFSET,
    DEFAULT_PORT,
    DEFAULT_REPEAT,
    DOMAIN,
    ID_POLICY_AUTO,
    ID_POLICY_FIXED,
)

_LOGGER = logging.getLogger(__name__)

_MULTILINE = selector.TextSelector(selector.TextSelectorConfig(multiline=True))

_IR_IMPORT_EXAMPLE = [
    {
        CONF_CMD_NAME: "PowerOn",
        CONF_CMD_FORMAT: "gc_pairs",
        CONF_CMD_DATA: "172,172,22,64,22,64",
    },
    {
        CONF_CMD_NAME: "PowerOff",
        CONF_CMD_FORMAT: "gc_pairs",
        CONF_CMD_DATA: "173,173,21,65,21,65",
    },
]
_SERIAL_IMPORT_EXAMPLE = [
    {CONF_CMD_NAME: "Status", CONF_SERIAL_PAYLOAD: "STATUS"},
    {CONF_CMD_NAME: "Power", CONF_SERIAL_PAYLOAD: "PWR ON"},
]


def _number_selector(
    *,
    minimum: float,
    maximum: float,
    step: float,
    unit: str | None = None,
) -> selector.NumberSelector:
    if unit is None:
        return selector.NumberSelector(
            selector.NumberSelectorConfig(
                mode=selector.NumberSelectorMode.BOX,
                min=minimum,
                max=maximum,
                step=step,
            )
        )
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            mode=selector.NumberSelectorMode.BOX,
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
        )
    )


def _optional_number(
    key: str,
    suggested: Any,
    *,
    minimum: float,
    maximum: float,
    step: float,
    unit: str | None = None,
) -> tuple[vol.Marker, selector.NumberSelector]:
    """Optional number that stays blank when cleared (suggested_value, not default)."""
    kwargs: dict[str, Any] = {}
    if suggested is not None and suggested != "":
        kwargs["description"] = {"suggested_value": suggested}
    return (
        vol.Optional(key, **kwargs),
        _number_selector(minimum=minimum, maximum=maximum, step=step, unit=unit),
    )


def _suggested(user_input: dict[str, Any] | None, existing: dict[str, Any] | None, key: str) -> Any:
    if user_input is not None:
        return user_input.get(key)
    if not existing:
        return None
    return existing.get(key)


def _ir_command_schema(
    user_input: dict[str, Any] | None, existing: dict[str, Any] | None
) -> vol.Schema:
    source = user_input if user_input is not None else (existing or {})
    raw_format = str(source.get(CONF_CMD_FORMAT, "pronto"))
    fmt = canonical_ir_format(raw_format) or "pronto"
    freq_key, freq_sel = _optional_number(
        CONF_CMD_FREQ,
        _suggested(user_input, existing, CONF_CMD_FREQ),
        minimum=15000,
        maximum=500000,
        step=1000,
        unit="Hz",
    )
    repeat_key, repeat_sel = _optional_number(
        CONF_CMD_REPEAT,
        _suggested(user_input, existing, CONF_CMD_REPEAT),
        minimum=1,
        maximum=50,
        step=1,
    )
    offset_key, offset_sel = _optional_number(
        CONF_CMD_OFFSET,
        _suggested(user_input, existing, CONF_CMD_OFFSET),
        minimum=1,
        maximum=383,
        step=2,
    )
    id_key, id_sel = _optional_number(
        CONF_CMD_ID,
        _suggested(user_input, existing, CONF_CMD_ID),
        minimum=0,
        maximum=65535,
        step=1,
    )
    return vol.Schema(
        {
            vol.Required(
                CONF_CMD_NAME, default=str(source.get(CONF_CMD_NAME, ""))
            ): str,
            vol.Required(CONF_CMD_FORMAT, default=fmt): vol.In(IR_FORMAT_LABELS),
            vol.Required(
                CONF_CMD_DATA, default=str(source.get(CONF_CMD_DATA, ""))
            ): _MULTILINE,
            freq_key: freq_sel,
            repeat_key: repeat_sel,
            offset_key: offset_sel,
            id_key: id_sel,
        }
    )


def _draft_command_list(draft: dict[str, Any], key: str) -> list[dict[str, Any]]:
    raw = draft.get(key)
    if not isinstance(raw, list):
        return []
    return [cmd for cmd in raw if isinstance(cmd, dict)]


def _import_json_default(commands: list[dict[str, Any]], example: list[dict[str, Any]]) -> str:
    payload = commands if commands else example
    return json.dumps(payload, indent=2)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): selector.NumberSelector(
            selector.NumberSelectorConfig(
                mode=selector.NumberSelectorMode.BOX,
                min=1,
                max=65535,
            )
        ),
        vol.Optional(CONF_DEVICE_NAME): str,
        vol.Optional(
            CONF_CONNECT_TIMEOUT, default=DEFAULT_CONNECT_TIMEOUT
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                mode=selector.NumberSelectorMode.BOX,
                min=1.0,
                max=120.0,
                step=0.5,
                unit_of_measurement="s",
            )
        ),
    }
)


async def _validate_connection(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Probe TCP and return device metadata."""
    host = data[CONF_HOST].strip()
    port = int(data[CONF_PORT])
    cto = float(data.get(CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT))
    client = ItachClient(host, port, connect_timeout=cto, command_timeout=15.0)
    try:
        await client.connect()
        lines = await client.getdevices()
        device_modules = parse_getdevices_lines(lines)
        ver_lines = await client.send_raw(
            "getversion,0",
            end_on=is_getversion_reply,
            timeout=10.0,
        )
        fw = firmware_from_getversion_lines(ver_lines)
        model = infer_product_label(device_modules, fw)
    except (TimeoutError, OSError, ItachError) as err:
        _LOGGER.warning("iTach validation failed: %s", err)
        raise vol.Invalid("Could not connect or query the iTach device") from err
    finally:
        await client.disconnect()
    return {
        "model": model,
        "firmware": fw,
        CONF_DEVICE_MODULES: device_modules,
    }


def _host_port_taken(hass: HomeAssistant, host: str, port: int) -> bool:
    """Return True if another config entry already uses this host and port."""
    host_key = host.strip().lower()
    for entry in hass.config_entries.async_entries(DOMAIN):
        if (
            entry.data.get(CONF_HOST, "").strip().lower() == host_key
            and int(entry.data.get(CONF_PORT, DEFAULT_PORT)) == port
        ):
            return True
    return False


class GlobalCacheItachConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle first-time UI setup."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize discovery flow state."""
        self._discovery_host: str | None = None
        self._discovery_port: int = DEFAULT_PORT
        self._discovery_unique_id: str | None = None
        self._discovery_model_hint: str = ""
        self._discovery_probe: dict[str, Any] | None = None
        self._discovered_beacons: list[BeaconInfo] = []

    def is_matching(self, other_flow: Self) -> bool:
        """Dedupe concurrent discovery flows for the same gateway."""
        return (
            self._discovery_unique_id is not None
            and self._discovery_unique_id == other_flow._discovery_unique_id
        )

    async def _async_prepare_discovery(
        self,
        host: str,
        unique_id: str,
        *,
        port: int = DEFAULT_PORT,
        model_hint: str = "",
    ) -> FlowResult:
        """Validate a discovered gateway and show the confirm step."""
        self._discovery_host = host.strip()
        self._discovery_port = port
        self._discovery_unique_id = unique_id
        self._discovery_model_hint = model_hint
        self._discovery_probe = None

        await self.async_set_unique_id(unique_id)

        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.unique_id == unique_id:
                if entry.data.get(CONF_HOST) != self._discovery_host:
                    return self.async_update_reload_and_abort(
                        entry,
                        data={**entry.data, CONF_HOST: self._discovery_host},
                    )
                return self.async_abort(reason="already_configured")

        if self.hass.config_entries.flow.async_has_matching_flow(self):
            return self.async_abort(reason="already_in_progress")

        if _host_port_taken(self.hass, self._discovery_host, port):
            return self.async_abort(reason="already_configured")

        probe_data = {
            CONF_HOST: self._discovery_host,
            CONF_PORT: port,
            CONF_CONNECT_TIMEOUT: DEFAULT_CONNECT_TIMEOUT,
        }
        try:
            info = await _validate_connection(self.hass, probe_data)
        except vol.Invalid:
            return self.async_abort(reason="cannot_connect")

        info["model"] = prefer_discovery_model(
            str(info.get("model", "")), model_hint
        )
        self._discovery_probe = info
        return await self.async_step_confirm()

    async def async_step_discovery(
        self, discovery_info: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle a device discovered via UDP multicast beacon."""
        if not discovery_info:
            return self.async_abort(reason="cannot_connect")
        beacon = BeaconInfo.from_dict(discovery_info)
        return await self._async_prepare_discovery(
            beacon.host,
            beacon.unique_id,
            model_hint=beacon.model,
        )

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> FlowResult:
        """Handle a device discovered via DHCP."""
        unique_id = normalize_unique_id(discovery_info.macaddress)
        hostname = discovery_info.hostname or ""
        model_hint = hostname.replace("GlobalCache_", "").replace("globalcache_", "")
        if model_hint == hostname:
            model_hint = ""
        return await self._async_prepare_discovery(
            discovery_info.ip,
            unique_id,
            model_hint=model_hint,
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm setup of a discovered gateway."""
        if self._discovery_host is None or self._discovery_probe is None:
            return self.async_abort(reason="cannot_connect")

        if user_input is not None:
            title = (
                user_input.get(CONF_DEVICE_NAME)
                or self._discovery_model_hint
                or f"iTach {self._discovery_host}"
            )
            opts = _default_options()
            return self.async_create_entry(
                title=str(title),
                data={
                    CONF_HOST: self._discovery_host,
                    CONF_PORT: self._discovery_port,
                    "model": self._discovery_probe.get(
                        "model", self._discovery_model_hint
                    ),
                    "firmware": self._discovery_probe.get("firmware", ""),
                    CONF_DEVICE_MODULES: self._discovery_probe.get(
                        CONF_DEVICE_MODULES, []
                    ),
                },
                options=opts,
            )

        default_name = self._discovery_model_hint or f"iTach {self._discovery_host}"
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_DEVICE_NAME, default=default_name): str,
                }
            ),
            description_placeholders={
                "host": self._discovery_host,
                "model": self._discovery_probe.get(
                    "model", self._discovery_model_hint
                )
                or "iTach",
                "uuid": self._discovery_unique_id or "",
            },
        )

    async def async_step_pick_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pick a gateway found during an active network scan."""
        if user_input is not None:
            if user_input["device"] == "__manual__":
                return await self.async_step_user({"__manual__": True})
            beacon = next(
                (
                    b
                    for b in self._discovered_beacons
                    if b.unique_id == user_input["device"]
                ),
                None,
            )
            if beacon is None:
                return self.async_abort(reason="cannot_connect")
            return await self._async_prepare_discovery(
                beacon.host,
                beacon.unique_id,
                model_hint=beacon.model,
            )

        if not self._discovered_beacons:
            return await self.async_step_user()

        choice_map = {
            beacon.unique_id: f"{beacon.model or 'iTach'} ({beacon.host})"
            for beacon in self._discovered_beacons
        }
        choice_map["__manual__"] = "Enter address manually"
        return self.async_show_form(
            step_id="pick_device",
            data_schema=vol.Schema({vol.Required("device"): vol.In(choice_map)}),
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is None:
            self._discovered_beacons = await async_scan_beacons(
                self.hass, timeout=5.0
            )
            if self._discovered_beacons:
                return await self.async_step_pick_device()
        elif user_input.get("__manual__"):
            user_input = None

        if user_input is not None:
            try:
                info = await _validate_connection(self.hass, user_input)
            except vol.Invalid:
                errors["base"] = "cannot_connect"
            else:
                host = user_input[CONF_HOST].strip().lower()
                port = int(user_input[CONF_PORT])
                if _host_port_taken(self.hass, user_input[CONF_HOST], port):
                    return self.async_abort(reason="already_configured")
                await self.async_set_unique_id(f"{host}_{port}")
                self._abort_if_unique_id_configured(updates=user_input)
                title = (
                    user_input.get(CONF_DEVICE_NAME)
                    or f"iTach {user_input[CONF_HOST]}"
                )
                opts = _default_options()
                opts[CONF_CONNECT_TIMEOUT] = float(
                    user_input.get(CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT)
                )
                return self.async_create_entry(
                    title=str(title),
                    data={
                        CONF_HOST: user_input[CONF_HOST].strip(),
                        CONF_PORT: port,
                        "model": info.get("model", ""),
                        "firmware": info.get("firmware", ""),
                        CONF_DEVICE_MODULES: info.get(CONF_DEVICE_MODULES, []),
                    },
                    options=opts,
                )
        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Change gateway host/port without removing the config entry."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()
        if user_input is not None:
            probe = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
                CONF_CONNECT_TIMEOUT: user_input.get(
                    CONF_CONNECT_TIMEOUT,
                    reconfigure_entry.options.get(
                        CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT
                    ),
                ),
            }
            try:
                info = await _validate_connection(self.hass, probe)
            except vol.Invalid:
                errors["base"] = "cannot_connect"
            else:
                host_key = user_input[CONF_HOST].strip().lower()
                port = int(user_input[CONF_PORT])
                await self.async_set_unique_id(f"{host_key}_{port}")
                self._abort_if_unique_id_configured()
                title = (
                    user_input.get(CONF_DEVICE_NAME) or reconfigure_entry.title
                )
                opts = dict(reconfigure_entry.options)
                opts[CONF_CONNECT_TIMEOUT] = float(
                    user_input.get(
                        CONF_CONNECT_TIMEOUT,
                        opts.get(CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT),
                    )
                )
                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    title=str(title),
                    data={
                        **reconfigure_entry.data,
                        CONF_HOST: user_input[CONF_HOST].strip(),
                        CONF_PORT: port,
                        "model": info.get("model", reconfigure_entry.data.get("model", "")),
                        "firmware": info.get(
                            "firmware", reconfigure_entry.data.get("firmware", "")
                        ),
                        CONF_DEVICE_MODULES: info.get(
                            CONF_DEVICE_MODULES,
                            reconfigure_entry.data.get(CONF_DEVICE_MODULES, []),
                        ),
                    },
                    options=opts,
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._reconfigure_schema(reconfigure_entry),
            errors=errors,
        )

    @staticmethod
    def _reconfigure_schema(entry: ConfigEntry) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(
                    CONF_HOST, default=entry.data.get(CONF_HOST, "")
                ): str,
                vol.Optional(
                    CONF_PORT,
                    default=int(entry.data.get(CONF_PORT, DEFAULT_PORT)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.BOX,
                        min=1,
                        max=65535,
                    )
                ),
                vol.Optional(
                    CONF_DEVICE_NAME, default=entry.title
                ): str,
                vol.Optional(
                    CONF_CONNECT_TIMEOUT,
                    default=float(
                        entry.options.get(
                            CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT
                        )
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.BOX,
                        min=1.0,
                        max=120.0,
                        step=0.5,
                        unit_of_measurement="s",
                    )
                ),
            }
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> GlobalCacheItachOptionsFlow:
        # OptionsFlow is constructed with no args; HA sets ``handler`` to the entry id.
        return GlobalCacheItachOptionsFlow()


def _default_options() -> dict[str, Any]:
    return {
        CONF_CONNECT_TIMEOUT: DEFAULT_CONNECT_TIMEOUT,
        CONF_COMMAND_TIMEOUT: DEFAULT_COMMAND_TIMEOUT,
        CONF_DEFAULT_FREQ: DEFAULT_CARRIER_HZ,
        CONF_DEFAULT_REPEAT: DEFAULT_REPEAT,
        CONF_DEFAULT_OFFSET: DEFAULT_OFFSET,
        CONF_ID_POLICY: ID_POLICY_AUTO,
        CONF_FIXED_COMMAND_ID: 1,
        CONF_REMOTES: [],
        CONF_RELAYS: [],
        CONF_SERIAL_PORTS: [],
    }


def _learn_timeout_selector() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            mode=selector.NumberSelectorMode.BOX,
            min=5,
            max=120,
            step=1,
            unit_of_measurement="s",
        )
    )


class GlobalCacheItachOptionsFlow(OptionsFlow):
    """Options and remote/command editor."""

    def __init__(self) -> None:
        """Initialize draft state for add-remote wizard steps."""
        self._remote_draft: dict[str, Any] | None = None
        self._relay_draft: dict[str, Any] | None = None
        self._serial_draft: dict[str, Any] | None = None
        self._learn_draft: dict[str, Any] | None = None
        self._editing_command_index: int | None = None
        self._command_pick_mode: str | None = None

    def _options_entry(self) -> ConfigEntry:
        """Resolve config entry from flow handler (entry id); works without ``OptionsFlow.__init__(entry)``."""
        assert self.hass is not None and self.handler is not None
        entry = self.hass.config_entries.async_get_entry(self.handler)
        if entry is None:
            raise RuntimeError("Options flow config entry not found")
        return entry

    def _opts(self) -> dict[str, Any]:
        base = dict(_default_options())
        base.update(self._options_entry().options)
        return base

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            if user_input["next"] == "timeouts":
                return await self.async_step_timeouts()
            if user_input["next"] == "ir_defaults":
                return await self.async_step_ir_defaults()
            if user_input["next"] == "add_remote":
                self._remote_draft = {}
                return await self.async_step_remote_name()
            if user_input["next"] == "remove_remote":
                return await self.async_step_remove_remote()
            if user_input["next"] == "edit_remote":
                return await self.async_step_edit_remote()
            if user_input["next"] == "add_relay":
                self._relay_draft = {}
                return await self.async_step_relay_name()
            if user_input["next"] == "remove_relay":
                return await self.async_step_remove_relay()
            if user_input["next"] == "edit_relay":
                return await self.async_step_edit_relay()
            if user_input["next"] == "add_serial":
                self._serial_draft = {}
                return await self.async_step_serial_name()
            if user_input["next"] == "remove_serial":
                return await self.async_step_remove_serial()
            if user_input["next"] == "edit_serial":
                return await self.async_step_edit_serial()
            if user_input["next"] == "learn_ir":
                return await self.async_step_learn_ir()
        # Use vol.In (not SelectSelector dict options) for broad HA version compatibility.
        opts = self._opts()
        remotes: list[dict[str, Any]] = list(opts.get(CONF_REMOTES, []))
        relays: list[dict[str, Any]] = list(opts.get(CONF_RELAYS, []))
        serials: list[dict[str, Any]] = list(opts.get(CONF_SERIAL_PORTS, []))
        menu = build_options_init_menu(
            remotes=remotes, relays=relays, serials=serials
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({vol.Required("next"): vol.In(menu)}),
        )

    async def async_step_timeouts(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        opts = self._opts()
        if user_input is not None:
            new_opts = {**opts, **user_input}
            return self.async_create_entry(title="", data=new_opts)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CONNECT_TIMEOUT,
                    default=opts.get(CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.BOX,
                        min=1.0,
                        max=120.0,
                        step=0.5,
                        unit_of_measurement="s",
                    )
                ),
                vol.Required(
                    CONF_COMMAND_TIMEOUT,
                    default=opts.get(CONF_COMMAND_TIMEOUT, DEFAULT_COMMAND_TIMEOUT),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.BOX,
                        min=1.0,
                        max=120.0,
                        step=0.5,
                        unit_of_measurement="s",
                    )
                ),
            }
        )
        return self.async_show_form(step_id="timeouts", data_schema=schema)

    async def async_step_ir_defaults(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        opts = self._opts()
        if user_input is not None:
            new_opts = {**opts, **user_input}
            return self.async_create_entry(title="", data=new_opts)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DEFAULT_FREQ,
                    default=opts.get(CONF_DEFAULT_FREQ, DEFAULT_CARRIER_HZ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.BOX,
                        min=15000,
                        max=500000,
                        step=1000,
                        unit_of_measurement="Hz",
                    )
                ),
                vol.Required(
                    CONF_DEFAULT_REPEAT,
                    default=opts.get(CONF_DEFAULT_REPEAT, DEFAULT_REPEAT),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.BOX,
                        min=1,
                        max=50,
                    )
                ),
                vol.Required(
                    CONF_DEFAULT_OFFSET,
                    default=opts.get(CONF_DEFAULT_OFFSET, DEFAULT_OFFSET),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.BOX,
                        min=1,
                        max=383,
                        step=2,
                    )
                ),
                vol.Required(
                    CONF_ID_POLICY,
                    default=opts.get(CONF_ID_POLICY, ID_POLICY_AUTO),
                ): vol.In(
                    {
                        ID_POLICY_AUTO: "Auto-increment ID",
                        ID_POLICY_FIXED: "Fixed ID",
                    }
                ),
                vol.Optional(
                    CONF_FIXED_COMMAND_ID,
                    default=opts.get(CONF_FIXED_COMMAND_ID, 1),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.BOX,
                        min=0,
                        max=65535,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="ir_defaults", data_schema=schema)

    async def async_step_edit_remote(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        opts = self._opts()
        remotes: list[dict[str, Any]] = list(opts.get(CONF_REMOTES, []))
        if not remotes:
            return await self.async_step_init()
        if user_input is not None:
            rid = user_input[CONF_REMOTE_ID]
            found = next(
                (r for r in remotes if str(r.get(CONF_REMOTE_ID)) == str(rid)),
                None,
            )
            if found is None:
                return await self.async_step_init()
            self._remote_draft = copy.deepcopy(found)
            return await self.async_step_remote_name()
        choice_map = {
            str(r[CONF_REMOTE_ID]): str(r.get(CONF_REMOTE_NAME, r[CONF_REMOTE_ID]))[:80]
            for r in remotes
        }
        schema = vol.Schema({vol.Required(CONF_REMOTE_ID): vol.In(choice_map)})
        return self.async_show_form(step_id="edit_remote", data_schema=schema)

    async def async_step_remote_name(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        assert self._remote_draft is not None
        if user_input is not None:
            self._remote_draft[CONF_REMOTE_NAME] = user_input[CONF_REMOTE_NAME]
            return await self.async_step_remote_connector()
        default_name = str(self._remote_draft.get(CONF_REMOTE_NAME, ""))
        return self.async_show_form(
            step_id="remote_name",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REMOTE_NAME, default=default_name): str,
                }
            ),
        )

    async def async_step_remote_connector(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        assert self._remote_draft is not None
        if user_input is not None:
            self._remote_draft[CONF_MODULE] = int(user_input[CONF_MODULE])
            self._remote_draft[CONF_CONN_PORT] = int(user_input[CONF_CONN_PORT])
            self._remote_draft[CONF_IR_COUNT] = int(user_input[CONF_IR_COUNT])
            return await self.async_step_remote_command_list()
        entry = self._options_entry()
        modules = list(entry.data.get(CONF_DEVICE_MODULES, []))
        dm = int(
            self._remote_draft.get(
                CONF_MODULE, default_ir_module(modules) if modules else 1
            )
        )
        dp = int(self._remote_draft.get(CONF_CONN_PORT, 1))
        dirc = int(self._remote_draft.get(CONF_IR_COUNT, 1))
        return self.async_show_form(
            step_id="remote_connector",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MODULE,
                        default=dm,
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            mode=selector.NumberSelectorMode.BOX,
                            min=1,
                            max=5,
                        )
                    ),
                    vol.Required(
                        CONF_CONN_PORT,
                        default=dp,
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            mode=selector.NumberSelectorMode.BOX,
                            min=1,
                            max=6,
                        )
                    ),
                    vol.Required(
                        CONF_IR_COUNT,
                        default=dirc,
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            mode=selector.NumberSelectorMode.BOX,
                            min=1,
                            max=50,
                        )
                    ),
                }
            ),
            description_placeholders={
                "ir_hint": ir_connectors_hint(modules),
            },
        )

    def _save_remote(self) -> FlowResult:
        """Write the remote draft, including its command list, into options."""
        assert self._remote_draft is not None
        commands = _draft_command_list(self._remote_draft, CONF_COMMANDS)
        opts = self._opts()
        remotes = list(opts.get(CONF_REMOTES, []))
        existing_id = self._remote_draft.get(CONF_REMOTE_ID)
        new_remote: dict[str, Any] = {
            CONF_REMOTE_ID: str(existing_id) if existing_id else str(uuid.uuid4()),
            CONF_REMOTE_NAME: self._remote_draft[CONF_REMOTE_NAME],
            CONF_MODULE: int(self._remote_draft[CONF_MODULE]),
            CONF_CONN_PORT: int(self._remote_draft[CONF_CONN_PORT]),
            CONF_IR_COUNT: int(self._remote_draft[CONF_IR_COUNT]),
            CONF_COMMANDS: commands,
        }
        if existing_id:
            eid = str(existing_id)
            remotes = [
                new_remote if str(r.get(CONF_REMOTE_ID)) == eid else r for r in remotes
            ]
        else:
            remotes.append(new_remote)
        self._remote_draft = None
        self._editing_command_index = None
        return self.async_create_entry(
            title="", data={**opts, CONF_REMOTES: remotes}
        )

    async def async_step_remote_command_list(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add, edit, remove, or import commands, then save the remote."""
        assert self._remote_draft is not None
        commands = _draft_command_list(self._remote_draft, CONF_COMMANDS)
        if user_input is not None:
            action = user_input["next"]
            if action == "add":
                self._editing_command_index = None
                return await self.async_step_remote_command_form()
            if action == "edit":
                self._command_pick_mode = "edit"
                return await self.async_step_remote_command_pick()
            if action == "remove":
                self._command_pick_mode = "remove"
                return await self.async_step_remote_command_pick()
            if action == "import_json":
                return await self.async_step_remote_command_import()
            if action == "save":
                return self._save_remote()
        self._editing_command_index = None
        self._command_pick_mode = None
        menu = build_command_list_menu(
            has_commands=bool(commands), save_label="Save remote"
        )
        return self.async_show_form(
            step_id="remote_command_list",
            data_schema=vol.Schema({vol.Required("next"): vol.In(menu)}),
            description_placeholders={
                "summary": format_command_summary(commands, include_format=True),
            },
        )

    async def async_step_remote_command_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Choose which command to edit or remove."""
        assert self._remote_draft is not None
        commands = _draft_command_list(self._remote_draft, CONF_COMMANDS)
        if not commands:
            return await self.async_step_remote_command_list()
        mode = self._command_pick_mode or "edit"
        if user_input is not None:
            index = int(user_input["command"])
            if mode == "remove":
                self._remote_draft[CONF_COMMANDS] = remove_command_at(commands, index)
                self._command_pick_mode = None
                return await self.async_step_remote_command_list()
            self._editing_command_index = index
            return await self.async_step_remote_command_form()
        choices = {
            str(index): format_command_summary([cmd], include_format=True)
            for index, cmd in enumerate(commands)
        }
        if mode == "remove":
            summary = (
                "Choose a command to remove. The button is deleted when you save "
                f"the remote. {format_command_summary(commands, include_format=True)}"
            )
        else:
            summary = (
                "Choose a command to edit. "
                f"{format_command_summary(commands, include_format=True)}"
            )
        return self.async_show_form(
            step_id="remote_command_pick",
            data_schema=vol.Schema({vol.Required("command"): vol.In(choices)}),
            description_placeholders={"summary": summary},
        )

    async def async_step_remote_command_form(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """One infrared command: name, format, code, and optional overrides."""
        assert self._remote_draft is not None
        commands = _draft_command_list(self._remote_draft, CONF_COMMANDS)
        index = self._editing_command_index
        existing = commands[index] if index is not None and index < len(commands) else None
        errors: dict[str, str] = {}
        if user_input is not None:
            built, err = ir_command_from_fields(
                str(user_input.get(CONF_CMD_NAME, "")),
                str(user_input.get(CONF_CMD_DATA, "")),
                str(user_input.get(CONF_CMD_FORMAT, "")),
                user_input.get(CONF_CMD_FREQ),
                user_input.get(CONF_CMD_REPEAT),
                user_input.get(CONF_CMD_OFFSET),
                user_input.get(CONF_CMD_ID),
            )
            if err or built is None:
                errors["base"] = err or "command_fields"
            else:
                updated, apply_err = apply_command_change(
                    commands, built, original_index=index if existing else None
                )
                if apply_err or updated is None:
                    errors["base"] = apply_err or "duplicate_command"
                else:
                    self._remote_draft[CONF_COMMANDS] = updated
                    self._editing_command_index = None
                    return await self.async_step_remote_command_list()
        return self.async_show_form(
            step_id="remote_command_form",
            data_schema=_ir_command_schema(user_input, existing),
            errors=errors,
        )

    async def async_step_remote_command_import(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Replace the draft command list from a JSON array."""
        assert self._remote_draft is not None
        commands = _draft_command_list(self._remote_draft, CONF_COMMANDS)
        errors: dict[str, str] = {}
        default_json = _import_json_default(commands, _IR_IMPORT_EXAMPLE)
        if user_input is not None:
            default_json = str(user_input.get("commands_json", default_json))
            parsed, parse_err = parse_commands_json(default_json)
            if parse_err or parsed is None:
                errors["base"] = command_json_error_key(parse_err or "")
            else:
                slug_err = imported_commands_error(parsed)
                if slug_err:
                    errors["base"] = slug_err
                else:
                    self._remote_draft[CONF_COMMANDS] = parsed
                    return await self.async_step_remote_command_list()
        return self.async_show_form(
            step_id="remote_command_import",
            data_schema=vol.Schema(
                {vol.Required("commands_json", default=default_json): _MULTILINE}
            ),
            errors=errors,
        )

    async def async_step_learn_ir(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pick remote (or create one) and command name, then capture from pinhole."""
        opts = self._opts()
        remotes: list[dict[str, Any]] = list(opts.get(CONF_REMOTES, []))
        entry = self._options_entry()
        modules = list(entry.data.get(CONF_DEVICE_MODULES, []))
        default_module = default_ir_module(modules) if modules else 1

        if not remotes:
            return await self._async_step_learn_ir_new_remote(
                user_input, default_module=default_module, modules=modules
            )

        choices = {
            str(r[CONF_REMOTE_ID]): str(r.get(CONF_REMOTE_NAME, r[CONF_REMOTE_ID]))
            for r in remotes
            if r.get(CONF_REMOTE_ID)
        }
        schema = vol.Schema(
            {
                vol.Required(CONF_REMOTE_ID): vol.In(choices),
                vol.Required(CONF_CMD_NAME, default="power"): str,
                vol.Required("timeout", default=30): _learn_timeout_selector(),
            }
        )
        placeholders = {"ir_hint": ir_connectors_hint(modules) or " "}
        if user_input is not None:
            cmd_name = str(user_input[CONF_CMD_NAME]).strip()
            self._learn_draft = {
                CONF_REMOTE_ID: str(user_input[CONF_REMOTE_ID]),
                CONF_CMD_NAME: cmd_name,
                "timeout": float(user_input.get("timeout", 30)),
            }
            if not cmd_name:
                return self.async_show_form(
                    step_id="learn_ir",
                    data_schema=schema,
                    errors={"base": "command_name_required"},
                    description_placeholders=placeholders,
                )
            return await self.async_step_learn_ir_capture()
        return self.async_show_form(
            step_id="learn_ir",
            data_schema=schema,
            description_placeholders=placeholders,
        )

    async def _async_step_learn_ir_new_remote(
        self,
        user_input: dict[str, Any] | None,
        *,
        default_module: int,
        modules: list[Any],
    ) -> FlowResult:
        """Learn IR when no remotes exist: create remote + command in one flow."""
        # CONF_REMOTE_NAME and CONF_CMD_NAME are both "name" in options storage;
        # use a distinct form key for the remote label on this combined step.
        schema = vol.Schema(
            {
                vol.Required("remote_name", default="Remote"): str,
                vol.Required(
                    CONF_MODULE, default=default_module
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.BOX,
                        min=1,
                        max=5,
                    )
                ),
                vol.Required(CONF_CONN_PORT, default=1): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.BOX,
                        min=1,
                        max=6,
                    )
                ),
                vol.Required(CONF_CMD_NAME, default="power"): str,
                vol.Required("timeout", default=30): _learn_timeout_selector(),
            }
        )
        placeholders = {"ir_hint": ir_connectors_hint(modules) or " "}
        if user_input is not None:
            remote_name = str(user_input.get("remote_name", "")).strip() or "Remote"
            cmd_name = str(user_input[CONF_CMD_NAME]).strip()
            self._learn_draft = {
                "create_remote": True,
                CONF_REMOTE_NAME: remote_name,
                CONF_MODULE: int(user_input[CONF_MODULE]),
                CONF_CONN_PORT: int(user_input[CONF_CONN_PORT]),
                CONF_IR_COUNT: 1,
                CONF_CMD_NAME: cmd_name,
                "timeout": float(user_input.get("timeout", 30)),
            }
            if not cmd_name:
                return self.async_show_form(
                    step_id="learn_ir",
                    data_schema=schema,
                    errors={"base": "command_name_required"},
                    description_placeholders=placeholders,
                )
            return await self.async_step_learn_ir_capture()
        return self.async_show_form(
            step_id="learn_ir",
            data_schema=schema,
            description_placeholders=placeholders,
        )

    async def async_step_learn_ir_capture(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """After Submit: enable learner, wait for one sendir, append to remote."""
        from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

        draft = self._learn_draft
        if draft is None:
            # Step can be entered without a prior learn_ir submit (or draft was cleared).
            return await self.async_step_learn_ir()

        errors: dict[str, str] = {}
        timeout = float(draft.get("timeout", 30))
        command = str(draft[CONF_CMD_NAME])
        if user_input is not None:
            entry = self._options_entry()
            coordinator = self.hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if coordinator is None:
                errors["base"] = "not_loaded"
            else:
                raw_line: str | None = None
                try:
                    raw_line = await coordinator.async_learn_ir_command(
                        timeout=timeout
                    )
                except (
                    TimeoutError,
                    OSError,
                    ItachError,
                    ServiceValidationError,
                    HomeAssistantError,
                ) as err:
                    _LOGGER.warning("IR learn failed: %s", err)
                    errors["base"] = "learn_failed"
                    draft["error_detail"] = str(err)

                if raw_line is not None and not errors:
                    opts = self._opts()
                    remotes = list(opts.get(CONF_REMOTES, []))
                    name = command
                    if draft.get("create_remote"):
                        module = int(draft[CONF_MODULE])
                        port = int(draft[CONF_CONN_PORT])
                        try:
                            sendir = rewrite_sendir_connector(
                                raw_line, module, port
                            )
                        except ValueError as err:
                            errors["base"] = "learn_failed"
                            draft["error_detail"] = str(err)
                        else:
                            remotes.append(
                                {
                                    CONF_REMOTE_ID: str(uuid.uuid4()),
                                    CONF_REMOTE_NAME: str(
                                        draft.get(CONF_REMOTE_NAME, "Remote")
                                    ),
                                    CONF_MODULE: module,
                                    CONF_CONN_PORT: port,
                                    CONF_IR_COUNT: int(draft.get(CONF_IR_COUNT, 1)),
                                    CONF_COMMANDS: [
                                        {
                                            CONF_CMD_NAME: name,
                                            CONF_CMD_FORMAT: "full_sendir",
                                            CONF_CMD_DATA: sendir,
                                        }
                                    ],
                                }
                            )
                            self._learn_draft = None
                            return self.async_create_entry(
                                title="",
                                data={**opts, CONF_REMOTES: remotes},
                            )
                    else:
                        rid = str(draft[CONF_REMOTE_ID])
                        updated: list[dict[str, Any]] = []
                        found = False
                        for remote in remotes:
                            if str(remote.get(CONF_REMOTE_ID)) != rid:
                                updated.append(remote)
                                continue
                            found = True
                            module = int(remote[CONF_MODULE])
                            port = int(remote[CONF_CONN_PORT])
                            try:
                                sendir = rewrite_sendir_connector(
                                    raw_line, module, port
                                )
                            except ValueError as err:
                                errors["base"] = "learn_failed"
                                draft["error_detail"] = str(err)
                                updated.append(remote)
                                continue
                            commands = [
                                c
                                for c in list(remote.get(CONF_COMMANDS, []))
                                if str(c.get(CONF_CMD_NAME, "")).strip().lower()
                                != name.lower()
                            ]
                            commands.append(
                                {
                                    CONF_CMD_NAME: name,
                                    CONF_CMD_FORMAT: "full_sendir",
                                    CONF_CMD_DATA: sendir,
                                }
                            )
                            updated.append({**remote, CONF_COMMANDS: commands})
                        if not errors and not found:
                            errors["base"] = "learn_failed"
                            draft["error_detail"] = "Remote not found"
                        elif not errors:
                            self._learn_draft = None
                            return self.async_create_entry(
                                title="",
                                data={**opts, CONF_REMOTES: updated},
                            )

        detail = str(draft.get("error_detail") or "")
        return self.async_show_form(
            step_id="learn_ir_capture",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={
                "timeout": str(int(timeout)),
                "command": command,
                "detail": detail or " ",
            },
        )

    async def async_step_remove_remote(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        opts = self._opts()
        remotes: list[dict[str, Any]] = list(opts.get(CONF_REMOTES, []))
        if not remotes:
            return self.async_create_entry(title="", data=opts)
        if user_input is not None:
            rid = user_input[CONF_REMOTE_ID]
            new_list = [r for r in remotes if r.get(CONF_REMOTE_ID) != rid]
            new_opts = {**opts, CONF_REMOTES: new_list}
            return self.async_create_entry(title="", data=new_opts)
        choice_map = {
            r[CONF_REMOTE_ID]: str(r.get(CONF_REMOTE_NAME, r[CONF_REMOTE_ID]))[:80]
            for r in remotes
        }
        schema = vol.Schema(
            {vol.Required(CONF_REMOTE_ID): vol.In(choice_map)}
        )
        return self.async_show_form(step_id="remove_remote", data_schema=schema)

    async def async_step_relay_name(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        assert self._relay_draft is not None
        if user_input is not None:
            self._relay_draft[CONF_RELAY_NAME] = user_input[CONF_RELAY_NAME]
            return await self.async_step_relay_connector()
        return self.async_show_form(
            step_id="relay_name",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_RELAY_NAME,
                        default=str(self._relay_draft.get(CONF_RELAY_NAME, "")),
                    ): str,
                }
            ),
        )

    async def async_step_relay_connector(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        assert self._relay_draft is not None
        if user_input is not None:
            opts = self._opts()
            relays = list(opts.get(CONF_RELAYS, []))
            existing_id = self._relay_draft.get(CONF_RELAY_ID)
            new_relay: dict[str, Any] = {
                CONF_RELAY_ID: str(existing_id)
                if existing_id
                else str(uuid.uuid4()),
                CONF_RELAY_NAME: self._relay_draft[CONF_RELAY_NAME],
                CONF_MODULE: int(user_input[CONF_MODULE]),
                CONF_CONN_PORT: int(user_input[CONF_CONN_PORT]),
            }
            if existing_id:
                eid = str(existing_id)
                relays = [
                    new_relay if str(r.get(CONF_RELAY_ID)) == eid else r
                    for r in relays
                ]
            else:
                relays.append(new_relay)
            self._relay_draft = None
            return self.async_create_entry(
                title="", data={**opts, CONF_RELAYS: relays}
            )
        dm = int(self._relay_draft.get(CONF_MODULE, 3))
        dp = int(self._relay_draft.get(CONF_CONN_PORT, 1))
        return self.async_show_form(
            step_id="relay_connector",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MODULE, default=dm): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            mode=selector.NumberSelectorMode.BOX, min=1, max=5
                        )
                    ),
                    vol.Required(CONF_CONN_PORT, default=dp): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            mode=selector.NumberSelectorMode.BOX, min=1, max=6
                        )
                    ),
                }
            ),
        )

    async def async_step_edit_relay(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        opts = self._opts()
        relays: list[dict[str, Any]] = list(opts.get(CONF_RELAYS, []))
        if not relays:
            return await self.async_step_init()
        if user_input is not None:
            rid = user_input[CONF_RELAY_ID]
            found = next(
                (r for r in relays if str(r.get(CONF_RELAY_ID)) == str(rid)),
                None,
            )
            if found is None:
                return await self.async_step_init()
            self._relay_draft = copy.deepcopy(found)
            return await self.async_step_relay_name()
        choice_map = {
            r[CONF_RELAY_ID]: str(r.get(CONF_RELAY_NAME, r[CONF_RELAY_ID]))[:80]
            for r in relays
        }
        return self.async_show_form(
            step_id="edit_relay",
            data_schema=vol.Schema({vol.Required(CONF_RELAY_ID): vol.In(choice_map)}),
        )

    async def async_step_remove_relay(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        opts = self._opts()
        relays: list[dict[str, Any]] = list(opts.get(CONF_RELAYS, []))
        if not relays:
            return self.async_create_entry(title="", data=opts)
        if user_input is not None:
            rid = user_input[CONF_RELAY_ID]
            new_list = [r for r in relays if r.get(CONF_RELAY_ID) != rid]
            return self.async_create_entry(
                title="", data={**opts, CONF_RELAYS: new_list}
            )
        choice_map = {
            r[CONF_RELAY_ID]: str(r.get(CONF_RELAY_NAME, r[CONF_RELAY_ID]))[:80]
            for r in relays
        }
        return self.async_show_form(
            step_id="remove_relay",
            data_schema=vol.Schema({vol.Required(CONF_RELAY_ID): vol.In(choice_map)}),
        )

    async def async_step_serial_name(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        assert self._serial_draft is not None
        if user_input is not None:
            self._serial_draft[CONF_SERIAL_NAME] = user_input[CONF_SERIAL_NAME]
            return await self.async_step_serial_connector()
        return self.async_show_form(
            step_id="serial_name",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SERIAL_NAME,
                        default=str(self._serial_draft.get(CONF_SERIAL_NAME, "")),
                    ): str,
                }
            ),
        )

    async def async_step_serial_connector(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        assert self._serial_draft is not None
        if user_input is not None:
            self._serial_draft[CONF_MODULE] = int(user_input[CONF_MODULE])
            self._serial_draft[CONF_CONN_PORT] = int(user_input[CONF_CONN_PORT])
            return await self.async_step_serial_settings()
        dm = int(self._serial_draft.get(CONF_MODULE, 1))
        dp = int(self._serial_draft.get(CONF_CONN_PORT, 1))
        return self.async_show_form(
            step_id="serial_connector",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MODULE, default=dm): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            mode=selector.NumberSelectorMode.BOX, min=1, max=5
                        )
                    ),
                    vol.Required(CONF_CONN_PORT, default=dp): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            mode=selector.NumberSelectorMode.BOX, min=1, max=6
                        )
                    ),
                }
            ),
        )

    async def async_step_serial_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        assert self._serial_draft is not None
        if user_input is not None:
            self._serial_draft[CONF_SERIAL_SETTINGS] = user_input[
                CONF_SERIAL_SETTINGS
            ].strip()
            self._serial_draft[CONF_SERIAL_APPEND_CR] = user_input[
                CONF_SERIAL_APPEND_CR
            ]
            self._serial_draft[CONF_SERIAL_LISTEN] = user_input[
                CONF_SERIAL_LISTEN
            ]
            return await self.async_step_serial_command_list()
        default_settings = str(
            self._serial_draft.get(CONF_SERIAL_SETTINGS, DEFAULT_SERIAL_SETTINGS)
        )
        return self.async_show_form(
            step_id="serial_settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SERIAL_SETTINGS, default=default_settings
                    ): str,
                    vol.Required(
                        CONF_SERIAL_APPEND_CR,
                        default=bool(
                            self._serial_draft.get(CONF_SERIAL_APPEND_CR, True)
                        ),
                    ): bool,
                    vol.Required(
                        CONF_SERIAL_LISTEN,
                        default=bool(
                            self._serial_draft.get(
                                CONF_SERIAL_LISTEN,
                                self._serial_draft.get(_LEGACY_SERIAL_LISTEN, True),
                            )
                        ),
                    ): bool,
                }
            ),
        )

    def _save_serial(self) -> FlowResult:
        """Write the serial draft, including preset buttons, into options."""
        assert self._serial_draft is not None
        commands = _draft_command_list(self._serial_draft, CONF_SERIAL_COMMANDS)
        opts = self._opts()
        ports = list(opts.get(CONF_SERIAL_PORTS, []))
        existing_id = self._serial_draft.get(CONF_SERIAL_ID)
        new_port: dict[str, Any] = {
            CONF_SERIAL_ID: str(existing_id) if existing_id else str(uuid.uuid4()),
            CONF_SERIAL_NAME: self._serial_draft[CONF_SERIAL_NAME],
            CONF_MODULE: int(self._serial_draft[CONF_MODULE]),
            CONF_CONN_PORT: int(self._serial_draft[CONF_CONN_PORT]),
            CONF_SERIAL_SETTINGS: self._serial_draft.get(
                CONF_SERIAL_SETTINGS, DEFAULT_SERIAL_SETTINGS
            ),
            CONF_SERIAL_APPEND_CR: bool(
                self._serial_draft.get(CONF_SERIAL_APPEND_CR, True)
            ),
            CONF_SERIAL_LISTEN: bool(
                self._serial_draft.get(
                    CONF_SERIAL_LISTEN,
                    self._serial_draft.get(_LEGACY_SERIAL_LISTEN, True),
                )
            ),
            CONF_SERIAL_COMMANDS: commands,
        }
        if existing_id:
            sid = str(existing_id)
            ports = [
                new_port if str(p.get(CONF_SERIAL_ID)) == sid else p for p in ports
            ]
        else:
            ports.append(new_port)
        self._serial_draft = None
        self._editing_command_index = None
        return self.async_create_entry(
            title="", data={**opts, CONF_SERIAL_PORTS: ports}
        )

    async def async_step_serial_command_list(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add, edit, remove, or import serial presets, then save the port."""
        assert self._serial_draft is not None
        commands = _draft_command_list(self._serial_draft, CONF_SERIAL_COMMANDS)
        if user_input is not None:
            action = user_input["next"]
            if action == "add":
                self._editing_command_index = None
                return await self.async_step_serial_command_form()
            if action == "edit":
                self._command_pick_mode = "edit"
                return await self.async_step_serial_command_pick()
            if action == "remove":
                self._command_pick_mode = "remove"
                return await self.async_step_serial_command_pick()
            if action == "import_json":
                return await self.async_step_serial_command_import()
            if action == "save":
                return self._save_serial()
        self._editing_command_index = None
        self._command_pick_mode = None
        menu = build_command_list_menu(
            has_commands=bool(commands), save_label="Save serial port"
        )
        return self.async_show_form(
            step_id="serial_command_list",
            data_schema=vol.Schema({vol.Required("next"): vol.In(menu)}),
            description_placeholders={
                "summary": format_command_summary(commands, include_format=False),
            },
        )

    async def async_step_serial_command_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Choose which serial preset to edit or remove."""
        assert self._serial_draft is not None
        commands = _draft_command_list(self._serial_draft, CONF_SERIAL_COMMANDS)
        if not commands:
            return await self.async_step_serial_command_list()
        mode = self._command_pick_mode or "edit"
        if user_input is not None:
            index = int(user_input["command"])
            if mode == "remove":
                self._serial_draft[CONF_SERIAL_COMMANDS] = remove_command_at(
                    commands, index
                )
                self._command_pick_mode = None
                return await self.async_step_serial_command_list()
            self._editing_command_index = index
            return await self.async_step_serial_command_form()
        choices = {
            str(index): format_command_summary([cmd], include_format=False)
            for index, cmd in enumerate(commands)
        }
        listed = format_command_summary(commands, include_format=False)
        if mode == "remove":
            summary = (
                "Choose a preset to remove. The button is deleted when you save "
                f"the serial port. {listed}"
            )
        else:
            summary = f"Choose a preset to edit. {listed}"
        return self.async_show_form(
            step_id="serial_command_pick",
            data_schema=vol.Schema({vol.Required("command"): vol.In(choices)}),
            description_placeholders={"summary": summary},
        )

    async def async_step_serial_command_form(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """One serial preset: name and payload."""
        assert self._serial_draft is not None
        commands = _draft_command_list(self._serial_draft, CONF_SERIAL_COMMANDS)
        index = self._editing_command_index
        existing = (
            commands[index] if index is not None and index < len(commands) else None
        )
        errors: dict[str, str] = {}
        source = user_input if user_input is not None else (existing or {})
        if user_input is not None:
            built, err = serial_command_from_fields(
                str(user_input.get(CONF_CMD_NAME, "")),
                str(user_input.get(CONF_SERIAL_PAYLOAD, "")),
            )
            if err or built is None:
                errors["base"] = err or "command_fields"
            else:
                updated, apply_err = apply_command_change(
                    commands, built, original_index=index if existing else None
                )
                if apply_err or updated is None:
                    errors["base"] = apply_err or "duplicate_command"
                else:
                    self._serial_draft[CONF_SERIAL_COMMANDS] = updated
                    self._editing_command_index = None
                    return await self.async_step_serial_command_list()
        return self.async_show_form(
            step_id="serial_command_form",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CMD_NAME, default=str(source.get(CONF_CMD_NAME, ""))
                    ): str,
                    vol.Required(
                        CONF_SERIAL_PAYLOAD,
                        default=str(source.get(CONF_SERIAL_PAYLOAD, "")),
                    ): _MULTILINE,
                }
            ),
            errors=errors,
        )

    async def async_step_serial_command_import(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Replace serial presets from a JSON array."""
        assert self._serial_draft is not None
        commands = _draft_command_list(self._serial_draft, CONF_SERIAL_COMMANDS)
        errors: dict[str, str] = {}
        default_json = _import_json_default(commands, _SERIAL_IMPORT_EXAMPLE)
        if user_input is not None:
            default_json = str(user_input.get("commands_json", default_json))
            parsed, parse_err = parse_serial_commands_json(default_json)
            if parse_err or parsed is None:
                errors["base"] = command_json_error_key(parse_err or "")
            else:
                slug_err = imported_commands_error(parsed)
                if slug_err:
                    errors["base"] = slug_err
                else:
                    self._serial_draft[CONF_SERIAL_COMMANDS] = parsed
                    return await self.async_step_serial_command_list()
        return self.async_show_form(
            step_id="serial_command_import",
            data_schema=vol.Schema(
                {vol.Required("commands_json", default=default_json): _MULTILINE}
            ),
            errors=errors,
        )

    async def async_step_edit_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        opts = self._opts()
        ports: list[dict[str, Any]] = list(opts.get(CONF_SERIAL_PORTS, []))
        if not ports:
            return await self.async_step_init()
        if user_input is not None:
            sid = user_input[CONF_SERIAL_ID]
            found = next(
                (p for p in ports if str(p.get(CONF_SERIAL_ID)) == str(sid)),
                None,
            )
            if found is None:
                return await self.async_step_init()
            self._serial_draft = copy.deepcopy(found)
            return await self.async_step_serial_name()
        choice_map = {
            p[CONF_SERIAL_ID]: str(p.get(CONF_SERIAL_NAME, p[CONF_SERIAL_ID]))[:80]
            for p in ports
        }
        return self.async_show_form(
            step_id="edit_serial",
            data_schema=vol.Schema({vol.Required(CONF_SERIAL_ID): vol.In(choice_map)}),
        )

    async def async_step_remove_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        opts = self._opts()
        ports: list[dict[str, Any]] = list(opts.get(CONF_SERIAL_PORTS, []))
        if not ports:
            return self.async_create_entry(title="", data=opts)
        if user_input is not None:
            sid = user_input[CONF_SERIAL_ID]
            new_list = [p for p in ports if p.get(CONF_SERIAL_ID) != sid]
            return self.async_create_entry(
                title="", data={**opts, CONF_SERIAL_PORTS: new_list}
            )
        choice_map = {
            p[CONF_SERIAL_ID]: str(p.get(CONF_SERIAL_NAME, p[CONF_SERIAL_ID]))[:80]
            for p in ports
        }
        return self.async_show_form(
            step_id="remove_serial",
            data_schema=vol.Schema({vol.Required(CONF_SERIAL_ID): vol.In(choice_map)}),
        )
