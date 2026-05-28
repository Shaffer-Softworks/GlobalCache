# Global Caché iTach (Home Assistant custom integration)

HACS-ready integration for **Global Caché iTach** TCP/IP gateways (e.g. IP2IR, IP2CC, IP2SL). It adds a **config flow** (including optional **connect timeout**), **options flow** for IR defaults, remotes, **relays**, and **serial ports**, **`remote`** entities (Pronto, GC pulse pairs, or full **`sendir`** lines), **`switch`** / **`text`** / **`button`** entities for relay and serial connectors, **`async_stop`** / **STOP** when supported by Home Assistant, diagnostic sensors, and **services** covering the TCP API (IR, LED, relay, serial, and raw lines).

Minimum Home Assistant version: **2024.1** (see [`hacs.json`](hacs.json)). The integration is declared as a **`hub`** (gateway) so Home Assistant does not offer a broken **“Add device”** device-subentry flow for a single-purpose TCP bridge.

**Diagnostics in the UI:** the gateway device exposes **TCP connected** (binary), **Last gateway poll** (timestamp, UTC), **Configured remotes** (count), and an optional **Gateway diagnostics** sensor (off by default—enable it to see raw `getdevices` / `getversion` text plus host/port in attributes). On the device page use **⋮ → Download diagnostics** for a JSON bundle (entry data/options, last coordinator snapshot, and live `getversion` / `get_NET` probes).

## Install

1. Copy [`custom_components/globalcache_itach`](custom_components/globalcache_itach) into your Home Assistant `config/custom_components/` directory, or add this repository to **HACS** as a custom repository (type: Integration).
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration** and search for **Global Caché iTach**.

## Run with Docker

From the repository root (Docker Desktop or another engine with Compose v2):

```bash
docker compose up -d
```

