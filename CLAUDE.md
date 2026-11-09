# TIMED contributor guide

## Purpose

TIMED lets researchers browse and view microscopy data where it lives. It
supports native OME-Zarr, TIMED's bare-array collections, and packed Cell Health
(idr0080) images through a read-only virtual OME-Zarr facade. The priority path
connects a browser to Arrhenius through a local connector, an SSH tunnel, and a
Slurm-hosted data server.

## Architecture

- `apps/web`: static Astro site with React islands. In development, Vite proxies
  `/connector` and `/backend` to `http://127.0.0.1:8700`.
- `services/connector`: local FastAPI/asyncssh service. It owns session state,
  SSH/Slurm lifecycle, and the authenticated reverse proxy.
- `services/dataserver`: token-protected FastAPI service beside the image data;
  it serves native OME-Zarr and read-only facades for TIMED and packed indexes.
- `dev/test_cluster`: POSIX local SSH/Slurm-compatible harness.
- `scripts`: metadata inventory and deterministic synthetic-data tools.

The browser talks only to the connector on the same origin. One session engine
serves both the HTTP UI and terminal CLI. The data-server package is identical
in local and HPC modes; only its launch mechanism changes.

## Commands

```bash
make dev          # connector plus Astro, using config/profiles.example.yaml
make test-cluster # local SSH/Slurm harness (POSIX)
make test         # Python and web checks
make test-e2e     # isolated test cluster plus Playwright browser suite
make test-data    # deterministic TIMED and native OME-Zarr fixtures
make gen-api      # regenerate typed OpenAPI schemas and clients
make check-api    # regenerate, then fail if the committed API files differ
make lint         # Ruff and Astro checks
```

Direct workspace verification:

```bash
uv run pytest
uv run ruff check .
pnpm -C apps/web check
pnpm -C apps/web build
pnpm -C apps/web gen:api && git diff --exit-code -- apps/web/src/lib/api
uv run python scripts/run_e2e.py --local-only  # browser suite without the test cluster
```

The direct development command, including on Windows without GNU Make, is
`uv run python scripts/dev.py --profiles config/profiles.example.yaml`. Set the
Makefile's `PROFILES` variable only when deliberately using another profile
file.

## Data server

Run `uv run timed-dataserver export-openapi` to print the complete API schema.
The `serve` command accepts one or more data roots, a host and port (including
dynamic port `0`), an environment, a connection-file path and a state directory,
plus an optional metadata root, the packed and mask cache budgets, repeated
`--qc-export` and `--qc-annotations` `INDEX=FOLDER` mappings and repeated
`--timed-masks` and `--timed-segmentation` `COLLECTION=FOLDER` mappings.

The server writes the selected port and random bearer token to the connection
file only after Uvicorn is accepting connections, and removes the file during
shutdown, including after SIGTERM. Every route requires that token. Linked
datasets are stored atomically in `<state-dir>/datasets.json`; unlinking never
deletes image data. Error details name files relative to their dataset or root,
never by absolute path.

Parsed metadata files and TIMED plate listings are memoised by file identity
(device, inode, size and modification time), so a TIMED request no longer
rescans its plate or re-reads a field's `zarr.json`, while an added, removed or
rewritten field is picked up on the next request. Validation still runs on every
request; memoised metadata is shared and must not be mutated.

A packed dataset is detected from the schema of an `*.index.parquet` file and
its adjacent tar shards, not from its filename alone. The server presents
`<index>/<experiment>/<plate>` as a guarded virtual hierarchy. Each field is an
in-memory OME-Zarr 0.5 / Zarr v3 group whose `s0` chunks are encoded from the
original TIFF planes on demand; the index, tar shards, and TIFF payloads remain
untouched. The first cold request loads all channels for one field into a
single-flight, byte-bounded cache (1 GiB by default); `serve
--packed-cache-mb` changes that budget.

Mask routes under `/api/datasets/{id}/masks` read CellPrompter's `tar+wal/v1`
stores for packed plates whose code profile declares a masks folder; the
listing answers every linked dataset, with no models when it has none. Decoded
masks share a 512 MiB LRU (`serve --mask-cache-mb`) and outline PNGs a 256 MiB
one. Mask readers never open leases, `profiles/`, the run ledger or QC tables.
A TIMED collection's `masks_root` mirrors its plates and fields; each field's
`masks_raw` and `nuclei` arrays form model `masks_raw` (kinds `cells` and
`nuclei`). Its `segmentation_root` holds production runs at
`<plate>/sites/<well>_s<site>/stage1/<run>/labels/<plate>/<well>_s<site>.zarr`,
with the plate's name without `.zarr`; model `R4_cpsam_dS_corrected` pins its
cell run and the linked `R4_cpsam_dS_corrected_nuclei` run. Run arrays store
no axes, so their (t, y, x) order is pinned, but each must store the plate,
well, site and run id its path names. The production model is listed first and
is the default wherever it covers a field. Every TIMED mask array holds one
plane per image timepoint, so counts and outlines take `?t=`, and caches and
ETags key on `t` and the files' identities. TIMED masks appear only in a
listing that names a field with `well` and `site`; fields without masks list
none. Mask folders are read-only and, like the QC folders, never browsable,
linkable or served.

