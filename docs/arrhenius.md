# Arrhenius setup and validation

This document will be filled with verified observations during M5. Do not record
passwords, verification codes, key passphrases, or data-server tokens here.

## Profile

- Host: `login.hpc.arrhenius.naiss.se`
- User: `nilsdu`
- Slurm account: `naiss2026-3-679`
- CPU partition: `naiss2026-3-679-cpu`
- Data root: `/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/TIMED/zarr`

## M5 findings

- Login-node Python and uv: not yet checked.
- Login-node internet access: not yet checked.
- Compute-node data access: not yet checked.
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
