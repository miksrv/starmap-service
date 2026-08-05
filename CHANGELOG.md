# CHANGELOG

## 1.0.0

### Major Changes

- Added object-name resolution for `optic` targets (`target.object`): catalog numbers (`M31`/`NGC224`/`IC1396` in any spacing/casing), the Sun/Moon, planets, star proper names, and DSO common names, resolved server-side against the already-loaded catalogs; request validation now accepts a deferred lookup instead of rejecting `target.object` outright
- Rendered planets, the Moon (true size + phase), and the Sun on `full`, `galactic`, `zenith`, and `horizon` charts, always using the request's `datetime` (not the real-world current time) even when `lat`/`lon` are omitted
- Added DSOs alongside solar-system bodies to the `full` chart before the Milky Way and gridlines are drawn
- Fixed a crash in `optic` rendering caused by `planets(true_size=True)` when a planet fell far outside the narrow field of view; whole-sky Moon rendering now uses a marker icon rather than a true-size circle, since the true angular size is imperceptible at that scale
- Unified marker/font scaling across `full`, `zenith`, `horizon`, and `galactic` with a shared `_scale_for(resolution)`, replacing starplot's own autoscaling (which drifted from the 6000px/0.8 baseline the styles were tuned for)
- Fixed uneven `N`/`E`/`S`/`W` label placement on `zenith` charts (starplot anchors them at inconsistent radii, now re-centered to a uniform radius after `horizon()` renders them)
- Fixed the horizon ground-bar/divider-line overlapping the azimuth scale on `horizon` charts, moved cardinal labels below the bar, and reduced their font size for readability
- Fixed starplot catalog/DuckDB-extension downloads landing in the process's working directory instead of the persistent data directory, by setting `STARPLOT_DATA_PATH` explicitly in both Docker and the systemd unit; refreshed the bundled DuckDB spatial extension to a single current version
- Refreshed `CLAUDE.md`/`ROADMAP.md` to match the implemented state (tests, Docker, queue/worker, LWT/status) and documented the `ZenithPlot.info()` starplot 0.20.4 bug
- Documented the new `optic`/`target.object` behavior in `API.md` and `README.md`, including a sample optic chart image

## 0.1.0

### Minor Changes

- Implemented the initial MQTT service: `starmap/command` → validate → enqueue → render → `starmap/result`, with a bounded FIFO queue, a single render worker, and retained `online`/`offline` status via Last Will & Testament
- Implemented all five chart types via starplot: `full` (all-sky RA/DEC), `galactic` (Mollweide), `zenith` (dome overhead), `horizon` (compass-direction panorama), and `optic` (telescope/binoculars/camera view)
- Added `src/request.py` (`parse_command` → `RenderRequest`) with per-`map_type` validation of `observer`, `target`, `optic`, and `options` blocks, and `src/errors.py` (`ValidationError`) so every failure surfaces as a bot-safe `error` reply
- Added `src/config.py` (config.yaml + environment-variable overrides) and `src/storage.py` (file-mode output with retention pruning by count/age)
- Added Raspberry Pi deployment tooling: `scripts/install.sh` (venv + deps + systemd unit + catalog fetch), `start.sh`/`stop.sh`/`restart.sh`, and the `systemd/starmap.service` unit template
- Added Docker local-dev setup (`Dockerfile`, `docker-compose.yml`) with a throwaway Mosquitto broker, and `scripts/send_request.py` as an MQTT test client
- Added `scripts/fetch-data.sh` to download starplot's star/DSO/constellation catalogs and the matching DuckDB spatial extension; bundled the `linux_arm64` DuckDB extension builds for the Pi
- Documented the full MQTT contract in `API.md` (topics, request/response schemas, optic definitions, error catalog, queue behavior), project usage in `README.md`, and architecture/planning notes in `CLAUDE.md`/`ROADMAP.md`
- Added a pytest suite (config, request parsing, errors, renderer, storage, service) with a lightweight `starplot` stub so tests run without the heavy real dependency
- Set up CI: a `check.yml` workflow (black/isort/pylint/pytest) and a SonarCloud quality-gate workflow, plus coverage/quality badges in the README
- Defaulted `output.mode` to `file` (a full-sky PNG as base64 exceeds the MQTT broker's packet limit) and tightened `optic` target validation
- Added sample chart images and a cover image to the README
