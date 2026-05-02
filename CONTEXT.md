# Saved context — Global Caché iTach (Home Assistant)

This file is a **handoff snapshot** for future chats, contributors, and debugging. Update it when behavior or architecture changes in meaningful ways.

## Purpose

Custom integration for **Global Caché iTach** TCP/IP → IR (e.g. IP2IR): config/options UI, **`remote`** (Pronto / GC / full `sendir`), services for the TCP API, tests, Docker-based dev run.

- **Domain:** `globalcache_itach`
- **Minimum HA:** see [`hacs.json`](hacs.json) (currently 2024.1)
- **Repo layout:** [`custom_components/globalcache_itach/`](custom_components/globalcache_itach/), [`tests/`](tests/), [`docker-compose.yml`](docker-compose.yml), [`README.md`](README.md)

## Key modules

| Area | File(s) |
|------|---------|
| Entry setup, services | [`__init__.py`](custom_components/globalcache_itach/__init__.py) |
| Config + options flow | [`config_flow.py`](custom_components/globalcache_itach/config_flow.py) |
| TCP client, framing, `sendir` / `completeir` / backoff | [`client.py`](custom_components/globalcache_itach/client.py) |
| Poll `getdevices` / `getversion` | [`coordinator.py`](custom_components/globalcache_itach/coordinator.py) |
| `remote` platform | [`remote.py`](custom_components/globalcache_itach/remote.py) |
| Pronto / GC helpers | [`pronto.py`](custom_components/globalcache_itach/pronto.py) |
| Services schema | [`services.yaml`](custom_components/globalcache_itach/services.yaml) |
| Strings / EN UI | [`strings.json`](custom_components/globalcache_itach/strings.json), [`translations/en.json`](custom_components/globalcache_itach/translations/en.json) |

## Decisions already made

1. **`integration_type`: `hub`** in [`manifest.json`](custom_components/globalcache_itach/manifest.json) — the iTach is a **gateway**, not a per-device subentry integration. Using **`device`** led Home Assistant to expose **“Add device”** / device-style flows that were not implemented → **500** when opening those paths.

2. **Options flow menus** — some steps use **`vol.In({...})`** instead of **`SelectSelector`** with `{value,label}` lists for broader HA compatibility.

3. **Translations** — `options.step.remote_commands` includes a **`description`** with **`{hint}`** so `description_placeholders={"hint": ...}` does not break rendering. **`options.step.init.data.next`** exists in `strings.json` where needed.

4. **Docker** — Do **not** set **`init: true`** on the Home Assistant service in Compose: the official image expects **s6 as PID 1**; an init wrapper caused **`s6-overlay-suexec: fatal: can only run as pid 1`**.

5. **Options flow constructor** — Core **`OptionsFlow`** no longer accepts **`super().__init__(config_entry)`** (it ends at **`object.__init__`** → **`TypeError`** when opening **Configure**). Use **`GlobalCacheItachOptionsFlow()`** with no args and resolve the entry via **`async_get_entry(self.handler)`** (see **`_options_entry()`** in `config_flow.py`).

6. **`RemoteEntityFeature`** — Core only defines **`LEARN_COMMAND`**, **`DELETE_COMMAND`**, **`ACTIVITY`** (and optionally future flags). **`TURN_ON` / `TURN_OFF` are not valid** on `RemoteEntityFeature`; using them prevents the **`remote`** platform from loading. Use **`RemoteEntityFeature(0)`** plus optional **`STOP`** if present (`remote.py`).

## Tests

```bash
python3 -m pytest tests/ -q
```

Client/pronto tests use fakes; they do not require a full HA install.

## If UI still returns 500

Check **Settings → System → Logs** for the Python traceback. Typical causes: missing translation key, selector schema mismatch, or an unhandled exception in `config_flow.py` / `async_setup_entry`.

## Optional follow-ups (not required for core use)

- UDP discovery (e.g. 239.255.250.250:9131)
- `binary_sensor` for receive IR / learner state
- YAML import from core `itach` if applicable
- **`async_step_reconfigure`** for host/port edits without removing the entry

---

*Last aligned with repo state: integration `hub`, manifest version 1.0.0.*
