"""Global Caché iTach TCP/IP integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from .const import (
    ATTR_COMMAND,
    ATTR_COMMAND_ID,
    ATTR_DEVICE_ID,
    ATTR_ENABLED,
    ATTR_FREQUENCY,
    ATTR_INTENSITY,
    ATTR_MODE,
    ATTR_MODULE,
    ATTR_OFFSET,
    ATTR_PORT,
    ATTR_PULSE_PAIRS,
    ATTR_RAMP,
    ATTR_REPEAT,
    ATTR_RESPONSE_LINES,
    CONF_COMMAND_TIMEOUT,
    CONF_CONNECT_TIMEOUT,
    CONF_HOST as CONF_GCI_HOST,
    CONF_PORT as CONF_GCI_PORT,
    DOMAIN,
    EVENT_IR_LEARNED,
    MANUFACTURER,
    SERVICE_GET_DEVICES,
    SERVICE_GET_IR,
    SERVICE_GET_LED_LIGHTING,
    SERVICE_GET_NET,
    SERVICE_GET_VERSION,
    SERVICE_IR_LEARNER_START,
    SERVICE_IR_LEARNER_STOP,
    SERVICE_RECEIVE_IR,
    SERVICE_SEND_COMMAND,
    SERVICE_SEND_RAW,
    SERVICE_SENDIR,
    SERVICE_SET_IR,
    SERVICE_SET_LED_LIGHTING,
    SERVICE_STOP_IR,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall

    from .coordinator import ItachCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["binary_sensor", "remote", "sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from UI."""
    from homeassistant.helpers import device_registry as dr

    from .coordinator import ItachCoordinator

    hass.data.setdefault(DOMAIN, {})
    coordinator = ItachCoordinator(hass, entry, dict(entry.data), dict(entry.options))
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer=MANUFACTURER,
        model=entry.data.get("model") or "iTach",
        sw_version=entry.data.get("firmware") or "",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _register_services(hass)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .coordinator import ItachCoordinator

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: ItachCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
        if not hass.data[DOMAIN]:
            _unregister_services(hass)
            hass.data.pop(DOMAIN)
    return unload_ok


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    from .client import ItachClient, ItachError
    from .coordinator import ItachCoordinator

    out: dict[str, Any] = {
        "data": dict(entry.data),
        "options": dict(entry.options),
    }
    coordinator: ItachCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator is not None:
        out["last_devices_response"] = coordinator.data
        out["coordinator"] = {
            "tcp_connected": coordinator.client.is_connected,
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None,
        }
    client = ItachClient(
        entry.data[CONF_GCI_HOST],
        int(entry.data[CONF_GCI_PORT]),
        connect_timeout=float(
            entry.options.get(CONF_CONNECT_TIMEOUT, 10.0),
        ),
        command_timeout=float(entry.options.get(CONF_COMMAND_TIMEOUT, 30.0)),
    )
    try:
        await client.connect()
        out["getversion"] = await client.send_raw(
            "getversion,0",
            end_on=lambda x: x.strip().lower().startswith("version,")
            or x.strip().lower().startswith("unknowncommand"),
            timeout=10.0,
        )
        out["get_net"] = await client.send_raw(
            "get_NET,0:1",
            end_on=lambda x: x.strip().upper().startswith("NET,")
            or x.strip().lower().startswith("unknowncommand"),
            timeout=10.0,
        )
    except (TimeoutError, OSError, ItachError) as err:
        out["probe_error"] = repr(err)
    finally:
        await client.disconnect()
    return out


def _device_id_from_call(call: ServiceCall) -> str:
    from homeassistant.exceptions import HomeAssistantError

    did = getattr(call, "device_id", None) or call.data.get(ATTR_DEVICE_ID)
    if did:
        return str(did)
    if getattr(call, "device_ids", None):
        return str(call.device_ids[0])
    msg = "device_id is required (pick the iTach gateway device)"
    raise HomeAssistantError(msg)


