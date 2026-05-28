"""Persistent TCP listener for Global Caché serial data ports."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from .client import ItachClient, ItachError, serial_data_port

_LOGGER = logging.getLogger(__name__)

OnSerialData = Callable[[str, bool], Awaitable[None]]


class SerialPortSession:
    """Maintain one serial bridge connection (control port + module) with RX loop."""

    def __init__(
        self,
        client: ItachClient,
        *,
        serial_id: str,
        module: int,
        connector_port: int,
        settings: str,
        append_cr: bool,
        connect_timeout: float,
        command_timeout: float,
        on_data: OnSerialData,
    ) -> None:
        self.serial_id = serial_id
        self._client = client
        self._module = module
        self._connector_port = connector_port
        self._settings = settings.strip()
        self._append_cr = append_cr
        self._connect_timeout = connect_timeout
        self._command_timeout = command_timeout
        self._on_data = on_data
        self._host = client._host
        self._control_port = client._port
        self._data_port = serial_data_port(self._control_port, module)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._closed = asyncio.Event()
        self._write_lock = asyncio.Lock()
        self._rx_queue: asyncio.Queue[str] = asyncio.Queue()
        self._expecting_response = asyncio.Event()
        self._connected = False
        self.last_received: str = ""

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        """Connect, apply line settings, and start background RX."""
        self._closed.clear()
        await self._connect()
        if self._read_task is None:
            self._read_task = asyncio.create_task(self._read_loop())

    async def stop(self) -> None:
        self._closed.set()
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None
        await self._close()

    async def _connect(self) -> None:
        if self._settings:
            await self._client.set_serial_settings(
                self._module, self._connector_port, self._settings
            )
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._data_port),
            timeout=self._connect_timeout,
        )
        self._connected = True
        _LOGGER.debug(
            "Serial session %s connected on %s:%s",
            self.serial_id,
            self._host,
            self._data_port,
        )

    async def _close(self) -> None:
        self._connected = False
        w = self._writer
        self._writer = None
        self._reader = None
        if w and not w.is_closing():
            w.close()
            try:
                await w.wait_closed()
            except OSError:
                pass

    async def _read_loop(self) -> None:
        backoff = 0.5
        try:
            while not self._closed.is_set():
                if not self._connected or self._reader is None:
                    try:
                        await self._close()
                        await self._connect()
                        backoff = 0.5
                    except (TimeoutError, OSError, ItachError) as err:
                        _LOGGER.debug(
                            "Serial %s reconnect failed: %s", self.serial_id, err
                        )
                        await asyncio.sleep(min(backoff, 6.0))
                        backoff = min(backoff * 2, 6.0)
                        continue
                try:
                    chunk = await self._reader.read(4096)
                except OSError:
                    chunk = b""
                if not chunk:
                    _LOGGER.debug("Serial %s RX EOF", self.serial_id)
                    self._connected = False
                    await self._close()
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 6.0)
                    continue
                text = chunk.decode("ascii", errors="replace").strip()
                if not text:
                    continue
                self.last_received = text
                if self._expecting_response.is_set():
                    await self._rx_queue.put(text)
                else:
                    await self._on_data(text, False)
        except asyncio.CancelledError:
            raise
        finally:
            await self._close()

    async def send(
        self,
        payload: str,
        *,
        append_cr: bool | None = None,
        wait_response: bool = True,
        response_timeout: float | None = None,
    ) -> str:
        """Send on the listener connection; optionally wait for the next RX chunk."""
        use_cr = self._append_cr if append_cr is None else append_cr
        wire = payload.encode("ascii", errors="replace")
        if use_cr and not wire.endswith(b"\r"):
            wire += b"\r"
        if not self._connected:
            await self._connect()
        assert self._writer is not None
        tout = (
            response_timeout
            if response_timeout is not None
            else self._command_timeout
        )
        async with self._write_lock:
            if wait_response:
                while not self._rx_queue.empty():
                    try:
                        self._rx_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                self._expecting_response.set()
            try:
                self._writer.write(wire)
                await self._writer.drain()
            finally:
                if not wait_response:
                    self._expecting_response.clear()
            if not wait_response:
                return ""
            try:
                text = await asyncio.wait_for(self._rx_queue.get(), timeout=tout)
            except TimeoutError:
                return ""
            finally:
                self._expecting_response.clear()
            await self._on_data(text, True)
            return text
