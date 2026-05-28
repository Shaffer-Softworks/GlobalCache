"""Relay and serial client tests."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.globalcache_itach.client import (
    ItachClient,
    parse_relay_state_line,
    serial_data_port,
)


def test_parse_relay_state_line() -> None:
    assert parse_relay_state_line("setstate,3:1,1") is True
    assert parse_relay_state_line("setstate,3:2,0") is False
    # GC-100-12 uses ``state,...`` responses (not ``setstate,...``).
    assert parse_relay_state_line("state,3:1,1") is True
    assert parse_relay_state_line("state,3:2,0") is False


def test_serial_data_port() -> None:
    assert serial_data_port(4998, 1) == 4999
    assert serial_data_port(5000, 2) == 5002


async def _control_handler(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    try:
        while True:
            raw = await reader.readuntil(b"\r")
            cmd = raw.decode("ascii").strip().lower()
            if cmd == "getstate,3:1":
                writer.write(b"state,3:1,0\r")
            elif cmd == "setstate,3:1,1":
                writer.write(b"state,3:1,1\r")
            elif cmd == "get_serial,1:1":
                writer.write(b"SERIAL,9600,FLOW_NONE,PARITY_NONE\r")
            elif cmd.startswith("set_serial,1:1,"):
                writer.write(b"SERIAL,19200,FLOW_NONE,PARITY_NONE\r")
            else:
                writer.write(b"unknowncommand,INVALID\r")
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        writer.close()


async def _serial_data_handler(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    try:
        raw = await reader.readuntil(b"\r")
        payload = raw.decode("ascii").strip()
        writer.write(f"echo:{payload}\r".encode("ascii"))
        await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        writer.close()


@pytest.fixture
async def relay_serial_servers() -> tuple[str, int]:
    control = await asyncio.start_server(_control_handler, "127.0.0.1", 0)
    cs = control.sockets
    assert cs
    base_port = cs[0].getsockname()[1]
    data_port = base_port + 1
    data_srv = await asyncio.start_server(
        _serial_data_handler, "127.0.0.1", data_port
    )
    yield "127.0.0.1", base_port
    control.close()
    await control.wait_closed()
    data_srv.close()
    await data_srv.wait_closed()


@pytest.mark.asyncio
async def test_relay_state(relay_serial_servers: tuple[str, int]) -> None:
    host, port = relay_serial_servers
    client = ItachClient(host, port, connect_timeout=2.0, command_timeout=2.0)
    async with client:
        assert await client.get_relay_state(3, 1) is False
        assert await client.set_relay_state(3, 1, True) is True


@pytest.mark.asyncio
async def test_serial_settings_and_payload(
    relay_serial_servers: tuple[str, int],
) -> None:
    host, port = relay_serial_servers
    client = ItachClient(host, port, connect_timeout=2.0, command_timeout=2.0)
    async with client:
        line = await client.get_serial_settings(1, 1)
        assert line.upper().startswith("SERIAL,")
        line2 = await client.set_serial_settings(1, 1, "19200,FLOW_NONE,PARITY_NONE")
        assert "19200" in line2
        resp = await client.send_serial_payload(1, "PING", append_cr=True)
        assert resp == "echo:PING"
