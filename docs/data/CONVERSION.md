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