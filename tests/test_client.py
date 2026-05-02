"""Protocol tests with a fake iTach TCP server."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.globalcache_itach.client import ItachClient, ItachError


async def _fake_itach_handler(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    try:
        while True:
            raw = await reader.readuntil(b"\r")
            if not raw:
                break
            cmd = raw.decode("ascii", errors="replace").strip().lower()
            if cmd == "getdevices":
                writer.write(
                    b"device,0,0,ETHERNET,device,1,3,IR\rendlistdevices\r"
                )
            elif cmd.startswith("sendir,1:1,7,"):
                writer.write(b"completeir,1:1,7\r")
            elif cmd.startswith("sendir,1:1,42,"):
                writer.write(b"completeir,1:1,42\r")
            elif cmd.startswith("sendir,1:1,9,"):
                writer.write(b"busyIR,1:1,0\r")
                await asyncio.sleep(0.01)
                writer.write(b"completeir,1:1,9\r")
            elif cmd == "getversion,0":
                writer.write(b"version,0,TESTFW\r")
            else:
                writer.write(b"unknowncommand,INVALID\r")
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        writer.close()


@pytest.fixture
async def fake_server() -> tuple[str, int]:
    server = await asyncio.start_server(_fake_itach_handler, "127.0.0.1", 0)
    sockets = server.sockets
    assert sockets
    port = sockets[0].getsockname()[1]
    yield "127.0.0.1", port
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_getdevices(fake_server: tuple[str, int]) -> None:
    host, port = fake_server
    client = ItachClient(host, port, connect_timeout=2.0, command_timeout=2.0)
    async with client:
        lines = await client.getdevices()
    joined = " ".join(lines).lower()
    assert "endlistdevices" in joined
    assert "device" in joined


@pytest.mark.asyncio
async def test_send_sendir_completeir(fake_server: tuple[str, int]) -> None:
    host, port = fake_server
    client = ItachClient(host, port, connect_timeout=2.0, command_timeout=2.0)
    async with client:
        lines = await client.send_sendir(1, 1, 7, 38000, 1, 1, [10, 20])
    assert any("completeir" in ln.lower() for ln in lines)


@pytest.mark.asyncio
async def test_send_raw_then_collect(fake_server: tuple[str, int]) -> None:
    host, port = fake_server
    client = ItachClient(host, port, connect_timeout=2.0, command_timeout=2.0)
    async with client:
        lines = await client.send_raw_then_collect("getversion,0", collect_seconds=0.5)
    assert any("version" in ln.lower() for ln in lines)


@pytest.mark.asyncio
async def test_send_full_sendir_line(fake_server: tuple[str, int]) -> None:
    host, port = fake_server
    client = ItachClient(host, port, connect_timeout=2.0, command_timeout=2.0)
    async with client:
        lines = await client.send_full_sendir(
            "sendir,1:1,42,38000,1,1,10,20"
        )
    assert any("completeir" in ln.lower() for ln in lines)


@pytest.mark.asyncio
async def test_unknowncommand_raises(fake_server: tuple[str, int]) -> None:
    host, port = fake_server
    client = ItachClient(host, port, connect_timeout=2.0, command_timeout=2.0)
    async with client:
        with pytest.raises(ItachError):
            await client.send_sendir(1, 1, 99, 38000, 1, 1, [1, 2])
