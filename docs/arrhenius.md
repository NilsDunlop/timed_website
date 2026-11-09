# Arrhenius setup and validation

This document will be filled with verified observations during M5. Do not record
passwords, verification codes, key passphrases, or data-server tokens here.

## Profile

- Host: `login.hpc.arrhenius.naiss.se`
- User: each person's own NAISS username, typed on Connect
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
9. In a terminal, confirm `squeue -u $USER` has no `timed-dataserver` job.

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
   site 1. Crop to pixels 216-432 in x and y, then use **Grayscale** mode to
   compare each channel with Cell Health v2.0's
   [`1.generate-profiles/figures/example_images/greyscale_channel.png`](https://github.com/broadinstitute/cell-health/blob/cd91bd0daacef2b5ea25dcceb62482bb664d9de1/1.generate-profiles/figures/example_images/greyscale_channel.png).
5. Disconnect after viewing images and confirm it reaches a final state within
   a few seconds.
6. Record field-opening timings in this document.
7. Choose Cell Health and TIMED from the Connect page. Confirm that the Data
   page follows the active choice and that returning from the viewer, using the
   Data navigation link, and reloading restore the last Cell Health plate and
   well.

## A1 findings (complete)

- TIMED was preselected and unchanged from the TIMED-only release, including
  its original channel controls and absence of a Display switch.
- A549 `SQ00014610`, well `A01`, site 1 matched the inspection screenshot.
- The independent grayscale check on ES2 `SQ00014613`, well `A01`, site 1,
  cropped to pixels 216-432 in x and y, matched Cell Health's
  `greyscale_channel.png` visually. This confirms the plate, well, site,
  orientation, and source imagery. It did not catch the AGP/RNA identity swap:
  the viewer labels still came from the incorrect assumption that packed slot
  equalled raw Phenix channel. Cell Health supplied the declared
  Color/Grayscale capability used for the check.
- The comparison of A02 (MCL1) on A549 `SQ00014611`, ES2 `SQ00014614`, and
  HCC44 `SQ00014616`, plus the A01 EMPTY control on `SQ00014611`, loaded
  quickly, stayed synchronized, and returned to the Data page successfully.
- Disconnecting after viewing images took about 2 seconds and ended in the
  `disconnected` state.
- A field opened in about 4-5 seconds on the operator's work network and about
  2 seconds at home, comparable to loading a TIMED trajectory. This result is
  accepted; do not try the performance fallbacks.
- The revised dataset flow worked: the Connect-page choice switched between
  Cell Health and TIMED, the Data page followed the active choice, and viewer
  return, Data navigation, and reload restored the last Cell Health plate and
  well.
- All A1 checks are complete.

## Cell Health 0.4.1 channel-identity re-check

Status: passed on Arrhenius on 2026-10-28.

1. Reconnect with the corrected `0.4.1.dev0` data server and open Cell Health
   ES2 `SQ00014613`, well `A01`, site 1.
2. In **Grayscale**, select RNA. It should show bright nucleoli inside the
   nuclei, matching the Cell Health reference panels.
3. Select AGP. It should show bright Golgi patches beside the nuclei and along
   the cell edges, matching the Cell Health reference panels.
4. Return to the default Color view and confirm that AGP is shown in red.

This re-check establishes stain identity. The original A1 grayscale check
remains evidence for plate, well, site, orientation, and imagery only.

The first attempt reused the pre-correction Slurm job and therefore still
showed the old labels. After cancelling that job, a newly submitted job
reported data-server `0.4.1.dev0`. The channel list then appeared in packed
slot order (DNA, ER, AGP, RNA, Mito); ES2 AGP used 375–10,793 and showed the
expected Golgi and cell-edge signal, while RNA used 108–5,778 and showed the
expected nucleolar signal. The default Color view showed AGP in red.

## Phase 1 scale calibration

- TIMED uses its documented calibration of 0.3 µm/px.
- The Phenix `Index.idx.xml` files for all nine plates, `SQ00014610` through
  `SQ00014618`, report identical X and Y resolutions of
  `2.9898804047838085E-07` m at 20×.
- Cell Health therefore uses the full converted value
  `0.29898804047838085` µm/px. Metadata and calculations retain that precision;
  only display text may round it.
- The viewer derives a scale bar independently for each panel, including each
  comparison panel. A source without a valid pixel size shows no scale bar.

## Phase 1 release-readiness record

- The operator-dependent A1 gate completed on 2026-10-27 against the real
  Arrhenius data.
- The real-data preview, independent grayscale reference, cross-cell-line
  comparison, dataset switching and restoration, and terminal disconnect state
  all met the Phase 1 acceptance criteria recorded above. The grayscale check
  did not independently establish stain identity; the 0.4.1 re-check above is
  the replacement evidence for the corrected AGP/RNA mapping.
- The observed field-opening times are accepted. The performance fallbacks are
  intentionally not part of the release configuration.
- The grayscale switch remains a declared Cell Health capability and is shown
  only when every loaded source declares it. TIMED and sources without that
  declaration keep the original controls with no switch.
- The scale-bar inputs are complete: 0.3 µm/px for TIMED and the acquisition-
  verified `0.29898804047838085` µm/px for all nine Cell Health plates.
- This record establishes the Arrhenius acceptance evidence. The release branch
  prepares version `0.4.0`; the merge and `v0.4.0` tag remain operator steps.

## Cell Health QC review A2

Status: passed manually on Arrhenius on 2026-11-04. The final accepted
development build was `0.5.0.dev12`; the release commit sets version `0.5.0`.

The one writable review folder was created on Arrhenius before the check:

```bash
mkdir -p /nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/scratch/qc_review/annotations
```

The operator used a fresh Slurm job rather than a reused server and confirmed
the core-ready export loaded without a hash error. The complete section 4.10
checklist in `docs/plans/cell-health-qc-brief.md` passed:

- the compact QC page, readable metric selection, primary and secondary
  filters, live counts, plate filtering, plate shading and reason-first Sites
  and Wells tables behaved as intended;
- the review-list and session queues preserved their order, displayed the flag
  reason, exposed exact flagged sites where available and supported button and
  Left/Right Arrow navigation;
- the field view used fixed QC contrast windows and a compact review-only
  metric table with aligned **Site value** and **Relative to controls** values;
- field- and well-scoped annotations could be saved, reloaded and retracted in
  the configured annotations folder; and
- TIMED and datasets without a configured QC export remained unchanged.

The operator accepted page loading and metric-control responsiveness during
manual use. Separate timing figures were not supplied with the final A2
acceptance. Blinded mode and segmentation outlines were not part of A2 and
remain later work.

## Cell Health segmentation outlines

Status: passed manually on Arrhenius on 2026-11-07, with a fresh job running
the development build `0.6.0.dev0`; the release commit sets version `0.6.0`.

- On SQ00014610, well A01, site 1, the panel offered two models,
  `idr0080_cpsam_B90` (the default, marked "May still change") and
  `cpsam_stock_R2_top999`. The count line read 731 nuclei · 616 cells · 616
  cytoplasm, and the nucleus and cell outlines sat on the nuclei and cell
  borders in Color and Grayscale. The widths worked, and the default 2 px read
  best.
- SQ00014611, well A01, site 1, offered only `idr0080_cpsam_B90`.
- A comparison of SQ00014613 (ES2) and SQ00014616 (HCC44), well A01, site 1,
  drew nucleus outlines in both panels and read 288 nuclei · 255 cells · 255
  cytoplasm and 196 nuclei · 183 cells · 183 cytoplasm.
- In the QC review queue the panel appeared, and **Next** kept the outlines on.
- On the first open of 14610 A01 site 1, the listing took 541 ms and the
  default model's counts 161 ms (`masks:listing-ready`, `masks:counts-ready`).
  The `outline:ready` line was copied without its elapsed time, so outline
  loading was not timed.
- Disconnect cancelled the job, and `squeue` listed no remaining job.

The count line's layout in the narrow controls column, and one region where
the segmentation itself could be better, were noted for later work.

## TIMED segmentation outlines

Status: passed manually on Arrhenius on 2026-11-09, with a fresh job running
the development build `0.7.0.dev1` (installed as `0.7.0.dev1-1d3a73873ad0`);
the release keeps version `0.7.0`.

The TIMED collection reads the production segmentation under
`TIMED/seg/track_v0/production/seg-track-v0` as its `segmentation_root`. On
PB100140_P2.1:

- C05 site 1 offered `R4_cpsam_dS_corrected` alone, since the presentation
  export is configured only for the local copy. At time 1 it read 74 nuclei ·
  98 cells, with outlines on the cells.
- A comparison of C05 and C15, site 1, followed time in both panels. At time 1
  the listings took 109 and 222 ms, the C05 counts 78 ms, and the outlines
  184 ms (C05) and 261 to 263 ms (C15) (`masks:listing-ready`,
  `masks:counts-ready`, `outline:ready`).
- C15 site 1 at time 32 read 37 nuclei · 39 cells, and C03 site 1 read 45
  nuclei · 55 cells.
- On another plate (P1.2, well K15, site 1) the viewer behaved as intended,
  without masks: the runs cover P2.1 only.

The operator reported every step working as intended. The same steps on the
local copy, with the export's `masks_raw` as a second model, matched every
expected count.

## Troubleshooting

- Job pending indefinitely: verify the account and partition.
- No connection file: inspect `~/.timed/logs/<jobid>.out`.
- Tunnel failure: verify that the login node can reach the compute-node host.
- Data permission failure: verify read access from a compute node, not just login.
- uv/Python setup failure: check login-node internet access and available modules.
- Remote environment versus `uv.lock`: each install directory
  `~/.timed/server/<version>/` holds the constraints the environment was
  installed with, exported from `uv.lock`. Compare them with
  `diff <(uv pip freeze --python venv/bin/python | grep -v '^timed-dataserver') timed_dataserver-<version>-constraints.txt`
  run in that directory; no output means the two match.

### Viewer loading diagnostics

To diagnose a frame that remains loading while the data server shows no new
GET requests, append `&debug=viewer` to the viewer URL and reload it. Keep the
browser developer tools Console open with Info messages enabled. Diagnostic
lines begin with `[TIMED viewer` and cover:

- requested, committed, rendering, and displayed timepoints;
- Zarr fetch start, response status, byte count, and elapsed time;
- decoded-pixel cache hits, misses, shared pending loads, handoffs, and evictions;
- prefetch scheduling, completion, cancellation, or failure;
- Viv viewport-complete callbacks, including callbacks ignored as stale;
- a `frame:still-waiting` snapshot after ten seconds; and
- mask listings, counts and outline images (`masks:listing-ready`,
  `masks:counts-ready`, `outline:ready`) with their elapsed times.

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
