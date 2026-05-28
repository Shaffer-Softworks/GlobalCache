"""UDP multicast discovery for Global Caché iTach / GC-100 gateways."""

from __future__ import annotations

import asyncio
import logging
import re
import socket
import struct
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from homeassistant import config_entries

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

MULTICAST_GROUP = "239.255.250.250"
DISCOVERY_PORT = 9131
DISCOVERY_MANAGER_KEY = "_discovery_manager"

BEACON_FIELD_RE = re.compile(r"<-([^=]+)=([^>]*)>")
CONFIG_URL_HOST_RE = re.compile(r"https?://([^/:]+)", re.IGNORECASE)

# Sample beacons from Global Caché API docs (iTach, Flex).
ITACH_BEACON = (
    b"AMXB<-UUID=GlobalCache_000C1E024239><-SDKClass=Utility>"
    b"<-Make=GlobalCache><-Model=iTachIP2IR><-Revision=710-1001-05>"
    b"<-Pkg_Level=GCPK001><-Config-URL=http://192.168.1.100.>"
    b"<-PCB_PN=025-0026-06><-Status=Ready>"
)
FLEX_BEACON = (
    b"AMXB<-UUID=GlobalCache_000C1E04E5D9><-Make=GlobalCache>"
    b"<-Line=iTachFlex><-Model=iTachFlexEthernet><-Revision=710-3000-24>"
    b"<-PCB_PN=025-0034-13><-I/O_Class=IR><-I/O_ID=FLC-2E1B>"
    b"<-Status=Net:Eth-Up,Host:OK><-Config-URL=http://192.168.0.147>"
    b"<-Config-Ver=GC_16.1.0><-SDKClass=Utility>"
)


