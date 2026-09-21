# Saved context — Global Caché iTach (Home Assistant)

This file is a **handoff snapshot** for future chats, contributors, and debugging. Update it when behavior or architecture changes in meaningful ways.

## Purpose

Custom integration for **Global Caché iTach / GC-100** gateways over TCP (default port **4998**): IR remotes, relay switches, serial send/monitor, and low-level TCP services. No external PyPI library — async **`ItachClient`** in-repo.

- **Domain:** `globalcache_itach`
- **Minimum HA:** see [`hacs.json`](hacs.json) (currently **2026.6** — requires core `infrared` platform)
- **HACS:** listed in [hacs/default](https://github.com/hacs/default) (`Shaffer-Softworks/GlobalCache`) — [PR #8063](https://github.com/hacs/default/pull/8063) merged 2026-07-31
- **Manifest:** [`manifest.json`](custom_components/globalcache_itach/manifest.json) — `integration_type: hub`, `dependencies: ["infrared"]`, version **1.1.0**
- **Repo layout:** [`custom_components/globalcache_itach/`](custom_components/globalcache_itach/), [`tests/`](tests/) , [`docs/images/`](docs/images/) (README screenshots), [`docker-compose.yml`](docker-compose.yml), [`README.md`](README.md)

## Platforms

`binary_sensor` (TCP connected), `button` (IR command + serial preset), **`infrared`** (emitter + optional receiver per IR connector), `sensor` (gateway diagnostics + serial **Last received**), `switch` (relay), `text` (serial send). No **`remote`** platform (avoids generic on/off UI; JSON commands are buttons only).

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
| UDP multicast + DHCP discovery | [`discovery.py`](custom_components/globalcache_itach/discovery.py) |
| HA `infrared` emitters/receivers | [`infrared.py`](custom_components/globalcache_itach/infrared.py), [`infrared_util.py`](custom_components/globalcache_itach/infrared_util.py) |
| `switch` / `text` / `button` / `sensor` / `binary_sensor` | respective `*.py` |
| Pronto / GC pair conversion | [`pronto.py`](custom_components/globalcache_itach/pronto.py) |
| Services schema | [`services.yaml`](custom_components/globalcache_itach/services.yaml) |
| UI strings | [`strings.json`](custom_components/globalcache_itach/strings.json), [`translations/en.json`](custom_components/globalcache_itach/translations/en.json) |

## Architecture notes

- **One serialized TCP client per config entry** on the control port (`client.py`). Serial **payload** traffic uses a separate socket per module: **control port + module** (e.g. 4999 for module 1 when control is 4998).
- **Each configured remote** is a **subdevice** under the gateway (`device_util.async_register_remote_devices`). JSON commands are **`button`** entities only (legacy **`remote.*`** entities removed on reload).
- **`infrared` platform** — one emitter per IR connector from `device_modules` / remotes. On **Global Connect** only, a disabled-by-default receiver is added when `get_IR` reports `RECEIVER` (room receive / `receiveIR`). iTach/GC-100/Flex skip that probe. Emitters convert `command.get_raw_timings()` (signed µs) → GC pulse pairs → `sendir`. Receivers parse inbound `sendir` lines and call `_handle_received_signal`.
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

3. **Translations** — `options.step.remote_commands` needs **`description`** + **`{hint}`** placeholder. **`options.step.init.data.next`** in `strings.json`. IR/serial command JSON uses **`TextSelector(multiline=True)`** (textarea), not a single-line field. Learn IR uses **`learn_ir`** / **`learn_ir_capture`** with placeholders `{timeout}`, `{command}`, `{detail}`.

4. **Do not use `listen` as a config-flow field key** — Home Assistant does not apply `options.step.*.data.listen` labels; UI shows raw `listen`. Use **`monitor_incoming`** with label *Monitor incoming data (persistent connection)* (plain **`bool`**, same as `append_cr`).

5. **Docker** — Do **not** set **`init: true`** on the HA service (s6 must be PID 1).

6. **Options flow constructor** — **`OptionsFlow()`** takes no args; entry via **`async_get_entry(self.handler)`** (`_options_entry()`).

7. **`RemoteEntityFeature`** — only **`LEARN_COMMAND`**, **`DELETE_COMMAND`**, **`ACTIVITY`** (+ optional **`STOP`**). No **`TURN_ON`/`TURN_OFF`**.

8. **`services.yaml`** — quote YAML keys like **`"on":`** for relay services so `on` is not parsed as boolean.

9. **Reconfigure** — **⋮ → Reconfigure** updates host/port/name/timeouts, refreshes `device_modules`, reloads entry.

10. **README screenshots** — PNGs in [`docs/images/`](docs/images/), driven by [`docs/screenshot-manifest.yaml`](docs/screenshot-manifest.yaml). Includes gateway IR emitters, options (Learn IR), and optional **LG Infrared** consumer device. Regenerate with `HA_REFRESH_TOKEN=… python3 scripts/capture_screenshots.py` (skill **`ha-integration-screenshots`**). `ha_url` must match the token `client_id` (use `http://127.0.0.1:8124` for this Docker stack). Never commit tokens (`.ha_refresh_token` is gitignored).

11. **HA 2026.2+ `DhcpServiceInfo`** — import from `homeassistant.helpers.service_info.dhcp`, **not** `homeassistant.components.dhcp` (removed in HA Core 2026.2). Fixed on `main` in PR #6 (`fix/dhcp-service-info-import`). **v1.0.1 does not include this fix** — cut **v1.0.2** for production/HACS.

12. **UDP discovery** — implemented in [`discovery.py`](custom_components/globalcache_itach/discovery.py): multicast `239.255.250.250:9131` + DHCP hostname `globalcache_*` fallback. Discovery listener runs at integration setup; bootstrap may log a timeout waiting on `globalcache_itach_discovery` in Docker bridge mode (HA continues anyway).

13. **Git / releases** — semver bumps via [`.github/workflows/release.yml`](.github/workflows/release.yml) (workflow_dispatch). Each release attaches **`globalcache_itach.zip`** (integration files at zip root) for HACS `zip_release` download counting; [`hacs.json`](hacs.json) sets `zip_release` + `filename`. `WORKFLOW_TRIGGER_TOKEN` enables automated manifest-bump PRs; without it, open the compare URL from the workflow summary. **Do not** add `Co-authored-by: Cursor` to commits; history was rewritten (2026-06-05) to remove it from `main` and retag `v1.0.0`.

14. **Pinhole Learn IR** — Options **Learn IR command (pinhole)** calls `get_IRL`, waits for one `sendir` line, `stop_IRL`, then stores `full_sendir` on the chosen remote via `rewrite_sendir_connector` (learner always reports `1:1`).

15. **Infrared timing padding** — `infrared-protocols` NEC (and similar) frames end on a mark with no trailing space. `us_timings_to_gc_pairs` pads with a **~40 ms** off pulse (not the ~80 µs GC minimum), otherwise LG Infrared / other consumers fail to decode on the TV.
## Documentation

- **[`README.md`](README.md)** — user-facing install, configure, API mapping, screenshot gallery.
- **[`docs/images/`](docs/images/)** — committed UI screenshots (integrations, config flow, options with Learn IR, gateway emitters, remotes, LG Infrared example, GC-100).
- **[`docs/screenshot-manifest.yaml`](docs/screenshot-manifest.yaml)** — Playwright capture plan (device selectors, paths, actions). Docker HA is on host port **8124**.
- **Cursor skill:** `~/.cursor/skills/ha-integration-screenshots/` — reusable across all HA custom integration repos.

## Dev environment

```bash
docker compose up -d    # http://localhost:8124 (maps to container 8123)
# Integration mounted: ./custom_components/globalcache_itach → /config/custom_components/globalcache_itach
python3 -m pytest tests/ -q   # unit tests, no full HA required
```

### Refresh README screenshots

```bash
export HA_REFRESH_TOKEN="<dev instance token>"   # client_id must match ha_url in manifest
pip install playwright requests pyyaml && python3 -m playwright install chromium
python3 scripts/capture_screenshots.py
# or: python3 ~/.cursor/skills/ha-integration-screenshots/scripts/capture_ha_screenshots.py --repo-root .
python3 ~/.cursor/skills/ha-integration-screenshots/scripts/capture_ha_screenshots.py --repo-root . --discover-devices
```

## If UI still returns 500

Check **Settings → System → Logs**. Typical causes: missing translation key, selector schema mismatch, exception in `config_flow.py` / `async_setup_entry`.

Restart HA after changing `strings.json` / integration code if labels or behavior do not update in the UI.

### HA 2026.2+ import error (known fixed on `main`)

```
cannot import name 'DhcpServiceInfo' from 'homeassistant.components.dhcp'
```

Config entry shows **`setup_error` / Import error**. Deploy manifest **1.0.2+** (or cherry-pick the one-line import change in `config_flow.py`), then restart HA or reload the integration.

## Production / deployment snapshot (2026-07-31)

| Environment | Integration version | Status |
|-------------|---------------------|--------|
| **HACS default** | Search **Global Caché iTach** / **GlobalCache** | Added via [hacs/default#8063](https://github.com/hacs/default/pull/8063) |
| **Latest release** | **v1.0.3** | https://github.com/Shaffer-Softworks/GlobalCache/releases |

Install via HACS (default feed) preferred; custom-repository install is no longer needed.

## Optional follow-ups (not implemented)

- YAML import from core `itach` integration if applicable

---

*Last updated: 2026-09-09 — HA `infrared` emitter/receiver platform (v1.1.0); min HA 2026.6.*
