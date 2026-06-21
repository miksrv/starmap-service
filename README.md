# starmap-service

A long-running Python service that generates **star charts** (maps of the night sky) on demand
and serves them to a Telegram bot over **MQTT**.

It is built to run on Linux — primarily a **Raspberry Pi** — as a standalone, always-on process.
Heavy astronomical catalogs are loaded once at startup and kept available; each incoming request
is rendered into a PNG and sent back to the bot.

Rendering is powered by [**starplot**](https://github.com/steveberardi/starplot).

---

## Why this exists

Generating a star chart is heavy: it needs large catalogs (a ~53 MB star catalog, deep-sky
objects, constellation geometry, a JPL ephemeris) and a non-trivial matplotlib render. Doing that
inside a Telegram bot would make the bot slow, memory-hungry, and fragile.

So the rendering is split out into its own service:

- the **bot** stays lightweight and only talks to users;
- the **service** owns the catalogs and the rendering;
- the two communicate over an MQTT broker that already runs on the Pi.

A crash in the renderer never takes down the bot, and the service publishes its own availability
so the bot always knows whether charts can be generated right now.

---

## How it works with the Telegram bot

The companion bot lives in a separate repository:
**[miksrv/telegram-ai-bot](https://github.com/miksrv/telegram-ai-bot)**.

```
   Telegram user
        │  /command
        ▼
┌──────────────────┐   starmap/command    ┌──────────────────────┐
│  telegram-ai-bot │ ───────────────────▶ │   starmap-service    │
│                  │                      │  (this repository)   │
│  (lightweight)   │ ◀─────────────────── │   starplot + data    │
└──────────────────┘   starmap/result     └──────────────────────┘
        ▲    ▲         starmap/status (online/offline, retained + LWT)
        │    └───────────────────────────────────┘
   PNG chart
```

1. A user triggers a command in the Telegram bot.
2. The bot publishes a JSON request to `starmap/command` with a unique `request_id` and the
   observer's location/time/chart type. (The bot already implements this request/response pattern
   with `register_request`, reused from its `/photo` flow.)
3. The service acknowledges the request with a `queued` reply, then renders the chart (one at a
   time, in order) and publishes the result to `starmap/result` — both with the **same**
   `request_id`, so the bot can match the replies to the original request.
4. The service publishes its status (`online`/`offline`) to `starmap/status` so the bot can tell
   the user when the service is down instead of timing out.

Both processes typically run on the same Raspberry Pi alongside the MQTT broker (Mosquitto).

---

## MQTT API (contract with the bot)

This is a contract shared between the two repositories. **Do not change it unilaterally.**

📄 **The full contract — request schema, all fields, the per-`map_type` requirements, the response
shapes, the error catalog, and the queue behavior — lives in [`API.md`](API.md).** It is the single
source of truth; the summary below is just an at-a-glance overview.

### Topics

| Topic             | Direction      | Purpose                                    |
|-------------------|----------------|--------------------------------------------|
| `starmap/command` | bot → service  | request to render a chart                  |
| `starmap/result`  | service → bot  | acknowledgement, finished chart, or error  |
| `starmap/status`  | service → bot  | `online` / `offline` (**retained + LWT**)  |

### At a glance

- **Request** (`starmap/command`): a JSON object with a required `request_id`, a `map_type`, and
  optional `observer` / `target` / `optic` / `options` blocks. The service validates it and turns
  any problem into a structured `error` reply — never a traceback.
- **Queue**: a single worker renders one request at a time. Each accepted request is immediately
  acknowledged with a `queued` reply (so the bot can show "accepted, please wait"), then answered
  with a final `ok` / `error`. The normal flow is **two** replies per `request_id`. If the queue is
  full the request is rejected with `error: "queue full, try again later"`.
- **Response** (`starmap/result`): `status` is `queued`, `ok` (with `image_path` in the default
  `file` mode, or `image_base64`), or `error` (with a human-readable message).
- **Status** (`starmap/status`): retained `online` on startup, `offline` via Last Will if the
  service dies, and `offline` on clean shutdown.

See [`API.md`](API.md) for the complete field tables, the optic definitions, and the error catalog.

---

## Chart types (`map_type`)

| Type       | Status         | Description                                                   |
|------------|----------------|---------------------------------------------------------------|
| `full`     | ✅ implemented | all-sky RA/DEC map (stars, constellations, DSOs, Milky Way…)  |
| `galactic` | ✅ implemented | all-sky map in galactic coordinates (Mollweide)               |
| `zenith`   | ✅ implemented | the dome of sky overhead from the observer (with horizon circle) |
| `horizon`  | ✅ implemented | sky above the horizon, centered on a compass direction        |
| `optic`    | ✅ implemented | how a target looks through a given optic (telescope/binoculars/camera) |

Notes:
- `horizon` accepts `options.direction` (one of `N, NE, E, SE, S, SW, W, NW`; default `S`) to
  choose which 180°-wide swath of the horizon to show.
- `optic` currently requires explicit `target.ra` / `target.dec` (degrees); resolving an object
  name like `M31` to coordinates is not wired up yet. If the target is below the horizon at the
  given time/place, or the field of view is too wide (> 20°), the service replies with an `error`.

---

## Project layout

```
main.py                  # entry point → starts the MQTT service
API.md                   # full MQTT contract with the bot (single source of truth)
src/
  config.py              # config.yaml + environment-variable overrides
  request.py             # parse_command(dict) -> RenderRequest; per-map_type validation
  errors.py              # ValidationError (bot-safe messages)
  renderer.py            # Renderer.render(RenderRequest) -> PNG bytes; style built once, reused
  storage.py             # file-mode output: <request_id>.png + retention pruning
  service.py             # MQTT loop: command → validate → enqueue → (worker) render → result; status + LWT
config/
  config.yaml            # runtime parameters (mounted separately in Docker)
  mosquitto.conf         # throwaway broker config for local testing
data/                    # starplot catalogs (downloaded; not committed) — see data/README.md
systemd/starmap.service  # unit template (filled in by install.sh)
scripts/                 # install / start / stop / restart / fetch-data
Dockerfile, docker-compose.yml
```

---

## Configuration

Defaults live in `config/config.yaml`. **Environment variables override the file**, which is handy
for Docker and systemd. In Docker the file is mounted separately so it can be edited without
rebuilding the image.

```yaml
mqtt:
  broker: localhost        # override with MQTT_BROKER
  port: 1883               # override with MQTT_PORT
  keepalive: 60

render:
  resolution: 2600         # output image width in px (lower = faster/lighter; Pi-friendly)
  default_map_type: full   # used when a request omits map_type
  style: BLUE_NIGHT        # starplot theme (e.g. BLUE_NIGHT, GRAYSCALE, ANTIQUE)
  star_magnitude_limit: 6  # plot stars brighter than this magnitude
  language:                # starplot UI language, e.g. "ru" (leave empty until verified)

queue:
  max_size: 10             # max render requests waiting in line (the one rendering is not counted; 0 = unbounded)

output:
  mode: file               # file (write to dir, return path — default) | base64 (embed PNG; small charts only)
  dir: output              # used when mode = file; relative to project root
  retention:               # file mode only: keep the output dir from growing without bound
    max_files: 50          # keep at most this many PNGs (0 = unlimited)
    max_age_hours: 24      # delete PNGs older than this (0 = no age limit)

logging:
  level: INFO
```

In `file` mode each chart is written as `<request_id>.png` and, after every write, the service
prunes the output directory: it deletes files beyond `max_files` (oldest first) and files older
than `max_age_hours`. Pruning never interrupts a request — failures are only logged.

### Environment variables

| Variable               | Overrides                | Notes                                            |
|------------------------|--------------------------|--------------------------------------------------|
| `MQTT_BROKER`          | `mqtt.broker`            | broker host                                      |
| `MQTT_PORT`            | `mqtt.port`              | broker port                                      |
| `STARMAP_RESOLUTION`   | `render.resolution`      | output width in px                               |
| `STARMAP_QUEUE_MAX_SIZE`| `queue.max_size`        | max requests waiting in line (0 = unbounded)     |
| `STARMAP_OUTPUT_MODE`  | `output.mode`            | `base64` or `file`                               |
| `LOG_LEVEL`            | `logging.level`          | `INFO`, `DEBUG`, …                               |
| `STARPLOT_DATA_PATH`   | data directory           | where starplot reads/writes catalogs (`data/`)   |
| `MPLBACKEND`           | matplotlib backend       | must be `Agg` (headless); set by Docker/systemd  |

Copy `.env.example` to `.env` for local overrides.

---

## Data catalogs

starplot's catalogs (star/DSO/constellation/Milky-Way parquet files, the `de421.bsp` ephemeris,
and the DuckDB spatial extension) live in **`data/`**. The large files are **not committed to git**.