## Connector and test cluster

`config/profiles.example.yaml` is the repository's active development profile;
do not assume a separate `~/.timed/profiles.yaml` on the operator workstation.
Its Arrhenius profile declares both the **TIMED** and **Cell Health** collections,
with TIMED carrying the sole `default: true` and reading the production
segmentation under `TIMED/seg/track_v0/production/seg-track-v0`. Dataset choice
remains available on Connect before and after connecting. The Berzelius and
`local-timed` profiles expose only TIMED; the latter reads the external downtime
mirror at `D:/TIMED`, including the export masks under `D:/TIMED/corrected` and
the production segmentation under `D:/TIMED/seg`, and must remain read-only. For a manual test-cluster run, replace
the profile's absolute-path placeholders, run `make test-cluster` on
macOS/Linux (or WSL), and run `make dev` in another terminal.

The connector binds loopback only, validates `Host`, and requires
`X-Timed-Client: web` on state-changing connector routes. Authentication
answers are held only by an in-memory future; the public session model carries
each prompt's text and echo flag, never an answer. The data-server token remains
private to the connector's proxy. The test cluster chroots SFTP to its
disposable home and allows direct TCP forwarding only to loopback destinations.

## Local mode

A `local` profile starts the same `timed-dataserver` package as a child process
on `127.0.0.1` with a dynamic port. Its token-bearing connection file lives in
a private temporary directory and is removed on disconnect, including after a
forced Windows process termination. The local dataset registry persists in
`~/.timed/local` by default; `timed-connector serve --local-state-dir` can
override that location for isolated tests.

The session state machine dispatches local profiles directly to
`LocalBackend`. It does not emulate SSH, Slurm jobs, queues, or tunnels. Child
output is streamed into the normal bounded session log, unexpected process exit
becomes a visible error, and disconnect always stops the child regardless of
the Slurm-specific `cancel_job` request field.

## Web application

The four Astro pages mount React islands for the Connect, Data, View, and QC
workflows. API calls use the committed `openapi-typescript` schemas and
`openapi-fetch` clients in `apps/web/src/lib/api`. Run `make gen-api` after a
response model or route changes, then commit the regenerated files.

For profiles with collections, Connect owns the active Dataset choice and
remembers it per profile. Data shows only the active collection, restores the
last opened plate and focused well separately for each collection, and offers a
small **Change dataset** link. Explicit `collection` and `dataset` URL
parameters override saved state. Profiles without collections keep the legacy
Connect page and filesystem Data browser unchanged.

The viewer pins Viv 0.23 with deck.gl 9.4.0 and luma.gl 9.4.2. Native
OME-Zarr 0.4/Zarr v2 data loads through Viv's URL loader. OME-Zarr 0.5/Zarr v3
and TIMED's synthetic field facade use a same-origin Zarrita store that skips
the v2 metadata probes, preventing expected 404 responses from appearing as
browser console errors. The data server implements GET and HEAD for Zarr
resources because sharded v3 reads discover object lengths before sending byte
ranges.

Cell Health declares Color and Grayscale support. Color uses a DNA/ER/AGP
composite by default; Grayscale shows one selected channel in white. The
**Display** switch appears only when every loaded source explicitly declares
the capability, including in comparison mode. TIMED deliberately omits the
declaration and retains its original composite channel controls, contrast, time
navigation, and workflow. Cell Health carries the selected well between plates,
highlights the same gene or controls on the active grid, and compares up to four
channel-compatible fields.

Every viewer panel derives its own scale bar from source metadata. TIMED uses
0.3 micrometres per pixel and Cell Health uses
0.29898804047838085 micrometres per pixel. A source without a valid pixel size
gets no scale bar; never infer one.

When every source's field has masks, a **Segmentation** panel joins the viewer
controls, which the QC field view shares: the models every source covers
(default first, full tag, recipe on its own line), a "May still change" badge
for unfrozen models, each source's object counts (breaking only between
kinds), kind toggles that start off, width and opacity. Listings load beside
the image. Outlines are `VivCanvas` overlay layers that follow the displayed
timepoint and never affect "Image ready"; on a source with a time axis
(TIMED) its counts follow the displayed timepoint too, and nothing is fetched
for a frame before it is on screen. `outlines=` and `model=` keep them in the
link. A TIMED field without masks shows no panel.

