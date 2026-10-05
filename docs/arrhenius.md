# Arrhenius setup and validation

This document will be filled with verified observations during M5. Do not record
passwords, verification codes, key passphrases, or data-server tokens here.

## Profile

- Host: `login.hpc.arrhenius.naiss.se`
- User: `nilsdu`
- Slurm account: `naiss2026-3-679-cpu`
- CPU partition: `cpu`
- Data root: `/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/TIMED/zarr`

## M1 findings

- The login node's system `python3` ran the standard-library metadata inventory.
- The login node could read metadata for all eight plate collections and all
  4,032 field arrays without errors.
- This confirms login-node read access only; compute-node access remains an M5
  check.
- The real layout is documented in `docs/data/arrhenius-inventory.json`,
  `docs/data/CONVERSION.md`, `CLAUDE.md`, and `docs/decisions.md`.

## M5 findings

- Authentication with password and verification code works.
- Login-node internet access works: the connector installed `uv` and the
  `timed-dataserver` 0.1.0 environment remotely.
- The first submission failed before a job was created because the profile had
  swapped Slurm values: `-A naiss2026-3-679-cpu` and `-p cpu` are the verified
  Arrhenius CPU directives.
- The corrected profile submitted a job, reached compute node `n213`, started
  Python 3.12.15, and completed the authenticated API handshake.
- Compute-node data access works. The server listed the configured root and
  identified all eight plate directories as OME-Zarr 0.5 / Zarr v3.
- The terminal smoke test then exposed a local null-handling bug because the
  root also contains `CONVERSION.md`; the CLI now skips non-Zarr entries.
- The final terminal smoke test linked `PB100087_P1.1.zarr`, printed its plate
  and first-image summary (48 timepoints, three fluorescence channels), and
  fetched 32 bytes from a real Zarr v3 chunk through the authenticated tunnel.
- Normal `timed-connector try` exit uses the default cancellation cleanup; no
  `--keep-job` flag was used.
- Typical scheduling delay: not yet measured.

## M7 browser checklist

1. Connect with **Arrhenius (NAISS)**.
2. Enter the password and verification code in the UI.
3. If asked, compare the displayed host-key fingerprint with a known-good
   terminal session before accepting it.
4. Watch the queue state and pending reason until connected.
5. Browse the configured data folder and confirm that all eight plates appear.
6. Link `PB100087_P1.1.zarr` and open a field.
7. Exercise channel toggles, contrast controls, and the time slider.
8. Disconnect with job cancellation enabled.
9. In a terminal, confirm `squeue -u nilsdu` has no `timed-dataserver` job.

## Troubleshooting

- Job pending indefinitely: verify the account and partition.
- No connection file: inspect `~/.timed/logs/<jobid>.out`.
- Tunnel failure: verify that the login node can reach the compute-node host.
- Data permission failure: verify read access from a compute node, not just login.
- uv/Python setup failure: check login-node internet access and available modules.

## Questions for NAISS

- Is a service job reached through SSH port forwarding via the login node allowed?
- Which partition should host that long-running service job?
