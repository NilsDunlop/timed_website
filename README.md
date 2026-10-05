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
gitignored synthetic fixtures.

The real Arrhenius validation steps require the operator to enter their own SSH
password and verification code. Credentials and data-server tokens must never be
committed, logged, or included in screenshots.
