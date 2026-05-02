"""Config and options flow."""

from __future__ import annotations

import copy
import json
import logging
import uuid
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .client import ItachClient, ItachError
from .command_util import parse_commands_json
from .const import (
    CONF_CMD_DATA,
    CONF_CMD_FORMAT,
    CONF_CMD_NAME,
    CONF_COMMAND_TIMEOUT,
    CONF_COMMANDS,
    CONF_CONN_PORT,
    CONF_CONNECT_TIMEOUT,
    CONF_DEFAULT_FREQ,
    CONF_DEFAULT_OFFSET,
    CONF_DEFAULT_REPEAT,
    CONF_DEVICE_NAME,
    CONF_FIXED_COMMAND_ID,
    CONF_HOST,
    CONF_ID_POLICY,
    CONF_IR_COUNT,
    CONF_MODULE,
    CONF_PORT,
    CONF_REMOTE_ID,
    CONF_REMOTE_NAME,
    CONF_REMOTES,
    DEFAULT_COMMAND_TIMEOUT,
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
        model = "iTach"
        for ln in lines:
            low = ln.lower()
            if "ip2ir" in low or "wf2ir" in low or "ir" in low:
                model = ln.strip()[:80]
                break
        ver_lines = await client.send_raw(
            "getversion,0",
            end_on=lambda x: x.strip().lower().startswith("version,")
            or x.strip().lower().startswith("unknowncommand"),
            timeout=10.0,
        )
        fw = ""
        for ln in ver_lines:
            if ln.strip().lower().startswith("version,"):
                fw = ln.strip()
                break
    except (TimeoutError, OSError, ItachError) as err:
        _LOGGER.warning("iTach validation failed: %s", err)
        raise vol.Invalid("Could not connect or query the iTach device") from err
    finally:
        await client.disconnect()
    return {"model": model, "firmware": fw}


class GlobalCacheItachConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle first-time UI setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await _validate_connection(self.hass, user_input)
            except vol.Invalid:
                errors["base"] = "cannot_connect"
            else:
                host = user_input[CONF_HOST].strip().lower()
                port = int(user_input[CONF_PORT])
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
                    },
                    options=opts,
                )
        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
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
    }


class GlobalCacheItachOptionsFlow(OptionsFlow):
    """Options and remote/command editor."""

    def __init__(self) -> None:
        """Initialize draft state for add-remote wizard steps."""
        self._remote_draft: dict[str, Any] | None = None

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
        # Use vol.In (not SelectSelector dict options) for broad HA version compatibility.
        opts = self._opts()
        remotes: list[dict[str, Any]] = list(opts.get(CONF_REMOTES, []))
        menu = {
            "ir_defaults": "IR defaults",
            "timeouts": "Timeouts",
            "add_remote": "Add remote",
            "edit_remote": "Edit remote",
            "remove_remote": "Remove remote",
        }
        if not remotes:
            menu.pop("edit_remote", None)
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
            return await self.async_step_remote_commands()
        dm = int(self._remote_draft.get(CONF_MODULE, 1))
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
        )

    async def async_step_remote_commands(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        assert self._remote_draft is not None
        if user_input is not None:
            raw = user_input["commands_json"]
            commands, parse_err = parse_commands_json(raw)
            if parse_err:
                errors["base"] = parse_err
            elif commands is not None:
                opts = self._opts()
                remotes = list(opts.get(CONF_REMOTES, []))
                existing_id = self._remote_draft.get(CONF_REMOTE_ID)
                new_remote: dict[str, Any] = {
                    CONF_REMOTE_ID: str(existing_id)
                    if existing_id
                    else str(uuid.uuid4()),
                    CONF_REMOTE_NAME: self._remote_draft[CONF_REMOTE_NAME],
                    CONF_MODULE: int(self._remote_draft[CONF_MODULE]),
                    CONF_CONN_PORT: int(self._remote_draft[CONF_CONN_PORT]),
                    CONF_IR_COUNT: int(self._remote_draft[CONF_IR_COUNT]),
                    CONF_COMMANDS: commands,
                }
                if existing_id:
                    eid = str(existing_id)
                    remotes = [
                        new_remote if str(r.get(CONF_REMOTE_ID)) == eid else r
                        for r in remotes
                    ]
                else:
                    remotes.append(new_remote)
                new_opts = {**opts, CONF_REMOTES: remotes}
                self._remote_draft = None
                return self.async_create_entry(title="", data=new_opts)
        existing_cmds = self._remote_draft.get(CONF_COMMANDS)
        if isinstance(existing_cmds, list) and existing_cmds:
            default_json = json.dumps(existing_cmds, indent=2)
        else:
            default_json = json.dumps(
                [
                    {
                        CONF_CMD_NAME: "power",
                        CONF_CMD_FORMAT: "pronto",
                        CONF_CMD_DATA: "0000 006D 0001 0010 00AC 00AC 0015 0040",
                    }
                ],
                indent=2,
            )
        return self.async_show_form(
            step_id="remote_commands",
            data_schema=vol.Schema(
                {
                    vol.Required("commands_json", default=default_json): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "hint": "JSON array of {name, data, format}. "
                "format: pronto | gc_pairs | full_sendir (aliases pronto_hex, gc_sendir_tail). "
                "Optional per-command freq, repeat, offset, command_id."
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
