# TIMED contributor guide

## Purpose

TIMED lets researchers browse and view time-resolved OME-Zarr microscopy data
where it lives. The priority path connects a browser to Arrhenius through a
local connector, an SSH tunnel, and a Slurm-hosted data server.

## Architecture

- `apps/web`: static Astro site with React islands. In development, Vite proxies
  `/connector` and `/backend` to `http://127.0.0.1:8700`.
- `services/connector`: local FastAPI/asyncssh service. It owns session state,
  SSH/Slurm lifecycle, and the authenticated reverse proxy.
- `services/dataserver`: token-protected FastAPI service beside the OME-Zarr data.
- `dev/test_cluster`: local SSH/Slurm-compatible harness (added in M4).
- `scripts`: metadata inventory and deterministic synthetic-data tools.

The browser talks only to the connector on the same origin. One session engine
serves both the HTTP UI and terminal CLI. The data-server package is identical
in local and HPC modes; only its launch mechanism changes.

## Commands

```bash
make dev          # connector plus Astro development servers (available after M4)
make test-cluster # local SSH/Slurm harness (available after M4)
make test         # Python and web checks
make test-e2e     # Playwright end-to-end suite (available after M6)
make test-data    # deterministic TIMED and native OME-Zarr fixtures
make gen-api      # regenerate typed OpenAPI clients (available after M6)
make lint         # Ruff and Astro checks
```

Direct workspace verification:

```bash
uv run pytest
uv run ruff check .
pnpm -C apps/web check
pnpm -C apps/web build
```

## Data server

Run `uv run timed-dataserver export-openapi` to print the complete API schema.
The `serve` command accepts one or more data roots, a host and port (including
dynamic port `0`), an environment, and a connection-file path.

The server writes the selected port and random bearer token to the connection
file only after Uvicorn is accepting connections, and removes the file on clean
shutdown. Every route requires that token. Linked datasets are stored atomically
in `<state-dir>/datasets.json`; unlinking never deletes image data.

## Conventions

- Pin every Python and Node dependency exactly; update deliberately.
- Build both Python workspace packages with the exactly pinned `uv_build`
  backend; root workspace dependencies expose both CLIs through `uv run`.
- Keep modules small and give each file one clear responsibility.
- Define Pydantic response models so generated OpenAPI is complete.
- Generate and commit TypeScript API types; use `openapi-fetch` at call sites.
- Use React state only. Components involving Viv/deck.gl must be mounted with
  Astro's `client:only="react"` directive because they require WebGL.
- Detect OME-Zarr through metadata, never through its directory suffix.
- Display plate names verbatim and load well metadata only on demand.

## Security rules

- Never persist or log passwords, verification codes, key passphrases, or
  data-server tokens. Never put them in URLs, snapshots, or screenshots.
- Bind the connector to `127.0.0.1` only. Bind the local data server to loopback;
  the HPC server binds the compute-node hostname and authenticates every request.
- Keep SSH host-key verification enabled. Never modify `~/.ssh/known_hosts` or
  `~/.ssh/config`; TIMED owns a separate known-hosts file.
- Restrict every filesystem operation to configured data roots and test path
  traversal and escaping symlinks.
- Do not commit real image data. Only metadata inventory and `CONVERSION.md` may
  be copied from Arrhenius.

## Playwright MCP

The project-scoped server is declared in `.mcp.json`. Restart the coding session
after first checkout and approve the project-scoped `playwright` server once.
Then run the app at `http://localhost:4321` and use the MCP browser after every UI
change to walk Connect -> Data -> View, capture relevant screenshots, and inspect
the browser console. Prefer headed Chromium for Viv; if WebGL is blank in
headless mode, add a checked-in MCP configuration with working SwiftShader flags.

## Real data

`docs/data/arrhenius-inventory.json` and `CONVERSION.md` are authoritative. The
eight plate-named `.zarr` directories are plain TIMED collection folders, not
Zarr groups or OME-Zarr plates. Each field is a bare Zarr v3 array named like
`B02_s1.zarr`.

- Every field is `uint16`, shape `(48, 3, 2500, 2500)`, axes `(t, c, y, x)`,
  and one resolution level. `dimension_names` is absent.
- Channels are 405, 505, and 625 nm. There are no OMERO colours or windows;
  use viewer defaults and computed statistics.
- Logical chunks are `(1, 1, 625, 625)` inside one whole-array shard of
  `(48, 3, 2500, 2500)`. Compression is Blosc Zstd level 5 with byte shuffle.
- P1 plates use rows B-O and columns 2-23: 308 wells and 616 fields each. P2
  plates use rows C-N and columns 3-22: 196 wells and 392 fields each. Every
  well has two fields, for 4,032 fields total.
- The field's `timed` attribute records well, site, axes, wavelengths, and time
  indices. Pixel size (0.3 micrometres) is documented only in conversion notes.
- The data server must provide a synthetic, read-only OME-Zarr metadata facade
  for Viv while mapping chunks to the untouched bare array. Keep standard
  OME-Zarr 0.4/v2 and 0.5/v3 support as separate tested paths.
- For a linked collection field `<field>.zarr`, the data server exposes a
  virtual OME-Zarr 0.5 group at that path and its array as `s0`. Requests below
  `s0/` map to the original field array, so shard bytes remain untouched and
  support HTTP Range requests.

`scripts/make_test_data.py` creates `test-data/test_plate.zarr` in the real
plain-collection layout: A1, A2, and B1 with two bare fields each, B2 absent,
six timepoints, three channels, 512 by 512 pixels, chunks `(1, 1, 128, 128)`,
and one whole-array shard per field. It also creates single-resolution native
`test_image_v04.zarr` (Zarr v2) and `test_image_v05.zarr` (Zarr v3). The output
is deterministic and gitignored.

## Known gotchas

- PowerShell may block npm's `.ps1` shim; use the `.cmd` shim when setting up a
  Windows workstation. Repository scripts themselves remain cross-platform.
- GNU Make is not installed on the current Windows workstation; run
  `uv run python scripts/make_test_data.py` as the direct equivalent of
  `make test-data` there.
- Windows does not expose meaningful POSIX mode bits. The connection-file test
  verifies mode `0600` on POSIX (including Arrhenius); Windows uses the file's
  inherited user ACL until local mode is implemented in M8.
- `ome-zarr==0.19.2` requires Pydantic below 2.13, so both services pin the
  newest compatible release, `pydantic==2.12.5`.
- M5 and M7 require the operator to enter Arrhenius password and 2FA input.
