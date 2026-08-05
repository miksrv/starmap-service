# MQTT API — contract with the bot

This document is the **single source of truth** for the MQTT contract between **starmap-service**
(this repository) and the **Telegram bot** ([miksrv/telegram-ai-bot](https://github.com/miksrv/telegram-ai-bot)).

> **This is a shared contract across two repositories. Do not change it unilaterally.**
> Any change here must be coordinated with the bot and reflected in both codebases.

The service never talks to Telegram directly: the bot owns the user interaction, the service owns
the rendering, and the two communicate only through the MQTT topics below.

---

## Topics

| Topic             | Direction      | QoS | Retained | Purpose                                          |
|-------------------|----------------|-----|----------|--------------------------------------------------|
| `starmap/command` | bot → service  | 1   | no       | request to render a chart                        |
| `starmap/result`  | service → bot  | 1   | no       | acknowledgement, finished chart, or error        |
| `starmap/status`  | service → bot  | 1   | **yes**  | `online` / `offline` (retained + Last Will)      |

All payloads are UTF-8 encoded JSON objects.

---

## Request — `starmap/command`

The bot publishes a JSON object. Common fields are top-level; type-specific data goes in optional
nested blocks (`observer`, `target`, `optic`, `options`). The service requires only the blocks a
given `map_type` actually needs and ignores the rest, so the contract can grow without breaking
older callers.

```json
{
  "request_id": "1718635200",
  "map_type": "optic",

  "observer": {
    "lat": 55.75,
    "lon": 37.62,
    "datetime": "2026-06-17T22:00:00"
  },

  "target": { "object": "M 31" },

  "optic": { "type": "binoculars", "magnification": 10, "fov": 65 },

  "options": { "style": "BLUE_NIGHT", "resolution": 2600, "projection": "miller" }
}
```

`target` also accepts explicit coordinates instead of a name: `{ "ra": 10.68, "dec": 41.27 }`.

### Fields

| Field                   | Type   | Description                                                          |
|-------------------------|--------|---------------------------------------------------------------------|
| `request_id`            | string | **required always** — the bot matches every reply to the request    |
| `map_type`              | string | chart type (see below); defaults to `render.default_map_type`       |
| `observer.lat` / `.lon` | number | observer coordinates in degrees; required for `zenith`/`horizon`/`optic` |
| `observer.datetime`     | string | ISO 8601; **defaults to the service's current system time**         |
| `target.ra` / `.dec`    | number | target coordinates in degrees; **required for `optic`** unless `target.object` is given |
| `target.object`         | string | object name — catalog number (`M31`, `NGC224`, `IC1396`, any spacing/case), Sun/Moon/planet name, star proper name (`Vega`), or DSO common name (`Andromeda Galaxy`); resolved server-side, see [Object name resolution](#object-name-resolution-targetobject) |
| `optic.type`            | string | `binoculars` / `telescope` / `refractor` / `reflector` / `camera`   |
| `options.*`             | object | optional per-request overrides of `config.yaml` (style, resolution…)|

> **Fallback form:** a flat top-level `lat` / `lon` / `datetime` (without the `observer` block) is
> also accepted as a convenience, but the nested `observer` form above is canonical.

If `observer.datetime` is omitted, the service uses its current system time. A naive datetime (no
timezone) is interpreted as **UTC**.

Parsing and validation live in `src/request.py` (`parse_command` → `RenderRequest`). Every failure
is turned into a structured `error` reply — a Python traceback never reaches the bot.

### Required blocks by `map_type`

| `map_type` | `observer` | `target` | `optic` | Notes                                       |
|------------|------------|----------|---------|---------------------------------------------|
| `full`     | —          | —        | —       | all-sky RA/DEC map; no observer needed      |
| `galactic` | —          | —        | —       | all-sky map in galactic coordinates         |
| `zenith`   | **yes**    | —        | —       | sky overhead at the observer's time/place   |
| `horizon`  | **yes**    | —        | —       | horizon panorama; `options.direction` opt.  |
| `optic`    | **yes**    | **yes**  | **yes** | object as seen through the given optic      |

### Optic definitions (`optic`)

For `map_type: optic`, the `optic` block selects the instrument and its parameters:

| `optic.type`            | Required fields                                                       |
|-------------------------|----------------------------------------------------------------------|
| `binoculars`            | `magnification`, `fov` (apparent FOV in degrees; ~60 if unknown)      |
| `telescope` / `scope`   | `focal_length`, `eyepiece_focal_length`, `eyepiece_fov` (mm/mm/deg)  |
| `refractor`             | same as `telescope` (image inverted, assumes a star diagonal)        |
| `reflector`             | same as `telescope` (image rotated 180°)                             |
| `camera`                | `sensor_width`, `sensor_height`, `lens_focal_length` (mm); optional `rotation` (deg) |

### Object name resolution (`target.object`)

The bot should forward whatever the user typed, untouched — no parsing or normalization on the
bot's side. The service normalizes it (strips spaces/underscores/hyphens, case-insensitive) and
tries, in order:

1. **Catalog number** — `M31`, `M 31`, `M_31`, `m-31`, `NGC224`, `ngc 224`, `IC1396`, `ic_1396` all
   resolve to the same object.
2. **Sun** / **Moon** (case-insensitive).
3. **Planet** name (`Jupiter`, `Saturn`, …), case-insensitive.
4. **Star** proper name (`Vega`, `Sirius`, `Polaris`, …), case-insensitive exact match.
5. **DSO common name** (`Andromeda Galaxy`, `Orion Nebula`, …), case-insensitive substring match.

If nothing matches, the service replies with an `error` (see the error catalog below) — this can
only be detected at render time (after the catalogs are queried), so it arrives **after** the
`queued` reply, same as e.g. "target below horizon". `target.ra`/`target.dec` skip all of this and
are used as-is, so prefer them if the bot already has coordinates (e.g. from a previous resolution).

### Options (`options`)

Optional per-request overrides of `config.yaml`. Unknown keys are ignored.

| Key          | Applies to | Description                                                          |
|--------------|------------|----------------------------------------------------------------------|
| `style`      | all        | starplot theme name (e.g. `BLUE_NIGHT`, `GRAYSCALE`, `ANTIQUE`)      |
| `resolution` | all        | output image width in px (lower = faster/lighter on the Pi)         |
| `projection` | `full`     | map projection (e.g. `miller`)                                       |
| `direction`  | `horizon`  | compass facing: `N`, `NE`, `E`, `SE`, `S`, `SW`, `W`, `NW` (default `S`) |

---

## Processing model — queue

Rendering is **serialized**: matplotlib is not thread-safe and the Raspberry Pi cannot handle
parallel renders, so a **single worker thread renders one request at a time**.

1. Each incoming command is parsed and validated on a short-lived intake thread (off the MQTT
   network loop, so keepalive pings are never blocked).
2. A valid request is appended to a **bounded FIFO queue** and **immediately acknowledged** with a
   `queued` reply (see below) — the bot can show the user "accepted, please wait".
3. The worker pulls requests from the queue **in order** and renders them one by one, publishing a
   final `ok` / `error` reply for each.
4. If the queue is **full**, the request is rejected right away with `error: "queue full, try again
   later"` instead of blocking the bot.

The queue size is configured by `queue.max_size` (env `STARMAP_QUEUE_MAX_SIZE`); `0` means
unbounded. The request currently rendering is not counted against the limit.

So the normal lifetime of one `request_id` is **two replies**: first `queued`, then `ok` (or
`error`). A rejected request gets a single `error` reply.

---

## Response — `starmap/result`

Every reply echoes the originating `request_id` so the bot can match it to the request. There are
three reply shapes, distinguished by `status`.

### 1. Queued (acknowledgement)

Sent as soon as a valid request is enqueued:

```json
{
  "request_id": "1718635200",
  "status": "queued",
  "position": 0
}
```

`position` is the approximate number of renders that must finish before this one starts: requests
waiting ahead in the queue plus the one currently rendering, if any. `0` means it starts rendering
right away. It is a **best-effort hint**, not an exact count.

### 2. Success

On success the reply shape depends on `output.mode`.

**`file` mode (default)** — the PNG is written to the shared output directory as `<request_id>.png`
(sanitized) and the reply carries its path. The bot reads the file from disk (bot and service share
the same host/filesystem on the Pi):

```json
{
  "request_id": "1718635200",
  "status": "ok",
  "image_path": "/path/to/output/1718635200.png"
}
```

> **Why `file` is the default:** a full-sky PNG is a few MB, and as base64 it exceeds the MQTT
> broker's packet limit (mosquitto disconnects with "oversize packet"). Inline base64 only works for
> small charts. After each write the output directory is pruned by count/age (`output.retention`).

**`base64` mode** — the PNG is embedded in the reply (usable only for small images):

```json
{
  "request_id": "1718635200",
  "status": "ok",
  "image_base64": "..."
}
```

### 3. Error

Any problem — validation, queue full, or an unexpected render failure — is reported as a structured
`error` reply with a human-readable, bot-safe message. The service never leaks a Python traceback.

```json
{
  "request_id": "1718635200",
  "status": "error",
  "error": "Short description of the problem"
}
```

#### Error catalog

| Situation                                              | `error` message (example)                                          |
|--------------------------------------------------------|--------------------------------------------------------------------|
| Unknown chart type                                     | `unknown map_type 'foo'; expected one of: full, zenith, …`         |
| Coordinates required but missing                       | `map_type 'zenith' requires observer coordinates (observer.lat …)` |
| Coordinates not numeric / out of range                 | `observer.lat must be between -90 and 90`                          |
| Bad datetime format                                    | `datetime must be ISO 8601, e.g. 2026-06-17T22:00:00`              |
| `optic` without a target                               | `map_type 'optic' requires target.ra/target.dec (degrees) or target.object (e.g. 'M31')` |
| `optic` `target.object` doesn't resolve to anything    | `object 'Foo' not found; try a catalog number (M31, NGC224, IC1396), the Sun/Moon/a planet, a star name (Vega), or a common DSO name (Andromeda Galaxy)` |
| `optic` without an optic definition                    | `map_type 'optic' requires an 'optic' definition …`                |
| `optic` with an unknown optic type or missing field    | `unknown optic.type 'x'…` / `optic.fov is required for optic.type…`|
| `optic` target below the horizon at the given time     | `Target is below horizon at specified time/location.`              |
| `optic` field of view too wide (> 20°)                 | `Field of View too big: …`                                         |
| The render queue is full (too many pending requests)   | `queue full, try again later`                                      |
| Unexpected failure during rendering                    | `internal render error` (full traceback is logged server-side)     |

> **Note:** a command with no usable `request_id`, or one that is not valid JSON, cannot be matched
> to a caller, so it is logged and **dropped** — no reply is possible.

---

## Status — `starmap/status`

The service publishes its availability so the bot can tell the user when charts cannot be generated
instead of timing out.

- On startup it publishes a **retained** `{"status": "online"}`.
- It registers `{"status": "offline"}` as the **Last Will & Testament** (retained), so the broker
  broadcasts it if the service dies unexpectedly.
- On a clean shutdown (SIGINT/SIGTERM) it publishes `offline` before disconnecting.

Because the message is retained, a bot that connects later immediately receives the current status.

---

## Chart types (`map_type`)

| Type       | Description                                                            | Required input |
|------------|------------------------------------------------------------------------|----------------|
| `full`     | all-sky RA/DEC map (stars, constellations, DSOs, Milky Way…)           | —              |
| `galactic` | all-sky map in galactic coordinates (Mollweide)                        | —              |
| `zenith`   | the dome of sky overhead from the observer (with horizon circle)       | `observer`     |
| `horizon`  | sky above the horizon, centered on a compass direction                 | `observer`     |
| `optic`    | how a target looks through a given optic (telescope/binoculars/camera) | `observer`, `target`, `optic` |

Notes:

- `horizon` accepts `options.direction` (one of `N, NE, E, SE, S, SW, W, NW`; default `S`) to choose
  which 180°-wide swath of the horizon to show.
- `optic` accepts either explicit `target.ra`/`target.dec` (degrees) or `target.object` (a name,
  resolved server-side — see [Object name resolution](#object-name-resolution-targetobject)). If the
  target is below the horizon at the given time/place, or the field of view is too wide (> 20°), the
  service replies with an `error`.

---

## End-to-end flow

A typical successful exchange for one request:

```
bot → starmap/command   {"request_id":"42","map_type":"full"}
service → starmap/result {"request_id":"42","status":"queued","position":0}
service → starmap/result {"request_id":"42","status":"ok","image_path":"/app/output/42.png"}
```

When the service is busy, several requests queue up and are answered in order:

```
bot → starmap/command   {"request_id":"a", ...}
bot → starmap/command   {"request_id":"b", ...}
service → starmap/result {"request_id":"a","status":"queued","position":0}
service → starmap/result {"request_id":"b","status":"queued","position":1}
service → starmap/result {"request_id":"a","status":"ok","image_path":".../a.png"}
service → starmap/result {"request_id":"b","status":"ok","image_path":".../b.png"}
```

When the queue is full, extra requests are rejected immediately:

```
service → starmap/result {"request_id":"z","status":"error","error":"queue full, try again later"}
```