`make test-e2e` creates isolated legacy and collection-aware test-cluster and
local profiles. It drives Connect -> Data -> View for TIMED and the packed
idr0080-like fixture, verifies viewer state and pixels, fails every flow on an
unexpected browser console error (`tests/e2e/fixtures.ts`), disconnects,
confirms that no Slurm job remains, and proves that the local child stops.
Successful runs retain screenshots under the gitignored Playwright
test-results folder.

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
- Keep collection-aware behavior optional; profiles without collections retain
  the legacy filesystem flow.
- Treat packed indexes and shards as immutable inputs and expose them through a
  facade rather than writing converted data.

## Security rules

- Never persist or log passwords, verification codes, key passphrases, or
  data-server tokens. Never put them in URLs, snapshots, or screenshots.
- Bind the connector to `127.0.0.1` only. Bind the local data server to loopback;
  the HPC server binds the compute-node hostname and authenticates every request.
- Keep SSH host-key verification enabled. Never modify `~/.ssh/known_hosts` or
  `~/.ssh/config`; TIMED owns a separate known-hosts file.
- Restrict every filesystem operation to configured data roots and test path
  traversal and escaping symlinks.
- Open packed indexes, tar shards, and metadata read-only. Never write into a
  source dataset directory.
- Do not commit real image data. Only metadata inventory and `CONVERSION.md` may
  be copied from Arrhenius.

## Playwright MCP

The project-scoped server is declared in `.mcp.json`, pinned to
`@playwright/mcp` 0.0.80 with `--browser chromium`, which uses the Chromium build
the browser suite installs. Restart the coding session after first checkout and
approve the project-scoped `playwright` server once. Then run the app at
`http://localhost:4321` and use the MCP browser after every UI change to walk
Connect -> Data -> View, capture relevant screenshots, and inspect the browser
console. Prefer headed Chromium for Viv; if WebGL is blank in headless mode, add
a checked-in MCP configuration with working SwiftShader flags.

## TIMED real data

`docs/data/arrhenius-inventory.json` and `CONVERSION.md` are authoritative. The
eight plate-named `.zarr` directories are plain TIMED collection folders, not
Zarr groups or OME-Zarr plates. Each field is a bare Zarr v3 array named like
`B02_s1.zarr`.

- Every field is `uint16`, shape `(48, 3, 2500, 2500)`, axes `(t, c, y, x)`,
  and one resolution level. `dimension_names` is absent.
- Channels are 405, 505, and 625 nm. The arrays carry no OMERO colours or
  windows; the facade declares blue, green and red with a fixed 100-65,535
  window, so TIMED never falls back to computed statistics.
- Arrhenius logical chunks are `(1, 1, 625, 625)` inside one whole-array shard
  of `(48, 3, 2500, 2500)`. The Berzelius P2.1 copy instead uses whole-plane
  `(1, 1, 2500, 2500)` chunks. The server derives and strictly validates each
  field's geometry rather than assuming either layout. Compression is Blosc
  Zstd level 5 with byte shuffle.
- P1 plates use rows B-O and columns 2-23: 308 wells and 616 fields each. P2
  plates use rows C-N and columns 3-22: 196 wells and 392 fields each. Every
  well has two fields, for 4,032 fields total.
- The field's `timed` attribute records well, site, axes, wavelengths, and time
  indices. Pixel size (0.3 micrometres) is documented only in conversion notes.
- The Berzelius plate root is a Zarr v3 group above its bare field arrays and
  may carry validated `site_position_fix` metadata. See `docs/berzelius.md` for
  the layout, generated per-plate metadata and read-only real-copy checks.
- The data server must provide a synthetic, read-only OME-Zarr metadata facade
  for Viv while mapping chunks to the untouched bare array. Keep standard
  OME-Zarr 0.4/v2 and 0.5/v3 support as separate tested paths.
- For a linked collection field `<field>.zarr`, the data server exposes a
  virtual OME-Zarr 0.5 group at that path and its array as `s0`. Requests below
  `s0/` map to the original field array, so shard bytes remain untouched and
  support HTTP Range requests.

