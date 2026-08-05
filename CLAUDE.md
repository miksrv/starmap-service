# CLAUDE.md

Guidance for Claude Code (and contributors) working in this repository.

## Conventions

- **Project documentation is written in English** (this file, `README.md`, `ROADMAP.md`, `API.md`,
  code comments, commit messages). Keep all in-repo docs in English.
- **Conversation with the maintainer happens in Russian.** Replies in chat are in Russian,
  but anything written into the repository stays in English.

## What this project is

**starmap-service** is a long-running Python service that generates star charts (maps of the
night sky) on demand and serves them to a Telegram bot.

It runs on Linux systems — primarily a **Raspberry Pi** — as a standalone, always-on process.
The service does **not** talk to Telegram directly. Instead it communicates with the bot over
**MQTT** (an MQTT broker already runs on the Pi; the bot is already connected to it):

- It loads all heavy astronomical data **once** at startup and keeps it in memory.
- It subscribes to a command topic and waits for render requests from the bot.
- For each request it generates a chart for the given location / time / chart type.
- It publishes the resulting image back to the bot.
- It publishes its own availability status so the bot knows whether the service is up.

The bot stays lightweight; the heavy lifting (large catalogs, matplotlib rendering) lives here.
A crash in the renderer does not take down the bot, and the MQTT Last Will & Testament (LWT)
makes the published status reflect whether the service is actually alive.

### Layout

```
main.py                  # entry point → starts the MQTT service
API.md                   # full MQTT contract with the bot (single source of truth)
src/
  config.py              # config.yaml + environment-variable overrides
  request.py             # parse_command(dict) -> RenderRequest; per-map_type validation
  errors.py              # ValidationError (bot-safe messages)
  renderer.py            # Renderer.render(RenderRequest) -> PNG bytes; style built once, reused
  storage.py             # file-mode output: <request_id>.png + retention pruning
  service.py             # MQTT loop: command → validate → enqueue → (worker) render → result; status (online/offline) + LWT
tests/                   # pytest suite covering config/request/errors/renderer/storage/service
config/
  config.yaml            # runtime parameters (resolution, style, output mode…); mounted separately in Docker
  mosquitto.conf         # throwaway broker config for local testing
data/                    # starplot catalogs (parquet + de421.bsp + duckdb-extensions); data_path target
systemd/starmap.service  # unit template (__USER__ / __WORKDIR__ filled in by install.sh)
scripts/                 # install.sh, start.sh, stop.sh, restart.sh, fetch-data.sh, send_request.py
Dockerfile               # local-dev image (not used on the Pi)
docker-compose.yml       # local-dev stack: throwaway mosquitto broker + the service
```

### Current state