@dataclass(slots=True)
class BeaconInfo:
    """Parsed Global Caché UDP discovery beacon."""

    uuid: str
    model: str
    host: str
    revision: str = ""
    status: str = ""
    make: str = ""

    @property
    def unique_id(self) -> str:
        """Stable config-entry unique ID derived from device MAC."""
        return normalize_unique_id(self.uuid)

    def as_dict(self) -> dict[str, str]:
        """Serialize for config-flow discovery data."""
        return {k: str(v) for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BeaconInfo:
        """Rebuild from config-flow discovery data."""
        return cls(
            uuid=str(data.get("uuid", "")),
            model=str(data.get("model", "")),
            host=str(data.get("host", "")),
            revision=str(data.get("revision", "")),
            status=str(data.get("status", "")),
            make=str(data.get("make", "")),
        )


def normalize_unique_id(value: str) -> str:
    """Normalize UUID or MAC to ``GlobalCache_XXXXXXXXXXXX``."""
    cleaned = value.strip()
    if not cleaned:
        return cleaned
    if cleaned.lower().startswith("globalcache_"):
        mac = cleaned.split("_", 1)[1]
    else:
        mac = cleaned
    mac = mac.replace(":", "").replace("-", "").upper()
    return f"GlobalCache_{mac}"


def host_from_config_url(config_url: str) -> str | None:
    """Extract host/IP from a Config-URL beacon field."""
    if not config_url:
        return None
    match = CONFIG_URL_HOST_RE.search(config_url.strip())
    if match:
        return match.group(1).rstrip(".")
    parsed = urlparse(config_url if "://" in config_url else f"http://{config_url}")
    if parsed.hostname:
        return parsed.hostname.rstrip(".")
    return None


def parse_beacon(data: bytes, source_host: str | None = None) -> BeaconInfo | None:
    """Parse an AMXB UDP beacon packet."""
    try:
        text = data.decode("utf-8", errors="replace")
    except (UnicodeDecodeError, AttributeError):
        return None
    if not text.startswith("AMXB"):
        return None
    fields = {
        match.group(1): match.group(2).strip()
        for match in BEACON_FIELD_RE.finditer(text)
    }
    uuid = fields.get("UUID")
    if not uuid:
        return None
    host = host_from_config_url(fields.get("Config-URL", "")) or source_host
    if not host:
        return None
    return BeaconInfo(
        uuid=uuid,
        model=fields.get("Model", ""),
        host=host,
        revision=fields.get("Revision", ""),
        status=fields.get("Status", ""),
        make=fields.get("Make", ""),
    )


class DiscoveryManager:
    """Background UDP multicast listener and active-scan coordinator."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._sock: socket.socket | None = None
        self._listen_task: asyncio.Task[None] | None = None
        self._scan_collectors: list[dict[str, Any]] = []
        self._recent_beacons: dict[str, float] = {}
        self._debounce_seconds = 60.0

    async def async_setup(self) -> None:
        """Start the multicast listener."""
        if self._listen_task is not None:
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", DISCOVERY_PORT))
            group = socket.inet_aton(MULTICAST_GROUP)
            mreq = struct.pack("4sL", group, socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.setblocking(False)
        except OSError as err:
            _LOGGER.debug("Global Caché UDP discovery unavailable: %s", err)
            return
        self._sock = sock
        self._listen_task = self._hass.async_create_task(
            self._listen_loop(), name="globalcache_itach_discovery"
        )

    async def async_shutdown(self) -> None:
        """Stop the listener and close the socket."""
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    async def async_scan(self, timeout: float = 5.0) -> list[BeaconInfo]:
        """Collect beacons for a short period (deduplicated by unique ID)."""
        await self.async_setup()
        if self._sock is None:
            return []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        found: dict[str, BeaconInfo] = {}
        collector: dict[str, Any] = {"deadline": deadline, "found": found}
        self._scan_collectors.append(collector)
        try:
            while loop.time() < deadline:
                await asyncio.sleep(min(0.25, deadline - loop.time()))
        finally:
            if collector in self._scan_collectors:
                self._scan_collectors.remove(collector)
        return list(found.values())

    async def _listen_loop(self) -> None:
        """Read UDP packets until cancelled."""
        assert self._sock is not None
        loop = asyncio.get_running_loop()
        while True:
            try:
                data, addr = await loop.sock_recvfrom(self._sock, 4096)
            except asyncio.CancelledError:
                raise
            except OSError as err:
                _LOGGER.debug("Global Caché discovery recv error: %s", err)
                await asyncio.sleep(1.0)
                continue
            source_host = addr[0] if addr else None
            beacon = parse_beacon(data, source_host)
            if beacon is None:
                continue
            self._dispatch_beacon(beacon)

    def _dispatch_beacon(self, beacon: BeaconInfo) -> None:
        """Route beacon to active scans and config-flow discovery."""
        loop = asyncio.get_running_loop()
        now = loop.time()
        unique_id = beacon.unique_id
        for collector in self._scan_collectors:
            if now <= collector["deadline"]:
                collector["found"][unique_id] = beacon
        last = self._recent_beacons.get(unique_id)
        if last is not None and (now - last) < self._debounce_seconds:
            return
        self._recent_beacons[unique_id] = now
        self._hass.async_create_task(
            self._async_handle_beacon(beacon),
            name=f"globalcache_itach_beacon_{unique_id}",
        )

    async def _async_handle_beacon(self, beacon: BeaconInfo) -> None:
        """Start a discovery config flow for a newly seen gateway."""
        unique_id = beacon.unique_id
        for entry in self._hass.config_entries.async_entries(DOMAIN):
            if entry.unique_id == unique_id:
                if entry.data.get("host") != beacon.host:
                    self._hass.config_entries.async_update_entry(
                        entry,
                        data={**entry.data, "host": beacon.host},
                    )
                    await self._hass.config_entries.async_reload(entry.entry_id)
                return
        await self._hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_DISCOVERY},
            data=beacon.as_dict(),
        )


def _get_manager(hass: HomeAssistant) -> DiscoveryManager:
    """Return the shared discovery manager, creating it if needed."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    manager = domain_data.get(DISCOVERY_MANAGER_KEY)
    if manager is None:
        manager = DiscoveryManager(hass)
        domain_data[DISCOVERY_MANAGER_KEY] = manager
    return manager


async def async_setup_discovery(hass: HomeAssistant) -> None:
    """Start UDP discovery when the integration domain loads."""
    await _get_manager(hass).async_setup()


async def async_shutdown_discovery(hass: HomeAssistant) -> None:
    """Stop UDP discovery."""
    domain_data = hass.data.get(DOMAIN, {})
    manager = domain_data.pop(DISCOVERY_MANAGER_KEY, None)
    if manager is not None:
        await manager.async_shutdown()


async def async_scan_beacons(
    hass: HomeAssistant, timeout: float = 5.0
) -> list[BeaconInfo]:
    """Actively listen for Global Caché beacons."""
    return await _get_manager(hass).async_scan(timeout)
