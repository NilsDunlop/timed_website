# TIMED imaging platform

TIMED is a prototype web platform for interactively viewing time-resolved
OME-Zarr microscopy data without moving full datasets away from their storage.
Its primary target is the Arrhenius NAISS cluster: a local connector establishes
SSH, starts a token-protected data server in Slurm, and exposes it to a same-origin
Astro web application.

The repository is being delivered milestone by milestone. Milestone 0 provides
the workspace, placeholder web app, package skeletons, shared commands, and
project-scoped Playwright MCP configuration.

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
- M2-M8: pending

The real Arrhenius validation steps require the operator to enter their own SSH
password and verification code. Credentials and data-server tokens must never be
committed, logged, or included in screenshots.