def _coordinator_for_service(hass: HomeAssistant, call: ServiceCall) -> ItachCoordinator:
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import device_registry as dr

    device_id = _device_id_from_call(call)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if not device:
        msg = "Unknown device_id"
        raise HomeAssistantError(msg)
    for ident in device.identifiers:
        if ident[0] == DOMAIN:
            entry_id = ident[1]
            return hass.data[DOMAIN][entry_id]
    msg = "Device is not an iTach gateway"
    raise HomeAssistantError(msg)


def _unregister_services(hass: HomeAssistant) -> None:
    for name in (
        SERVICE_SEND_RAW,
        SERVICE_SEND_COMMAND,
        SERVICE_SENDIR,
        SERVICE_STOP_IR,
        SERVICE_IR_LEARNER_START,
        SERVICE_IR_LEARNER_STOP,
        SERVICE_RECEIVE_IR,
        SERVICE_GET_IR,
        SERVICE_SET_IR,
        SERVICE_GET_DEVICES,
        SERVICE_GET_VERSION,
        SERVICE_GET_NET,
        SERVICE_SET_LED_LIGHTING,
        SERVICE_GET_LED_LIGHTING,
    ):
        if hass.services.has_service(DOMAIN, name):
            hass.services.async_remove(DOMAIN, name)


