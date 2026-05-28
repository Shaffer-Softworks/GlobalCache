# Saved context — Global Caché iTach (Home Assistant)

This file is a **handoff snapshot** for future chats, contributors, and debugging. Update it when behavior or architecture changes in meaningful ways.

## Purpose

Custom integration for **Global Caché iTach / GC-100** gateways over TCP (default port **4998**): IR remotes, relay switches, serial send/monitor, and low-level TCP services. No external PyPI library — async **`ItachClient`** in-repo.

- **Domain:** `globalcache_itach`
- **Minimum HA:** see [`hacs.json`](hacs.json) (currently 2024.1)
- **Manifest:** [`manifest.json`](custom_components/globalcache_itach/manifest.json) — `integration_type: hub`, version **1.0.0**
- **Repo layout:** [`custom_components/globalcache_itach/`](custom_components/globalcache_itach/), [`tests/`](tests/) (32 tests), [`docs/images/`](docs/images/) (README screenshots), [`docker-compose.yml`](docker-compose.yml), [`README.md`](README.md)

## Platforms

`binary_sensor` (TCP connected), `button` (IR command + serial preset), `sensor` (gateway diagnostics + serial **Last received**), `switch` (relay), `text` (serial send). No **`remote`** platform (avoids generic on/off UI; JSON commands are buttons only).

## Key modules

| Area | File(s) |
|------|---------|
| Entry setup, services | [`__init__.py`](custom_components/globalcache_itach/__init__.py) |
| Config + options + reconfigure | [`config_flow.py`](custom_components/globalcache_itach/config_flow.py) |
| TCP client, framing, IR/relay/serial | [`client.py`](custom_components/globalcache_itach/client.py) |
| Coordinator, serial session lifecycle | [`coordinator.py`](custom_components/globalcache_itach/coordinator.py) |
| Persistent serial data-port RX | [`serial_session.py`](custom_components/globalcache_itach/serial_session.py) |
| `getdevices` parsing, IR module checks | [`device_util.py`](custom_components/globalcache_itach/device_util.py) |
| Stale entity cleanup (options-driven) | [`entity_registry_util.py`](custom_components/globalcache_itach/entity_registry_util.py) |
| `remote` / `switch` / `text` / `button` / `sensor` / `binary_sensor` | respective `*.py` |
| Pronto / GC pair conversion | [`pronto.py`](custom_components/globalcache_itach/pronto.py) |
| Services schema | [`services.yaml`](custom_components/globalcache_itach/services.yaml) |
| UI strings | [`strings.json`](custom_components/globalcache_itach/strings.json), [`translations/en.json`](custom_components/globalcache_itach/translations/en.json) |

## Architecture notes

- **One serialized TCP client per config entry** on the control port (`client.py`). Serial **payload** traffic uses a separate socket per module: **control port + module** (e.g. 4999 for module 1 when control is 4998).
- **Each configured remote** is a **subdevice** under the gateway (`device_util.async_register_remote_devices`). JSON commands are **`button`** entities only (legacy **`remote.*`** entities removed on reload).
- **`device_modules`** from `getdevices` is stored on the config entry at setup; **`module_accepts_ir()`** blocks IR to non-IR connectors (avoids `unknowncommand` on GC-100 serial/relay modules). Hints point users to correct modules (GC-100-12: relays **3**, IR **4** and **5**).
- **GC-100 relay responses** use `state,...` not `setstate,...` — parsed in `client.py` (`RELAY_STATE_RE`).
- **GC-100** allows only **one** TCP client on **4998**; avoid iHelp/other tools holding that port while HA is connected.

## Serial monitoring (implemented)

Options → **Serial line settings** → **Monitor incoming data (persistent connection)** (`monitor_incoming`, default **on**). Legacy option key **`listen`** is still read via **`serial_listen_enabled()`** in [`const.py`](custom_components/globalcache_itach/const.py).

When enabled:

1. **`SerialPortSession`** keeps a persistent connection on the data port, reconnects on EOF, applies `set_SERIAL` settings on connect.
2. Bus event **`globalcache_itach_serial_received`** — `config_entry_id`, `serial_id`, `module`, `port`, `data`, `is_response`.
3. **`sensor`** per port: unique_id `{entry_id}_serial_{serial_id}_rx`, translation **Last received**.
4. **`text`** entity updates on unsolicited RX (send path still sets value from command response).
5. **`async_send_serial`** uses the session when monitoring is on; otherwise one-shot `send_serial_payload`.

