# data/

starplot's data catalogs live here. The service points starplot at this
directory via `settings.data_path` (configured in `src/config.py` as
`DATA_DIR`; override with the `STARPLOT_DATA_PATH` environment variable).

## Expected contents

| File / dir                              | Purpose                                  |
|-----------------------------------------|------------------------------------------|
| `stars.bigksy.*.parquet`                | star catalog (~53 MB)                    |
| `ongc.*.parquet`                        | deep sky objects (OpenNGC, ~15 MB)       |
| `constellations.*.parquet`              | constellation lines                      |
| `constellations-borders-*.parquet`      | constellation boundaries                 |
| `milky_way-*.parquet`                   | Milky Way outline                        |
| `de421.bsp`                             | JPL ephemeris for planet/Moon positions  |
| `duckdb-extensions/`                    | DuckDB spatial extension (per-arch)      |

## Why most of these are not in git

The parquet catalogs and `de421.bsp` are large, so they are git-ignored (see
`.gitignore`). Obtain them in any of these ways:

- **`bash scripts/fetch-data.sh`** — runs `starplot setup` with
  `STARPLOT_DATA_PATH` pointed at this directory (downloads catalogs, installs
  the DuckDB spatial extension, builds the font cache). `scripts/install.sh`
  calls this automatically on the Pi when the catalogs are missing.
- **Automatically** — starplot downloads any missing files into this directory
  the first time a plot is created.
- **Copy an existing `data/`** to the host (e.g. `rsync` to the Pi) if you
  already have the files and want to avoid re-downloading.

The committed `duckdb-extensions/` currently ships the **linux_arm64** build for
the Raspberry Pi. On a different architecture (e.g. an Intel mac running the
Docker image), starplot will download the matching extension into this mounted
directory on first use.

## Docker

This directory is mounted into the container at `/app/data` (see
`docker-compose.yml`) rather than baked into the image, so the ~85 MB of data
does not bloat the image and persists across rebuilds.