def _register_services(hass: HomeAssistant) -> None:
    from homeassistant.core import ServiceCall
    from homeassistant.helpers import config_validation as cv

    if hass.services.has_service(DOMAIN, SERVICE_SEND_RAW):
        return

    async def _send_raw(call: ServiceCall) -> dict[str, Any]:
        coord = _coordinator_for_service(hass, call)
        cmd = str(call.data[ATTR_COMMAND])
        collect = float(call.data.get("collect_seconds", 2.0))
        lines = await coord.client.send_raw_then_collect(
            cmd, collect_seconds=max(0.1, min(collect, 30.0))
        )
        return {ATTR_RESPONSE_LINES: lines}

    async def _sendir(call: ServiceCall) -> None:
        coord = _coordinator_for_service(hass, call)
        mod = int(call.data[ATTR_MODULE])
        port = int(call.data[ATTR_PORT])
        cmd_id = int(call.data[ATTR_COMMAND_ID])
        freq = int(call.data[ATTR_FREQUENCY])
        repeat = int(call.data[ATTR_REPEAT])
        offset = int(call.data[ATTR_OFFSET])
        pairs = call.data[ATTR_PULSE_PAIRS]
        if isinstance(pairs, str):
            from .pronto import parse_gc_pair_string

            plist = parse_gc_pair_string(pairs)
        else:
            plist = [int(x) for x in pairs]
        await coord.client.send_sendir(mod, port, cmd_id, freq, repeat, offset, plist)

    async def _stop_ir(call: ServiceCall) -> None:
        coord = _coordinator_for_service(hass, call)
        mod = int(call.data[ATTR_MODULE])
        port = int(call.data[ATTR_PORT])
        await coord.client.send_raw(
            f"stopir,{mod}:{port}",
            end_on=lambda l: l.strip().lower().startswith("stopir"),
            timeout=10.0,
        )

    async def _learner_start(call: ServiceCall) -> None:
        coord = _coordinator_for_service(hass, call)
        lines = await coord.client.send_raw(
            "get_IRL",
            end_on=lambda l: "learner" in l.lower() or "disabled" in l.lower(),
            timeout=10.0,
        )
        hass.bus.async_fire(
            EVENT_IR_LEARNED,
            {"device_id": _device_id_from_call(call), "phase": "start", "lines": lines},
        )

    async def _learner_stop(call: ServiceCall) -> None:
        coord = _coordinator_for_service(hass, call)
        lines = await coord.client.send_raw(
            "stop_IRL",
            end_on=lambda l: "learner" in l.lower() or "disabled" in l.lower(),
            timeout=10.0,
        )
        hass.bus.async_fire(
            EVENT_IR_LEARNED,
            {"device_id": _device_id_from_call(call), "phase": "stop", "lines": lines},
        )

    async def _receive_ir(call: ServiceCall) -> None:
        coord = _coordinator_for_service(hass, call)
        mod = int(call.data[ATTR_MODULE])
        port = int(call.data[ATTR_PORT])
        en = "enabled" if call.data[ATTR_ENABLED] else "disabled"
        await coord.client.send_raw(
            f"receiveIR,{mod}:{port},{en}",
            end_on=lambda l: l.strip().lower().startswith("receiveir"),
            timeout=10.0,
        )
        if call.data[ATTR_ENABLED]:
            coord.enable_ir_receive_events()

    async def _get_ir(call: ServiceCall) -> dict[str, Any]:
        coord = _coordinator_for_service(hass, call)
        mod = int(call.data[ATTR_MODULE])
        port = int(call.data[ATTR_PORT])
        lines = await coord.client.send_raw(
            f"get_IR,{mod}:{port}",
            end_on=lambda l: l.strip().upper().startswith("IR,")
            or l.strip().lower().startswith("unknowncommand"),
            timeout=10.0,
        )
        return {"lines": lines}

    async def _set_ir(call: ServiceCall) -> dict[str, Any]:
        coord = _coordinator_for_service(hass, call)
        mod = int(call.data[ATTR_MODULE])
        port = int(call.data[ATTR_PORT])
        mode = str(call.data[ATTR_MODE])
        lines = await coord.client.send_raw(
            f"set_IR,{mod}:{port},{mode}",
            end_on=lambda l: l.strip().upper().startswith("IR,")
            or l.strip().lower().startswith("unknowncommand"),
            timeout=10.0,
        )
        return {"lines": lines}

    async def _get_devices(call: ServiceCall) -> dict[str, Any]:
        coord = _coordinator_for_service(hass, call)
        return {"lines": await coord.client.getdevices()}

    async def _get_version(call: ServiceCall) -> dict[str, Any]:
        coord = _coordinator_for_service(hass, call)
        mod = str(call.data.get("module", "0"))
        lines = await coord.client.send_raw(
            f"getversion,{mod}",
            end_on=lambda l: l.strip().lower().startswith("version,")
            or l.strip().lower().startswith("unknowncommand"),
            timeout=10.0,
        )
        return {"lines": lines}

    async def _get_net(call: ServiceCall) -> dict[str, Any]:
        coord = _coordinator_for_service(hass, call)
        lines = await coord.client.send_raw(
            "get_NET,0:1",
            end_on=lambda l: l.strip().upper().startswith("NET,")
            or l.strip().lower().startswith("unknowncommand"),
            timeout=10.0,
        )
        return {"lines": lines}

    async def _set_led_lighting(call: ServiceCall) -> dict[str, Any]:
        coord = _coordinator_for_service(hass, call)
        mod = int(call.data[ATTR_MODULE])
        port = int(call.data[ATTR_PORT])
        intensity = int(call.data[ATTR_INTENSITY])
        ramp = int(call.data.get(ATTR_RAMP, 0))
        cmd = f"set_LED_LIGHTING,{mod}:{port},{intensity},{ramp}"
        lines = await coord.client.send_raw(
            cmd,
            end_on=lambda l: l.strip().upper().startswith("LED_LIGHTING,")
            or l.strip().lower().startswith("unknowncommand"),
            timeout=10.0,
        )
        return {"lines": lines}

    async def _get_led_lighting(call: ServiceCall) -> dict[str, Any]:
        coord = _coordinator_for_service(hass, call)
        mod = int(call.data[ATTR_MODULE])
        port = int(call.data[ATTR_PORT])
        lines = await coord.client.send_raw(
            f"get_LED_LIGHTING,{mod}:{port}",
            end_on=lambda l: l.strip().upper().startswith("LED_LIGHTING,")
            or l.strip().lower().startswith("unknowncommand"),
            timeout=10.0,
        )
        return {"lines": lines}

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_RAW,
        _send_raw,
        schema=vol.Schema(
            {
                vol.Required(ATTR_DEVICE_ID): cv.string,
                vol.Required(ATTR_COMMAND): cv.string,
                vol.Optional("collect_seconds", default=2.0): vol.Coerce(float),
            }
        ),
        supports_response=True,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_COMMAND,
        _send_raw,
        schema=vol.Schema(
            {
                vol.Required(ATTR_DEVICE_ID): cv.string,
                vol.Required(ATTR_COMMAND): cv.string,
                vol.Optional("collect_seconds", default=2.0): vol.Coerce(float),
            }
        ),
        supports_response=True,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SENDIR,
        _sendir,
        schema=vol.Schema(
            {
                vol.Required(ATTR_DEVICE_ID): cv.string,
                vol.Required(ATTR_MODULE): vol.Coerce(int),
                vol.Required(ATTR_PORT): vol.Coerce(int),
                vol.Required(ATTR_COMMAND_ID): vol.Coerce(int),
                vol.Required(ATTR_FREQUENCY): vol.Coerce(int),
                vol.Required(ATTR_REPEAT): vol.Coerce(int),
                vol.Required(ATTR_OFFSET): vol.Coerce(int),
                vol.Required(ATTR_PULSE_PAIRS): vol.Any(
                    cv.string, vol.All(cv.ensure_list, [vol.Coerce(int)])
                ),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_STOP_IR,
        _stop_ir,
        schema=vol.Schema(
            {
                vol.Required(ATTR_DEVICE_ID): cv.string,
                vol.Required(ATTR_MODULE): vol.Coerce(int),
                vol.Required(ATTR_PORT): vol.Coerce(int),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_IR_LEARNER_START,
        _learner_start,
        schema=vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_IR_LEARNER_STOP,
        _learner_stop,
        schema=vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RECEIVE_IR,
        _receive_ir,
        schema=vol.Schema(
            {
                vol.Required(ATTR_DEVICE_ID): cv.string,
                vol.Required(ATTR_MODULE): vol.Coerce(int),
                vol.Required(ATTR_PORT): vol.Coerce(int),
                vol.Required(ATTR_ENABLED): cv.boolean,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_IR,
        _get_ir,
        schema=vol.Schema(
            {
                vol.Required(ATTR_DEVICE_ID): cv.string,
                vol.Required(ATTR_MODULE): vol.Coerce(int),
                vol.Required(ATTR_PORT): vol.Coerce(int),
            }
        ),
        supports_response=True,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_IR,
        _set_ir,
        schema=vol.Schema(
            {
                vol.Required(ATTR_DEVICE_ID): cv.string,
                vol.Required(ATTR_MODULE): vol.Coerce(int),
                vol.Required(ATTR_PORT): vol.Coerce(int),
                vol.Required(ATTR_MODE): cv.string,
            }
        ),
        supports_response=True,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_DEVICES,
        _get_devices,
        schema=vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string}),
        supports_response=True,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_VERSION,
        _get_version,
        schema=vol.Schema(
            {
                vol.Required(ATTR_DEVICE_ID): cv.string,
                vol.Optional("module", default="0"): cv.string,
            }
        ),
        supports_response=True,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_NET,
        _get_net,
        schema=vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string}),
        supports_response=True,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_LED_LIGHTING,
        _set_led_lighting,
        schema=vol.Schema(
            {
                vol.Required(ATTR_DEVICE_ID): cv.string,
                vol.Required(ATTR_MODULE): vol.Coerce(int),
                vol.Required(ATTR_PORT): vol.Coerce(int),
                vol.Required(ATTR_INTENSITY): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=100)
                ),
                vol.Optional(ATTR_RAMP, default=0): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=10)
                ),
            }
        ),
        supports_response=True,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_LED_LIGHTING,
        _get_led_lighting,
        schema=vol.Schema(
            {
                vol.Required(ATTR_DEVICE_ID): cv.string,
                vol.Required(ATTR_MODULE): vol.Coerce(int),
                vol.Required(ATTR_PORT): vol.Coerce(int),
            }
        ),
        supports_response=True,
    )
