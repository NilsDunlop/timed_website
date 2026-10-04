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
make test-data    # deterministic OME-Zarr fixtures (available after M2)
make gen-api      # regenerate typed OpenAPI clients (available after M6)
make lint         # Ruff and Astro checks
```

Direct M0 verification:

```bash
uv run pytest
uv run ruff check .
pnpm -C apps/web check
pnpm -C apps/web build
```

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

The authoritative description will be added in M1 from
`docs/data/arrhenius-inventory.json`. Until then, synthetic data defaults to
OME-Zarr 0.4, Zarr v2, axes `(t, c, z, y, x)`, `uint16`, two pyramid levels, and
chunks `(1, 1, 1, 256, 256)`.

## Known gotchas

- PowerShell may block npm's `.ps1` shim; use the `.cmd` shim when setting up a
  Windows workstation. Repository scripts themselves remain cross-platform.
- M1, M5, and M7 require the operator to enter Arrhenius password and 2FA input.