The MQTT service architecture is in place, and all five chart types (`full`, `galactic`, `zenith`,
`horizon`, `optic`) are implemented — see [Chart types](#chart-types-map_type--all-implemented)
below. The old one-shot `map_big.png` script behavior now lives inside `Renderer._render_full`.
Config, the queue/worker, LWT/status, retention pruning, and a `tests/` suite are all in place too.
See [Running](#running) below for what's actually still open.

Deployment target is the Raspberry Pi **without Docker** (Docker/`docker-compose.yml` exist only
for local development). On the Pi the service runs under **systemd** with `Restart=always` and
`MPLBACKEND=Agg` (installed via `scripts/install.sh`).

## MQTT API (contract with the bot)

This is a contract shared between two repositories (this service and the bot). It must not be
changed unilaterally.

**The full contract is in [`API.md`](API.md) — the single source of truth.** It documents the
topics, the request schema and every field, the per-`map_type` requirements, the optic definitions,
all response shapes (`queued` / `ok` / `error`), the error catalog, and the queue behavior. Keep
`API.md` in sync with the code; this file only summarizes the implementation side.

Topics: `starmap/command` (bot → service), `starmap/result` (service → bot), `starmap/status`
(service → bot, retained + LWT).

### Implementation notes

- **Parsing/validation:** `src/request.py` (`parse_command` → `RenderRequest`) raises
  `ValidationError` (`src/errors.py`) with bot-safe messages. The service turns every failure into a
  structured `error` reply — no Python traceback ever reaches the bot.
- **Queue (`src/service.py`):** a **single worker thread** drains a bounded FIFO `queue.Queue`
  (`config.QUEUE_MAX_SIZE`, env `STARMAP_QUEUE_MAX_SIZE`; 0 = unbounded). Intake (parse/validate)
  runs on a short-lived thread off the network loop, then enqueues and immediately acknowledges with
  a `queued` reply (`position` counts requests ahead plus the in-flight render). The worker renders
  one at a time in order. A request that arrives when the queue is full is rejected with `queue
  full, try again later` (this replaced the old `busy` single-flight reply); unexpected render
  failures reply `internal render error`.
- **Output:** `output.mode = file` is the **default** (chosen after live testing: a full-sky PNG as
  base64 exceeds the MQTT broker packet limit). In file mode the PNG is written as `<request_id>.png`
  (sanitized) under `OUTPUT_DIR` and the reply carries `image_path` (the bot reads the file from the
  shared dir). After each write the output dir is pruned by count/age (`output.retention`; logic in
  `src/storage.py`). `base64` mode (`image_base64`) is only safe for small charts.
- **Status:** retained `{"status": "online"}` on startup; `{"status": "offline"}` registered as the
  **LWT** (retained) so the broker broadcasts it if the service dies; `offline` published before a
  clean shutdown.

### Chart types (`map_type`) — all implemented

Each maps to a starplot plot class in `Renderer` (starplot 0.20.x):

- `full` — `MapPlot` (Miller, all-sky RA/DEC). `_render_full`.
- `galactic` — `GalaxyPlot` (Mollweide, galactic coords; no observer). `_render_galactic`.
- `zenith` — `ZenithPlot(observer=...)` + `.horizon()`. `_render_zenith`. (`.info()` is deliberately
  **not** called — it's broken in starplot 0.20.4, references a missing `self.dt`.)
- `horizon` — `HorizonPlot(altitude, azimuth, observer=...)`; `options.direction` picks the azimuth. `_render_horizon`.
- `optic` — `OpticPlot(ra, dec, optic, observer=...)`. `_render_optic` + `_build_optic`/`_target_radec`.

starplot API notes: `Observer(dt=<tz-aware>, lat, lon)` — the kwarg is `dt` and must be
timezone-aware (parse_command guarantees this). Optic classes (`Binoculars`, `Scope`, `Refractor`,
`Reflector`, `Camera`) come from `starplot`. `OpticPlot` raises `ValueError` if the target is below
the horizon or the FOV > 20°; the renderer converts these to `ValidationError` so the bot gets a
clean message. `optic` currently needs explicit `target.ra`/`target.dec` (object-name lookup TODO).

## Rendering engine: starplot

The service renders charts with **starplot** (https://github.com/steveberardi/starplot), a Python
library for creating star charts and maps built on top of matplotlib, with a data backend powered
by **DuckDB + Ibis** for fast object lookup over large catalogs.

### Plot types starplot provides

- **Map** — star maps with 10+ customizable projections (used for the `full` all-sky chart).
- **Zenith** — the entire sky as seen from a specific time and place (used for `zenith`).
- **Horizon** — the horizon view from a specific time and place (used for `horizon`).
- **Optic** — simulates what an object looks like through a given optic (telescope, binoculars,
  camera) at a specific time and place.
- **Galactic** — Mollweide projection in galactic coordinates.

### Objects starplot can render

- Stars and constellations (lines, labels, boundaries).
- Planets and the Moon.
- Deep Sky Objects (DSOs) — with support for plotting their true apparent extent.
- Comets and satellites, including trajectory plotting.
- The Milky Way, ecliptic, celestial equator, gridlines.

### Other starplot features

- Export to PNG, SVG, JPEG.
- 8+ built-in style themes plus full styling customization.
- Label collision avoidance for readable charts.
- Localization for several languages (verify Russian support before relying on it — it may not be
  included; a custom label dictionary might be needed).

### Data files (consumed by starplot)

All starplot catalogs live under `data/` (NOT the repo root). starplot finds them via
`settings.data_path`, which the renderer sets to `config.DATA_DIR` (`data/`); override with the
`STARPLOT_DATA_PATH` env var. See `data/README.md`.

- `data/stars.bigksy.0.1.3.mag11.parquet` (~53 MB) — star catalog.
- `data/de421.bsp` (~16 MB) — JPL ephemeris for planet/Moon positions.
- `data/ongc.0.1.2.parquet` (~15 MB) — deep sky object catalog.
- `data/constellations.0.3.3.parquet`, `data/constellations-borders-0.3.1.parquet` — constellation lines/borders.
- `data/milky_way-0.1.0.parquet` — Milky Way outline.
- `data/duckdb-extensions/` — DuckDB spatial extension (linux_arm64 build committed for the Pi).

The large catalogs are git-ignored. Get them with `bash scripts/fetch-data.sh` (wraps
`starplot setup`), or let starplot download them automatically on the first render; `install.sh`
fetches them on the Pi when missing. In Docker, `data/` is mounted as a volume (not baked into the
image). starplot loads catalogs lazily via its DuckDB backend and keeps them available for the
lifetime of the service.

## Important constraints

- **matplotlib is not thread-safe** and the Pi cannot handle parallel renders — rendering is
  serialized through a single worker thread draining a bounded FIFO queue (see `src/service.py`).
- The bot waits ~90–120s for a response, so each render must fit within that time budget. Keep the
  Telegram resolution low (2000–3000px, not 6000) and profile first vs. repeat renders on the Pi.
- Always render headless: set `MPLBACKEND=Agg`.

## Running

- **Local development (macOS):** `docker compose up --build` — starts a throwaway mosquitto broker
  plus the service. Test the full flow against `localhost:1883`:
  - subscribe: `mosquitto_sub -t 'starmap/result' -t 'starmap/status'`
  - request: `mosquitto_pub -t starmap/command -m '{"request_id":"1","lat":55.75,"lon":37.62,"map_type":"full"}'`
- **Raspberry Pi (no Docker):** `bash scripts/install.sh` (creates venv, installs deps, registers
  and starts the systemd unit). Manage with `scripts/{start,stop,restart}.sh`; logs via
  `journalctl -u starmap.service -f`.
- **Configuration:** edit `config/config.yaml` (e.g. `render.resolution`); environment variables
  override it (`MQTT_BROKER`, `MQTT_PORT`, `STARMAP_RESOLUTION`, `STARMAP_QUEUE_MAX_SIZE`,
  `STARMAP_OUTPUT_MODE`, `STARPLOT_DATA_PATH`, `LOG_LEVEL`; full list with descriptions in
  `README.md`). In Docker the config file is mounted separately so it can be changed without
  rebuilding.

`ROADMAP.md` is the original pre-implementation plan; most of its checklist is now done even where
the checkboxes weren't updated (config, the MQTT service, deployment, tests, docs), so treat unchecked
items there as needing re-verification rather than as an accurate to-do list. The concrete work still
open is: object-name lookup for `optic` targets, and Russian label localization.
