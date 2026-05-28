"""SerialPortSession listener tests."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.globalcache_itach.client import ItachClient
from custom_components.globalcache_itach.serial_session import SerialPortSession


async def _control_handler(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    try:
        while True:
            raw = await reader.readuntil(b"\r")
            cmd = raw.decode("ascii").strip().lower()
            if cmd.startswith("set_serial,1:1,"):
                writer.write(b"SERIAL,9600,FLOW_NONE,PARITY_NONE\r")
            else:
                writer.write(b"unknowncommand,INVALID\r")
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        writer.close()


async def _listening_data_handler(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    writer.write(b"hello\r")
    await writer.drain()
    try:
        while True:
            raw = await reader.readuntil(b"\r")
            payload = raw.decode("ascii").strip()
            writer.write(f"echo:{payload}\r".encode("ascii"))
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        writer.close()


@pytest.fixture
async def serial_listener_servers() -> tuple[str, int]:
    control = await asyncio.start_server(_control_handler, "127.0.0.1", 0)
    cs = control.sockets
    assert cs
    base_port = cs[0].getsockname()[1]
    data_port = base_port + 1
    data_srv = await asyncio.start_server(
        _listening_data_handler, "127.0.0.1", data_port
    )
    yield "127.0.0.1", base_port
    control.close()
    await control.wait_closed()
    data_srv.close()
    await data_srv.wait_closed()


@pytest.mark.asyncio
async def test_serial_session_unsolicited_and_send(
    serial_listener_servers: tuple[str, int],
) -> None:
    host, port = serial_listener_servers
    received: list[tuple[str, bool]] = []

    async def on_data(data: str, is_response: bool) -> None:
        received.append((data, is_response))

    client = ItachClient(host, port, connect_timeout=2.0, command_timeout=2.0)
    await client.connect()
    session = SerialPortSession(
        client,
        serial_id="s1",
        module=1,
        connector_port=1,
        settings="9600,FLOW_NONE,PARITY_NONE",
        append_cr=True,
        connect_timeout=2.0,
        command_timeout=2.0,
        on_data=on_data,
    )
    try:
        await session.start()
        await asyncio.sleep(0.15)
        assert ("hello", False) in received
        resp = await session.send("PING")
        assert resp == "echo:PING"
        assert ("echo:PING", True) in received
        assert session.last_received == "echo:PING"
    finally:
        await session.stop()
        await client.disconnect()