Get them with:

```bash
bash scripts/fetch-data.sh        # wraps `starplot setup`, downloads into data/
```

starplot will also download any missing files automatically on the first render, and
`scripts/install.sh` fetches them on the Pi when they are missing. See `data/README.md` for details.

---

## Running

### Option A — Docker (local development / testing on macOS)

Docker is for local testing only. It brings up a throwaway Mosquitto broker plus the service, so
you can exercise the full request/response flow.

```bash
docker compose up --build
```

This starts:
- `starmap_mosquitto` — an MQTT broker on `localhost:1883`;
- `starmap_service` — the renderer, connected to that broker.

`config/config.yaml` and `data/` are mounted as volumes (so you can edit config and keep catalogs
without rebuilding the image).

Test it end-to-end from another terminal (requires `mosquitto-clients`):

```bash
# Watch results and status
mosquitto_sub -h localhost -t 'starmap/result' -t 'starmap/status' -v

# Send a render request
mosquitto_pub -h localhost -t starmap/command \
  -m '{"request_id":"1","lat":55.75,"lon":37.62,"map_type":"full"}'
```

> On an Intel Mac the Linux container needs the `linux_amd64` DuckDB extension; starplot downloads
> it into the mounted `data/` on first use. On Apple Silicon the bundled `linux_arm64` build is used.