Open [http://localhost:8123](http://localhost:8123), complete the onboarding wizard, then add **Global Caché iTach** under **Settings → Devices & services**.

- **Config volume**: [`docker_data/config`](docker_data/config) stores Home Assistant’s full `/config` (ignored by git except [`.gitkeep`](docker_data/config/.gitkeep)).
- **Integration mount**: the container bind-mounts [`custom_components/globalcache_itach`](custom_components/globalcache_itach) into `/config/custom_components/globalcache_itach` read-only so edits in the repo are visible after **Developer tools → YAML → Restart** (or a container restart).

```bash
docker compose logs -f homeassistant
docker compose down
```

To use **mDNS / discovery** for devices on your LAN from the container, you may need `network_mode: host` (Linux only) or extra `cap_add` / macvlan setups; for a fixed iTach IP, bridge networking is usually enough.

## Configure

- **First step**: host (IP, hostname, or mDNS name), TCP port (default **4998**), optional friendly name, optional **connect timeout**. The integration validates the device with `getdevices` / `getversion`.
- **Change IP later**: **Settings → Devices & services → Global Caché iTach** → **⋮ → Reconfigure** (updates host/port, re-probes `getdevices`, and reloads the entry).
- **Options** (gear on the integration card):
  - **IR defaults**: carrier frequency, repeat, offset, sendir ID policy (auto-increment vs fixed).
  - **Timeouts**: connect and command timeouts.
  - **Add remote**: name, module/port (e.g. `1` and `2` for connector **1:2**), repeat multiplier, and a **JSON array** of commands.
  - **Edit remote**: pick an existing remote, then adjust name, connector, repeat multiplier, or the same **JSON command list** (saving replaces that remote; entity IDs stay stable).

### Command JSON format

Each command is an object:

| Field | Required | Description |
|--------|----------|-------------|
| `name` | yes | Button entity label; matched case-insensitively in services if needed. |
| `data` | yes | Pronto hex, comma-separated GC pulse pairs, or a full `sendir,...` line (no trailing CR) when using `full_sendir`. |
| `format` | no | `pronto` (alias `pronto_hex`), `gc_pairs` (alias `gc_sendir_tail`), or `full_sendir`. |
| `freq`, `repeat`, `offset`, `command_id` | no | Overrides for that command only. |

Example:

```json
[
  {
    "name": "power",
    "format": "pronto",
    "data": "0000 006D 0000 0022 00AC 00AC 0015 0040"
  }
]
```

### More than on/off on the dashboard

Each configured remote gets its own **device** under the gateway. Every JSON command becomes a **button** on that device (only what you configure—no generic on/off remote card). Automations: **`button.press`** on the command entity, or **`globalcache_itach.sendir`** / **`send_command`** services with the gateway device.

## API mapping (iTach TCP ↔ Home Assistant)

| iTach / unified TCP | Home Assistant |
|---------------------|----------------|
| `sendir` (Pronto → GC conversion) | Per-command **button** entities, `globalcache_itach.sendir` / `send_command` services |
| `completeir` / `busyIR` | Handled internally in the TCP client |
| `stopir` | `globalcache_itach.stop_ir` service and **`remote` stop** (`async_stop` / STOP) when the HA version exposes it |
| `set_LED_LIGHTING` / `get_LED_LIGHTING` | `globalcache_itach.set_led_lighting` / `get_led_lighting` |
| `get_IR` / `set_IR` | `globalcache_itach.get_ir` / `globalcache_itach.set_ir` |
| `get_IRL` / `stop_IRL` | `globalcache_itach.ir_learner_start` / `ir_learner_stop` (+ bus event `globalcache_itach_ir_learned`) |
| `receiveIR` | `globalcache_itach.receive_ir` (+ bus event `globalcache_itach_ir_received` when unsolicited `sendir`/`IR` lines arrive) |
| `getdevices`, `getversion`, `get_NET` | Coordinator refresh, **Gateway diagnostics** sensor (off by default), diagnostics download, and `get_devices` / `get_version` / `get_net` services |
| Arbitrary ASCII line | `globalcache_itach.send_raw` or **`send_command`** (same behaviour; collects lines for `collect_seconds`) |
| `setstate` / `getstate` | **Configure → Add relay** → `switch` entities; services `set_relay`, `get_relay`, `pulse_relay` |
| `get_SERIAL` / `set_SERIAL` + serial data port | **Configure → Add serial port** → `text` (+ optional **button** presets), **Last received** sensor, bus event `globalcache_itach_serial_received` when **Monitor incoming data** is enabled; services `send_serial`, `get_serial`, `set_serial` |

Protocol reference: [iTach API (PDF)](https://www.globalcache.com/files/docs/API-iTach.pdf), [Unified TCP API (PDF)](https://globalcache.com/files/docs/API-GC-UnifiedTCPv1.1.pdf).

## Limitations

- One **serialized** TCP client per config entry with **connect retries** and **EOF recovery** so the next command opens a new session. Multiple Home Assistant instances or other controllers talking to the same iTach can still contend on port **4998**.
- **Relay** and **serial** connectors are configured in **integration options** (like remotes). Serial payloads use the Unified TCP data socket (**control port + module**, e.g. 4999 for module 1 when control is 4998). Confirm module/port wiring on your SKU (IP2CC relays are often module **3**; **GC-100-12** relays are module **3**, IR emitters modules **4** and **5** — run `get_devices` or check diagnostics).
- **GC-100** allows only **one** TCP client on port **4998** at a time; avoid iHelp/other tools holding that port while Home Assistant is connected.
- **IR learner** output is exposed via events and logs; it does not replace Global Caché’s **iLearn** utility for every workflow.

## Development

```bash
pip install pytest pytest-asyncio voluptuous
pytest tests/
```

The test suite exercises **Pronto parsing** and the **async TCP client** against a fake iTach server (no Home Assistant install required for those tests).

## Legal

“Global Caché” and “iTach” are trademarks of their respective owners. This project is an independent open-source integration and is not affiliated with Global Caché.
