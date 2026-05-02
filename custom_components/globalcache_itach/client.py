"""Async TCP client for Global Caché iTach (port 4998)."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import TracebackType

_LOGGER = logging.getLogger(__name__)

CR = "\r"
COMPLETEIR_RE = re.compile(
    r"^completeir,(\d+):(\d+),(\d+)\s*$", re.IGNORECASE
)
BUSYIR_RE = re.compile(r"^busyIR,(\d+):(\d+),(\d+)\s*$", re.IGNORECASE)
UNKNOWN_RE = re.compile(r"^unknowncommand", re.IGNORECASE)
SENDIR_HEAD_RE = re.compile(
    r"^sendir,(\d+):(\d+),(\d+),",
    re.IGNORECASE,
)


class ItachError(Exception):
    """Protocol or device error."""


class ItachClient:
    """One TCP connection; serialized writes; background read loop."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        connect_timeout: float = 10.0,
        command_timeout: float = 30.0,
        connect_retries: int = 4,
        reconnect_backoff_initial: float = 0.4,
    ) -> None:
        self._host = host
        self._port = port
        self._connect_timeout = connect_timeout
        self._command_timeout = command_timeout
        self._connect_retries = max(1, connect_retries)
        self._reconnect_backoff_initial = reconnect_backoff_initial
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._line_queue: asyncio.Queue[str] = asyncio.Queue()
        self._write_lock = asyncio.Lock()
        self._closed = asyncio.Event()
        self._ir_callbacks: list[Callable[[str], Coroutine[None, None, None]]] = []

    def add_ir_received_callback(
        self, cb: Callable[[str], Coroutine[None, None, None]]
    ) -> Callable[[], None]:
        """Register async callback for unsolicited lines (e.g. IR receive / learner)."""

        self._ir_callbacks.append(cb)

        def remove() -> None:
            if cb in self._ir_callbacks:
                self._ir_callbacks.remove(cb)

        return remove

    def _close_writer_nawait(self) -> None:
        w = self._writer
        self._writer = None
        self._reader = None
        if w and not w.is_closing():
            try:
                w.close()
            except OSError:
                pass

    async def connect(self) -> None:
        """Open TCP connection with exponential backoff on transient failures."""
        if self._writer and not self._writer.is_closing():
            return
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None
        self._close_writer_nawait()
        delay = self._reconnect_backoff_initial
        last_err: BaseException | None = None
        for attempt in range(self._connect_retries):
            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port),
                    timeout=self._connect_timeout,
                )
            except OSError as err:
                last_err = err
                _LOGGER.debug(
                    "Connect attempt %s/%s failed: %s",
                    attempt + 1,
                    self._connect_retries,
                    err,
                )
            except TimeoutError as err:
                last_err = err
                _LOGGER.debug("Connect timeout (attempt %s)", attempt + 1)
            else:
                self._closed.clear()
                self._read_task = asyncio.create_task(self._read_loop())
                return
            if attempt + 1 < self._connect_retries:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 6.0)
        msg = f"Could not connect to {self._host}:{self._port}"
        raise ItachError(msg) from last_err

    async def disconnect(self) -> None:
        self._closed.set()
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
            self._writer = None
        self._reader = None
        while not self._line_queue.empty():
            try:
                self._line_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def __aenter__(self) -> ItachClient:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.disconnect()

    async def _read_loop(self) -> None:
        assert self._reader is not None
        buf = b""
        try:
            while not self._closed.is_set():
                chunk = await self._reader.read(4096)
                if not chunk:
                    _LOGGER.debug("iTach TCP read EOF")
                    break
                buf += chunk
                while CR.encode("ascii") in buf or b"\r" in buf:
                    sep = buf.find(b"\r")
                    if sep == -1:
                        break
                    line_b, buf = buf[:sep], buf[sep + 1 :]
                    try:
                        line = line_b.decode("ascii", errors="replace").strip()
                    except UnicodeDecodeError:
                        line = line_b.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    await self._dispatch_line(line)
        except asyncio.CancelledError:
            raise
        except OSError as err:
            _LOGGER.debug("Read loop ended: %s", err)
        finally:
            if not self._closed.is_set():
                self._close_writer_nawait()

    async def _dispatch_line(self, line: str) -> None:
        """Push to waiters or IR callbacks."""
        await self._line_queue.put(line)
        lower = line.lower()
        if lower.startswith("sendir,") or lower.startswith("ir,"):
            for cb in list(self._ir_callbacks):
                try:
                    await cb(line)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("IR callback failed")

    async def _drain_matching_until(
        self,
        predicate: Callable[[str], bool],
        timeout: float,
    ) -> list[str]:
        lines: list[str] = []
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                msg = "Timeout waiting for iTach response"
                raise TimeoutError(msg)
            try:
                line = await asyncio.wait_for(
                    self._line_queue.get(), timeout=min(remaining, 1.0)
                )
            except TimeoutError:
                continue
            lines.append(line)
            if predicate(line):
                return lines
            if UNKNOWN_RE.match(line):
                msg = f"iTach error: {line}"
                raise ItachError(msg)

    def _norm_cmd(self, command: str) -> bytes:
        c = command.strip()
        if not c.endswith(CR):
            c = c + CR
        return c.encode("ascii", errors="replace")

    async def send_raw(
        self,
        command: str,
        *,
        timeout: float | None = None,
        end_on: Callable[[str], bool] | None = None,
    ) -> list[str]:
        """Send one command line; collect responses until end_on or single line if None."""
        await self.connect()
        assert self._writer is not None
        tout = timeout if timeout is not None else self._command_timeout
        data = self._norm_cmd(command)
        lines: list[str] = []

        async with self._write_lock:
            self._writer.write(data)
            await self._writer.drain()
            if end_on is None:
                try:
                    line = await asyncio.wait_for(
                        self._line_queue.get(), timeout=tout
                    )
                    lines.append(line)
                except TimeoutError:
                    pass
                return lines
            return await self._drain_matching_until(end_on, tout)

    async def getdevices(self) -> list[str]:
        def end(line: str) -> bool:
            return line.strip().lower().startswith("endlistdevices")

        return await self.send_raw("getdevices", end_on=end, timeout=self._command_timeout)

    async def getversion(self, module: str = "0") -> list[str]:
        lines = await self.send_raw(f"getversion,{module}", timeout=self._command_timeout)
        return lines

    async def _await_completeir_after_write(
        self,
        wire: bytes,
        module: int,
        port: int,
        cmd_id: int,
    ) -> list[str]:
        """Write a sendir frame and wait for matching completeir (busyIR resend)."""
        mod_s, port_s = str(module), str(port)
        cid_s = str(cmd_id)

        def complete_match(line: str) -> bool:
            m = COMPLETEIR_RE.match(line.strip())
            if not m:
                return False
            return (
                m.group(1) == mod_s
                and m.group(2) == port_s
                and m.group(3) == cid_s
            )

        await self.connect()
        assert self._writer is not None
        collected: list[str] = []
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._command_timeout
        max_resends = 25

        async with self._write_lock:
            for _ in range(max_resends):
                if loop.time() > deadline:
                    break
                self._writer.write(wire)
                await self._writer.drain()
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        msg = "sendir timeout waiting for completeir"
                        raise ItachError(msg)
                    try:
                        line = await asyncio.wait_for(
                            self._line_queue.get(), timeout=min(remaining, 1.0)
                        )
                    except TimeoutError:
                        continue
                    collected.append(line)
                    if UNKNOWN_RE.match(line.strip()):
                        msg = f"sendir failed: {line}"
                        raise ItachError(msg)
                    if BUSYIR_RE.match(line.strip()):
                        await asyncio.sleep(0.05)
                        break
                    if complete_match(line):
                        return collected
        msg = "sendir aborted: timeout or too many busyIR retries"
        raise ItachError(msg)

    async def send_sendir(
        self,
        module: int,
        port: int,
        cmd_id: int,
        frequency: int,
        repeat: int,
        offset: int,
        pairs: list[int],
    ) -> list[str]:
        """Send sendir and wait for matching completeir (retries on busyIR)."""
        if len(pairs) % 2:
            msg = "sendir requires an even number of pulse counts"
            raise ValueError(msg)
        if len(pairs) >= 520:
            msg = "Too many pulse pairs for sendir"
            raise ValueError(msg)
        tail = ",".join(str(x) for x in pairs)
        body = f"{module}:{port},{cmd_id},{frequency},{repeat},{offset},{tail}"
        cmd = f"sendir,{body}"
        wire = self._norm_cmd(cmd)
        return await self._await_completeir_after_write(wire, module, port, cmd_id)

    async def send_full_sendir(self, command: str) -> list[str]:
        """Send a complete ``sendir,...`` line (no trailing CR in ``command``); await completeir."""
        s = command.strip()
        if not s.lower().startswith("sendir,"):
            msg = "full_sendir data must start with sendir,"
            raise ValueError(msg)
        m = SENDIR_HEAD_RE.match(s)
        if not m:
            msg = "Could not parse module:port and command id from sendir line"
            raise ValueError(msg)
        module = int(m.group(1))
        port = int(m.group(2))
        cmd_id = int(m.group(3))
        wire = self._norm_cmd(s)
        return await self._await_completeir_after_write(wire, module, port, cmd_id)

    async def send_raw_then_collect(
        self,
        command: str,
        *,
        collect_seconds: float = 2.0,
    ) -> list[str]:
        """Send a command and collect response lines for a short window."""
        await self.connect()
        assert self._writer is not None
        data = self._norm_cmd(command)
        lines: list[str] = []
        loop = asyncio.get_event_loop()
        deadline = loop.time() + collect_seconds
        async with self._write_lock:
            self._writer.write(data)
            await self._writer.drain()
            while loop.time() < deadline:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    line = await asyncio.wait_for(
                        self._line_queue.get(), timeout=min(remaining, 0.3)
                    )
                    lines.append(line)
                except TimeoutError:
                    continue
        return lines