### Option B — Raspberry Pi (production, no Docker)

Deployment on the Pi does **not** use Docker. The service runs under systemd.

```bash
git clone https://github.com/miksrv/starmap-service.git
cd starmap-service
bash scripts/install.sh
```

`install.sh` will:

1. create a virtual environment in `venv/`;
2. install dependencies from `requirements.txt`;
3. download starplot catalogs into `data/` if they are missing;
4. create the output directory;
5. install the systemd unit (`/etc/systemd/system/starmap.service`) with the current user and
   project path filled in;
6. enable and start the service.

Then manage it with:

```bash
bash scripts/start.sh      # start + enable autostart on boot
bash scripts/stop.sh       # stop + disable autostart
bash scripts/restart.sh    # restart (e.g. after a code update)

journalctl -u starmap.service -f   # follow logs
```

The systemd unit sets `Restart=always` and `MPLBACKEND=Agg`, so the service comes back after a
crash or reboot — and the MQTT status correctly reflects whether it is alive.

---

## Operational notes

- **One render at a time, with a queue.** matplotlib is not thread-safe and the Pi cannot handle
  parallel renders, so a single worker renders requests one at a time. Requests that arrive
  mid-render wait in a bounded FIFO queue (`queue.max_size`); each is acknowledged with a `queued`
  reply. Only when the queue is full is a request rejected (`error: "queue full, try again later"`).
- **Time budget.** The bot waits ~90–120 s for a reply, so keep `render.resolution` modest
  (2000–3000 px) on the Pi. Profile the first vs. repeat render on the actual hardware.
- **Memory.** All catalogs are loaded into memory for the service's lifetime; check RAM usage on
  the Pi after startup.

---

## Requirements

- Python 3.10–3.13
- An MQTT broker (Mosquitto) reachable by both the bot and the service
- See `requirements.txt` (starplot, matplotlib, numpy, astropy, pytz, paho-mqtt, PyYAML,
  python-dotenv)

---

## Related

- Telegram bot: **[miksrv/telegram-ai-bot](https://github.com/miksrv/telegram-ai-bot)**
- Rendering library: **[steveberardi/starplot](https://github.com/steveberardi/starplot)**
- MQTT contract with the bot: [`API.md`](API.md)
- Design notes and remaining work: `ROADMAP.md`
- Architecture/concept reference: `CLAUDE.md`