Started after coordinator first refresh: **`async_start_serial_listeners()`** in `__init__.py`. Stopped in **`async_shutdown()`**.

## Entity registry cleanup

Removing remotes/relays/serial ports from options **deletes** stale registry entities on reload (not left greyed out). Cleanup runs at start of **`async_setup_entry`**, **`async_unload_entry`**, and options **`_async_update_listener`**.

Matching uses **unique_id** patterns (`{entry_id}_relay_*`, `{entry_id}_serial_*`, `{entry_id}_{remote_id}`), **not** HA’s `platform` field (integration registers as `globalcache_itach`).

## Decisions already made

1. **`integration_type`: `hub`** — gateway model; **`device`** caused **500** on unimplemented “Add device” flows.

2. **Options flow menus** — some steps use **`vol.In({...})`** instead of **`SelectSelector`** for broader HA compatibility.

3. **Translations** — `options.step.remote_commands` needs **`description`** + **`{hint}`** placeholder. **`options.step.init.data.next`** in `strings.json`. IR/serial command JSON uses **`TextSelector(multiline=True)`** (textarea), not a single-line field.

4. **Do not use `listen` as a config-flow field key** — Home Assistant does not apply `options.step.*.data.listen` labels; UI shows raw `listen`. Use **`monitor_incoming`** with label *Monitor incoming data (persistent connection)* (plain **`bool`**, same as `append_cr`).

5. **Docker** — Do **not** set **`init: true`** on the HA service (s6 must be PID 1).

6. **Options flow constructor** — **`OptionsFlow()`** takes no args; entry via **`async_get_entry(self.handler)`** (`_options_entry()`).

7. **`RemoteEntityFeature`** — only **`LEARN_COMMAND`**, **`DELETE_COMMAND`**, **`ACTIVITY`** (+ optional **`STOP`**). No **`TURN_ON`/`TURN_OFF`**.

8. **`services.yaml`** — quote YAML keys like **`"on":`** for relay services so `on` is not parsed as boolean.

9. **Reconfigure** — **⋮ → Reconfigure** updates host/port/name/timeouts, refreshes `device_modules`, reloads entry.

10. **README screenshots** — PNGs in [`docs/images/`](docs/images/), driven by [`docs/screenshot-manifest.yaml`](docs/screenshot-manifest.yaml). Regenerate with `HA_REFRESH_TOKEN=… python3 scripts/capture_screenshots.py` (delegates to personal skill **`ha-integration-screenshots`** at `~/.cursor/skills/`). Never commit tokens.

## Documentation

- **[`README.md`](README.md)** — user-facing install, configure, API mapping, screenshot gallery.
- **[`docs/images/`](docs/images/)** — committed UI screenshots (integrations, config flow, options, devices).
- **[`docs/screenshot-manifest.yaml`](docs/screenshot-manifest.yaml)** — Playwright capture plan (device selectors, paths, actions).
- **Cursor skill:** `~/.cursor/skills/ha-integration-screenshots/` — reusable across all HA custom integration repos.

## Dev environment

```bash
docker compose up -d    # http://localhost:8123
# Integration mounted: ./custom_components/globalcache_itach → /config/custom_components/globalcache_itach
python3 -m pytest tests/ -q   # 32 tests, no full HA required
```

### Refresh README screenshots

```bash
export HA_REFRESH_TOKEN="<dev instance token>"
pip install playwright requests pyyaml && python3 -m playwright install chromium
python3 scripts/capture_screenshots.py
# or: python3 ~/.cursor/skills/ha-integration-screenshots/scripts/capture_ha_screenshots.py --repo-root .
python3 ~/.cursor/skills/ha-integration-screenshots/scripts/capture_ha_screenshots.py --repo-root . --discover-devices
```

## If UI still returns 500

Check **Settings → System → Logs**. Typical causes: missing translation key, selector schema mismatch, exception in `config_flow.py` / `async_setup_entry`.

Restart HA after changing `strings.json` / integration code if labels or behavior do not update in the UI.

## Optional follow-ups (not implemented)

- UDP discovery (e.g. 239.255.250.250:9131)
- YAML import from core `itach` integration if applicable

---

*Last updated: README + docs/images screenshots, docs/screenshot-manifest.yaml, ha-integration-screenshots Cursor skill, button-only IR (no `remote` platform) — manifest 1.0.0.*
