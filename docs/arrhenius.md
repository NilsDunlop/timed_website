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

## M7 findings

- The complete website flow was validated against Arrhenius: browser-based
  password and verification-code authentication, data browsing, linking a real
  plate, opening a field, and using the Viv viewer.
- Real microscopy pixels rendered correctly and the channel, contrast, and time
  controls worked. The operator reported that the general responsiveness was
  good.
- Disconnect completed successfully. The local connector returned to a clean
  `disconnected` state with no retained job id.
- Pan and zoom work through Viv/deck.gl, but their gestures are not obvious in
  the current UI. A small help overlay plus visible reset/zoom controls would
  make navigation more discoverable.
- Moving to an uncached timepoint in a real field such as `D12_s1.zarr` takes
  roughly 5–10 seconds. Viv 0.23 renders the current single-resolution fields
  through `ImageLayer.getRaster`, so TIMED prefetches the next selected jump
  destination through that exact path and retains decoded pixels in a bounded
  cache. Completed prefetches become foreground cache hits; an in-flight
  prefetch has a short handoff window instead of being cancelled and restarted.

## A1 browser checklist

1. Confirm TIMED is preselected and unchanged from the TIMED-only release.
2. Choose **Cell Health**, then open A549, `SQ00014610`, well `A01`, site 1,
   and compare it with the inspection previews.
3. Compare `A02` (MCL1) on `SQ00014611` (A549), `SQ00014614` (ES2), and
   `SQ00014616` (HCC44), plus the `A01` EMPTY control on `SQ00014611`.
4. For the channel sanity check, open `SQ00014613` (ES2), well `A01` (EMPTY),
   site 1. Crop to pixels 216-432 in x and y, then use the **Grayscale**
   section to compare each channel with Cell Health v2.0's
   [`1.generate-profiles/figures/example_images/greyscale_channel.png`](https://github.com/broadinstitute/cell-health/blob/cd91bd0daacef2b5ea25dcceb62482bb664d9de1/1.generate-profiles/figures/example_images/greyscale_channel.png).
5. Disconnect after viewing images and confirm it reaches a final state within
   a few seconds.
6. Record field-opening timings in this document.
7. Choose Cell Health and TIMED from the Connect page. Confirm that the Data
   page follows the active choice and that returning from the viewer, using the
   Data navigation link, and reloading restore the last Cell Health plate and
   well.

## A1 findings (in progress)

- TIMED was preselected and unchanged from the TIMED-only release.
- A549 `SQ00014610`, well `A01`, site 1 matched the inspection screenshot. The
  channel-by-channel grayscale comparison in step 4 remains the independent
  check and must be repeated after the viewer changes above.
- The comparison in step 3 loaded quickly, stayed synchronized, and returned
  to the Data page successfully.
- Disconnecting after viewing images took about 2 seconds and ended in the
  `disconnected` state.
- A field opened in about 4-5 seconds on the operator's work network and about
  2 seconds at home, comparable to loading a TIMED trajectory. This result is
  accepted; do not try the performance fallbacks.
- The revised Connect-page dataset choice and place-restoration flow remains
  to be checked before A1 is complete.

## Troubleshooting

- Job pending indefinitely: verify the account and partition.
- No connection file: inspect `~/.timed/logs/<jobid>.out`.
- Tunnel failure: verify that the login node can reach the compute-node host.
- Data permission failure: verify read access from a compute node, not just login.
- uv/Python setup failure: check login-node internet access and available modules.

### Viewer loading diagnostics

To diagnose a frame that remains loading while the data server shows no new
GET requests, append `&debug=viewer` to the viewer URL and reload it. Keep the
browser developer tools Console open with Info messages enabled. Diagnostic
lines begin with `[TIMED viewer` and cover:

- requested, committed, rendering, and displayed timepoints;
- Zarr fetch start, response status, byte count, and elapsed time;
- decoded-pixel cache hits, misses, shared pending loads, handoffs, and evictions;
- prefetch scheduling, completion, cancellation, or failure;
- Viv viewport-complete callbacks, including callbacks ignored as stale; and
- a `frame:still-waiting` snapshot after ten seconds.

The messages omit authorization headers and tokens. Copy the messages from the
last `time:requested` or `time:commit` through `frame:still-waiting`, along
with the matching data-server GET log lines. Remove `debug=viewer` to disable
the diagnostics.

TIMED deliberately bypasses the browser HTTP cache for sharded byte-range
requests. Chromium sparse cache entries can reject concurrent operations for
the same shard and leave Viv waiting for one missing tile. Metadata remains
cacheable, identical shard-footer reads are shared in memory, and decoded image
rasters or tiles remain in TIMED's bounded cache.

## Questions for NAISS

- Is a service job reached through SSH port forwarding via the login node allowed?
- Which partition should host that long-running service job?
