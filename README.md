# TIMED imaging platform

TIMED is a web platform for interactively browsing and viewing microscopy data
without moving full datasets away from their storage. It supports native
OME-Zarr, TIMED's bare-array collections, and the packed Cell Health (idr0080)
dataset through a read-only virtual OME-Zarr facade. Its primary target is the
Arrhenius NAISS cluster: a local connector establishes SSH, starts a
token-protected data server in Slurm, and exposes it to a same-origin Astro web
application.

The repository includes deterministic fixtures, the authenticated data server,
the local/HPC connector, collection-aware dataset navigation, and the
interactive viewer workflow.

## Prerequisites

- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/)
- Node.js 24 LTS and pnpm 12.9.1

## Quick start

```bash
uv sync
pnpm install --frozen-lockfile
uv run pytest
pnpm -C apps/web check
pnpm -C apps/web build
```

Common development commands are documented in `CLAUDE.md` and exposed through
the root `Makefile`.

The Makefile uses `config/profiles.example.yaml` by default. The equivalent
cross-platform command is:

```bash
uv run python scripts/dev.py --profiles config/profiles.example.yaml
```

That file keeps **TIMED** as the default Arrhenius collection and offers
**Cell Health** as the idr0080 collection. It also includes the TIMED-only
Berzelius profile and a **Local TIMED (external drive)** profile for the
read-only downtime mirror at `D:/TIMED`. Generate the fixtures with
`make test-data` (or `uv run python scripts/make_test_data.py`), then choose
**This computer with datasets** to exercise the same collection flow locally.

The isolated browser suite exercises both the SSH/Slurm harness and local mode:

```bash
pnpm -C apps/web test
```

On Windows the suite starts the test cluster inside WSL with uv;
`uv run python scripts/run_e2e.py --local-only` runs it without the cluster.

An external real-data sample can be tested without copying it into the
repository. The store must be an immediate child of the supplied data root:

```bash
uv run python scripts/run_e2e.py \
  --real-local-root /path/to/local/data \
  --real-local-store sample-plate.zarr
```

This opt-in flow renders the first field, verifies three adjacent timepoints,
prints render-completion timings, and shuts down its local data server. If a
development server is already running, validate the production build without
interrupting it:

```bash
pnpm -C apps/web build
uv run python scripts/run_e2e.py --built-ui --connector-port 8870 \
  --real-local-root /path/to/local/data \
  --real-local-store sample-plate.zarr
```

To inspect experiment/acquisition metadata for metadata-aware plate views, run
the read-only inspector in an environment with PyArrow and save its JSON report:

```bash
uv run --with pyarrow python scripts/inspect_timed_metadata.py \
  /path/to/TIMED/metadata --output timed-metadata-inspection.json
```

The inspector reads no image chunks. It summarizes acquisition JSON, CSV and
Parquet schemas and values, redacts credential-like fields, and caps table reads
at 200,000 rows per file by default.

To enable plate and well metadata in the application, set `metadata_root` next
to `data_roots` in the selected profile:

```yaml
metadata_root: /path/to/TIMED/metadata
```

TIMED then loads the matching per-plate CSV and acquisition JSON on demand. The
plate view can highlight wells by compound, mechanism class, or control status,
and both the plate view and image viewer show structured experiment information.
The inspected high-level layout and join rules are documented in
[`docs/data/METADATA.md`](docs/data/METADATA.md); raw inspection output and
instrument-specific details remain local.

## Collections and Cell Health

Profiles may declare ordered collections. For those profiles, the Connect page
owns the active **Dataset** choice and remembers it per profile. The Data page
shows only that collection and restores its last opened plate and selected
well; explicit `collection` and `dataset` URL parameters remain deep links.
Profiles without collections retain the original filesystem browser.

Cell Health browses idr0080 by cell line, plate, well, and site, with gene,
guide, control, and well-type metadata. In packed slot order, its five stains
are DNA, ER, AGP, RNA, and Mito. Color mode starts with the DNA/ER/AGP
composite; Grayscale presents one channel at a time. The Display switch is
shown only when every loaded
source declares that capability, so TIMED retains its original channel and time
controls. Cell Health preserves a selected well across plates, can highlight
the same gene or controls on the active plate, and uses the existing
four-field comparison tray.

The data server opens idr0080's Parquet index and adjacent tar shards read-only,
then exposes each field as an in-memory OME-Zarr 0.5 / Zarr v3 hierarchy. The
source TIFF planes are decoded and cached on demand; no converted dataset is
written. Calibrated sources show an independently computed scale bar in every
viewer panel, including comparisons, while uncalibrated sources show none.

See [`docs/data/IDR0080.md`](docs/data/IDR0080.md) for the verified data facts
and [`docs/ADDING_DATASETS.md`](docs/ADDING_DATASETS.md) for the supported
dataset contracts and onboarding steps.

## Current status

- The original TIMED prototype milestones M0-M8 are complete, including local
  subprocess mode and the Arrhenius SSH/Slurm path.
- Cell Health Phase 1 is complete: collection selection, packed-image browsing,
  metadata-aware plate views, Color/Grayscale viewing, comparison, and scale
  bars have been accepted on Arrhenius.
- Cell Health QC review (Part B) was accepted on Arrhenius and released as
  0.5.0. The optional QC page reads a versioned export, presents a compact
  reason-first Sites/Wells workflow, carries exact flagged sites into well
  review, and records append-only site or well annotations. The viewer applies
  the export's fixed QC windows and shows only registered review metrics.
- Version 0.5.1 adds strict, geometry-independent TIMED field validation for
  the Berzelius P2.1 layout, its per-plate metadata and optional site-repair
  note. The same layout is validated read-only through the external-drive
  mirror while Berzelius is unavailable.
- Version 0.5.2 restructures the data server and viewer for segmentation
  without changing behaviour, the API, URLs or stored data.
- Version 0.5.3 fixes the review findings: exact contrast sliders, the QC
  overview's open state and site scope, path-free error details, private QC
  folders, a Windows reviewer rule, connection-file removal after SIGTERM, and
  remote installs constrained to `uv.lock`. The viewer no longer shows the
  site-position repair note.
- Version 0.6.0 adds segmentation outlines for Cell Health (Part C): the viewer
  and the QC field view offer each plate's mask models, their object counts and
  nucleus, cell and cytoplasm outlines, read-only from CellPrompter's stores.
  TIMED is unchanged.

Choose **Arrhenius (NAISS)** for the primary HPC path, **Berzelius (NSC)** for
the temporary TIMED-only Slurm path, or **Local TIMED (external drive)** for its
local read-only mirror. **This computer** and the collection-aware fixture
profiles use the gitignored synthetic datasets.

The real Arrhenius validation steps require the operator to enter their own SSH
password and verification code. Credentials and data-server tokens must never be
committed, logged, or included in screenshots.