`scripts/make_test_data.py` creates `test-data/test_plate.zarr` in the real
plain-collection layout: A01, A02, and B01 with two bare fields each, B02
absent, six timepoints, three channels, 512 by 512 pixels, chunks
`(1, 1, 128, 128)`, and one whole-array shard per field. `test_plate_2.zarr` to
`test_plate_4.zarr` hold B01's two fields for comparisons, and `metadata/` holds
each plate's metadata CSV and acquisition file. It also creates
single-resolution native `test_image_v04.zarr` (Zarr v2) and
`test_image_v05.zarr` (Zarr v3), the packed idr0080-like fixture under
`packed/idr0080-like/` (an index beside three plate shards of five-channel,
64 by 64 fields, with well metadata, `tar+wal/v1` mask stores for two models
from `scripts/mask_stores.py`, and decoy fragment and mask indexes),
`timed-masks/` (time-varying `masks_raw` and `nuclei` arrays for four TIMED
fields) and `timed-seg/` (the production layout for those fields and
test_plate A02 s2), both from `scripts/timed_mask_stores.py`, and the QC exports
`qc-export/` and `qc-export-next/`. The output is deterministic and gitignored.

## Cell Health / idr0080 real data

The Cell Health collection points to `idr0080-raw.index.parquet` beside nine tar
shards. It contains three experiments (A549, ES2, and HCC44), three plates per
cell line, all 384 wells, nine sites per well, and five single-timepoint stains:
DNA, ER, AGP, RNA, and Mito in packed slot order. The real index has 155,520
rows for 31,104 fields.
Each source plane is an uncompressed 2160 by 2160 `uint16` TIFF inside its plate
shard; the facade presents each field as `(5, 2160, 2160)` with 1080 by 1080
spatial chunks.

The exact real and fixture profiles are pinned in code. Real idr0080 uses fixed
cell-line contrast windows, the RxRx stain colours, DNA/ER/AGP active by
default, and the calibrated pixel size above. Well metadata comes from
`metadata/metadata_idr0080.csv`; a missing metadata file makes metadata
unavailable without blocking image browsing. Other valid packed indexes use a
conservative inferred profile with numeric channel labels and automatic
contrast.

Cell Health plate switching preserves the selected well. **Same gene** and
**Controls** highlight the active plate grid and are mutually exclusive. The
server retains the packed matches endpoint for direct API use and tests, but
the current UI does not call it. A collection may separately declare a QC
export and annotations folder. The QC page is dictionary-driven and uses
progressive disclosure: readable metric selection, compact Sites/Wells tables
with **Flagged by**, and secondary filters and columns on demand. Field review
uses the export's fixed plate-by-channel windows and shows only primary review
metrics, with stain channels before whole-field checks. Session well queues
retain their exact flagged sites. The data server validates the whole export
before serving it and annotations are its only QC write.
Segmentation outlines (Part C) are a sanity check on the masks: they are not
part of QC review and never change image loading or "Image ready".

## Known gotchas

- PowerShell may block npm's `.ps1` shim; use the `.cmd` shim when setting up a
  Windows workstation. Repository scripts themselves remain cross-platform.
- GNU Make is not installed on the current Windows workstation; run
  `uv run python scripts/make_test_data.py` as the direct equivalent of
  `make test-data` there.
- Windows does not expose meaningful POSIX mode bits. The connection-file test
  verifies mode `0600` on POSIX (including Arrhenius); Windows local mode uses
  the private temporary directory's inherited user ACL.
- The test cluster itself is POSIX-only, so the cluster test and the tests
  marked `posix_only` or `needs_symlinks` skip on Windows. Run them from this
  workstation in WSL2 with an isolated environment, last verified on
  2026-11-06: `UV_PROJECT_ENVIRONMENT=/tmp/timed-wsl-venv uv run --frozen pytest
  -m "needs_symlinks or posix_only"`. On Windows, `scripts/run_e2e.py` starts
  the test cluster inside WSL with uv; `--local-only` skips it.
- `ome-zarr==0.19.2` requires Pydantic below 2.13, so both services pin the
  newest compatible release, `pydantic==2.12.5`.
- `openapi-typescript==7.13.0` declares a TypeScript 5 peer range while this
  workspace pins TypeScript 6. API generation, Astro diagnostics, and the
  production build are verified; `pnpm peers check` reports that one known
  metadata mismatch.
- M5 and M7 require the operator to enter Arrhenius password and 2FA input.
- Keep `.canvas-frame` positioned; deck.gl otherwise places its canvas relative
  to the page and can cover the viewer controls.
- Viv 0.23 recreates its deck.gl tile cache when `selections` change. TIMED
  therefore keeps a lazily allocated 256 MiB decoded-pixel cache below Viv,
  prefetches one adjacent timepoint with bounded concurrency, and distinguishes
  requested, rendering, and displayed time. Preserve those semantics when
  changing the viewer; readiness must describe pixels on screen, not the slider
  value.
- `scripts/run_e2e.py --built-ui --connector-port <free-port>` serves the
  production build directly from an isolated connector. Use it when another
  Astro development server already owns this checkout's singleton dev lock.
