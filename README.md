# TIMED imaging platform

TIMED is a prototype web platform for interactively viewing time-resolved
OME-Zarr microscopy data without moving full datasets away from their storage.
Its primary target is the Arrhenius NAISS cluster: a local connector establishes
SSH, starts a token-protected data server in Slurm, and exposes it to a same-origin
Astro web application.

The repository is being delivered milestone by milestone. It now includes
deterministic fixtures, the authenticated read-only data server, the local/HPC
connector, and the interactive browser workflow.

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

The isolated browser suite exercises both the SSH/Slurm harness and local mode:

```bash
pnpm -C apps/web test
```

An external real-data sample can be tested without copying it into the
repository. The store must be an immediate child of the supplied data root:

```bash
uv run python scripts/run_e2e.py \
  --real-local-root /path/to/local/data \
  --real-local-store sample-plate.zarr
```

This opt-in flow renders the first field, verifies three adjacent timepoints,
prints settled transfer timings, and shuts down its local data server.

To inspect experiment/acquisition metadata for metadata-aware plate views, run
the read-only inspector in an environment with PyArrow and save its JSON report:

```bash
uv run --with pyarrow python scripts/inspect_timed_metadata.py \
  /path/to/TIMED/metadata --output timed-metadata-inspection.json
```

The inspector reads no image chunks. It summarizes acquisition JSON, CSV and
Parquet schemas and values, redacts credential-like fields, and caps table reads
at 200,000 rows per file by default.

## Current status

- M0: scaffold and tooling complete
- M1: real Arrhenius metadata inventoried and documented
- M2: inventory-matched synthetic data complete
- M3: authenticated read-only data server complete
- M4: connector and local SSH/Slurm test cluster complete
- M5: real Arrhenius smoke test complete
- M6: Connect, Data, and Viv viewer workflow complete
- M7: real Arrhenius browser workflow complete
- M8: local subprocess mode complete

The full prototype scope is implemented. Choose **Arrhenius (NAISS)** for the
primary HPC path or **This computer** to launch a loopback data server over the
configured local data roots. The example local profile uses the gitignored
synthetic fixtures.

The real Arrhenius validation steps require the operator to enter their own SSH
password and verification code. Credentials and data-server tokens must never be
committed, logged, or included in screenshots.
