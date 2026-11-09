# TIMED MOAC conversion record

| | |
|---|---|
| **Source** | `timed:/files/TIMED_MOAC_LGCP_EXP01/` (Uppsala SFTP) |
| **Converted** | 2026-09-08 to 2026-09-09 |
| **Tooling** | `convert_plate.py` in `cellprompter-cpu.sif` |

## Summary

- 8 plates, 4,032 positions (well × site), 580,608 planes
- 5.71 TiB of LZW TIFFs (697,682 files) converted to 3.6 TiB of Zarr (~4,000 files)
- Pixels are unmodified; no correction was applied

## Data format

Each position is one Zarr v3 array:

| Property | Value |
|---|---|
| Shape | (48, 3, 2500, 2500) |
| Axes | t, c, y, x |
| Data type | uint16 |
| Channels (index order) | 405, 505, 625 nm |
| Pixel size | 0.3 µm/px |
| Chunks | (1, 1, 625, 625) |
| Shard | whole array (one shard per position) |
| Compression | Blosc zstd, level 5, byte shuffle |

### Layout

```
zarr/
├── CONVERSION.md
└── <barcode>_<plate>.zarr/          plain folder, not a Zarr group
    ├── acquisition_config.json
    ├── plane_metadata.parquet       per-plane OME-XML fields
    └── <well>_s<site>.zarr/         bare Zarr v3 array, e.g. B02_s1.zarr
```

The arrays are not OME-Zarr: there is no `multiscales` metadata and no
pyramid. The arrays do not store `dimension_names`, so the axis order, channel
wavelengths and pixel size above are documented here only.

## Plates

| Plate | Barcode | Positions | Planes | Zarr size | Autofocus N/A |
|---|---|---:|---:|---:|---:|
| P1.1 | PB100087 | 616 | 88,704 | 562 GiB | 9 |
| P1.2 | PB100137 | 616 | 88,704 | 555 GiB | 105 |
| P1.3 | PB100138 | 616 | 88,704 | 556 GiB | 6 |
| P1.4 | PB100139 | 616 | 88,704 | 557 GiB | 18 |
| P2.1 | PB100140 | 392 | 56,448 | 355 GiB | 0 |
| P2.2 | PB100141 | 392 | 56,448 | 355 GiB | 0 |
| P2.3 | PB100142 | 392 | 56,448 | 355 GiB | 0 |
| P2.4 | PB100143 | 392 | 56,448 | 356 GiB | 0 |

## Verification

1. Before conversion, the file count was checked for every timepoint of every
   plate (uniform: 1,848 per timepoint for P1, 1,176 for P2).
2. The converter fails on any missing plane.
3. After conversion, 5 random planes per plate were verified bit-exact against
   the source TIFFs.
4. Source TIFFs were deleted only after all checks passed.

## Acquisition

P1.1 differs from the other plates. P1.2 through P2.4 share identical settings.

| Setting | P1.1 | P1.2 – P2.4 |
|---|---|---|
| Plate type | Revvity ViewPlate 384 | PhenoPlate 384 |
| Bottom offset | 2.9 mm | 0.328 mm |
| Site spacing | 0.7 mm | 0.9 mm |
| Exposure (405/505/625 nm) | 20 / 290 / 350 ms | 20 / 350 / 250 ms |
| Gain (405/505/625 nm) | 15 / 20 / 15 dB | 13 / 20 / 15 dB |
| Illumination | 100 % on all channels | 100 % on all channels |
| Sweep interval | 59 min 59 s | ~60 min 02 s |

Per-plane OME-XML is preserved in each plate's `plane_metadata.parquet`:
exposure, gain, autofocus status, acquisition timestamp, stage XYZ and pixel
size.

## Known issue: autofocus N/A

- Occurs only on P1 plates (138 events, June 5 – July 3); never on P2
  (July 10 – 17).
- Always affects a whole site-timepoint across all three channels.
- `stage_z` is within the normal range for these events.
- Raised with the lab.

## Segmentation masks (TIMED addition, 2026-11)

Not part of the conversion. A presentation export (2026-11-06, "not a pipeline
output") holds masks for four P2.1 fields: C05, C15, F13 and G07, site 1. It
mirrors the image layout in a tree beside it,
`corrected/PB100140_P2.1.zarr/<well>_s<site>.zarr/`, one Zarr v3 group per
field; the export's own `README.md` records how it was made. TIMED reads
`masks_raw` (cells) and `nuclei`. The group also holds `raw` (a copy of the
field), `corrected_505` and `masks_corrected` (cells on TiLu-corrected 505 nm,
not used).

| Property | `masks_raw` and `nuclei` |
|---|---|
| Shape, axes | (48, 2500, 2500), t, y, x (`axes` attribute) |
| Data type | uint16, fill value 0 |
| Chunks | (1, 1250, 1250), four per plane |
| Shard | whole array, one file `<array>/0.0.0` (`v2` chunk keys, `.` separator); index at the end, CRC32C |
| Compression | Blosc zstd, level 5, byte shuffle |
| Time | `t_indices` 0 to 47, equal to the field's |

- **Kinds:** `masks_raw` holds whole-cell labels from Cellpose-SAM (`cpsam`,
  cellpose 4.2.1.1, diameter 90, run `R5_cells_cpsam_dS`, digest
  `2a760ccd1f0e`) on raw 505 and 625 nm. `nuclei` holds classical foreground
  objects on raw 625 nm (radius-3 opening, 80 px floor; digest `97117440304a`).
  Per field, the median share of a frame's nuclei that lie inside a cell is
  94 to 100%.
- **Labels:** 0 is background; each frame's objects are numbered 1 to N without
  gaps, so the largest label is the count (37 to 112 cells and 36 to 92 nuclei
  per frame, equal to the export's `counts.csv`). Labels are not tracked: every
  frame was segmented on its own, so an id names a different object in the next
  frame.
- **Alignment:** plane size, orientation and timepoints match the field as
  stored, with no transpose or flip. Mask frame i belongs to image frame i: the
  export's `raw` equals the field, and each frame's nuclei fit that frame's
  cells better than its neighbours'.

### Production segmentation

The production run covers all 392 PB100140_P2.1 fields. It lies on Arrhenius
under `TIMED/seg/track_v0/production/seg-track-v0`, copied to `D:/TIMED/seg`, as
`<plate>/sites/<well>_s<site>/stage1/<run>/labels/<plate>/<well>_s<site>.zarr`,
one bare array per run and field. TIMED reads two runs:

- `R4_cpsam_dS_corrected`: Cellpose-SAM cells (diameter 90) on TiLu-corrected
  505 nm (`tilu_v1_provisional:5feabc75e262`) and raw 625 nm. Its `run.json`
  names the nucleus run.
- `R4_cpsam_dS_corrected_nuclei`: classical nuclei (`R2_classical625`) on raw
  625 nm.

The arrays are stored like the export's: shape, chunks, codecs, `v2` keys and
`t_indices` 0 to 47 are the same. They have no `axes` attribute, but store
their `run_id`, `plate`, `well` and `site`, plus per-plane input QC `records`
(about 450 KB of metadata). Every field's `production_verification.json`
passed with 48 planes and none refused, from one segmentation commit
(`44276ae`). Labels number each frame 1 to N without gaps.

On the four exported fields, the nuclei equal the export's `nuclei`. The cells
equal the export's `masks_corrected` in every frame except C15 time index 31,
where the run has 39 cells against the export's 40: one export cell is part of
a neighbour.
