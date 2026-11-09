# Berzelius operator notes

Berzelius is TIMED's second Slurm compute profile. It provides temporary access
to plate `PB100140_P2.1` while Arrhenius is unavailable and uses the same remote
data-server package, TIMED collection facade, and per-plate metadata contract.

## Profile and job

Choose **Berzelius (NSC)** and then **TIMED** on Connect. The profile connects to
`berzelius.nsc.liu.se` as `x_nildu`, using the normal password followed by the
six-digit verification code. It exposes only the TIMED collection; Cell Health
and QC are not configured and must not appear.

The data server reads:

- images from `/proj/berzelius-2026-62/users/x_nildu/TIMED/zarr`;
- metadata from `/proj/berzelius-2026-62/users/x_nildu/TIMED/metadata`.

Slurm uses account `berzelius-2026-299`, partition `berzelius-cpu`, two CPU
cores, and an eight-hour time limit. The job deliberately requests neither a
GPU nor `--mem`: the data server is CPU-only, NSC cancels jobs that leave GPUs
idle, and memory on `berzelius-cpu` follows the allocated cores.

## Host-key verification

On the first connection, compare the fingerprint shown by TIMED with NSC's
[published Berzelius fingerprints](https://www.nsc.liu.se/support/systems/berzelius-getting-started/)
before accepting it. For the Ampere login nodes NSC currently publishes:

- RSA: `SHA256:T+pB0hRJv9RhlE8KlW9bCU5Zk22B9fHW2X65xjLhtLk`
- ECDSA: `SHA256:ZjS69kEb4xeK2a97awDqz0cu0zUwTRyaqL5w59tmty0`
- ED25519: `SHA256:iZLFlrmwW7YbMXsR7TQxlrDfHUbQnFg5tSfB4GNCtGQ`

Reject an unlisted fingerprint and check the NSC page again. If a later
connection reports that the high-availability host key changed, use
`berzelius1.nsc.liu.se` instead; do not disable host-key checking or edit the
user's OpenSSH configuration.

## Layout differences and decisions

The recreated plate is compatible with the Arrhenius reference after adding
explicit axes to every field. The integration follows these decisions:

| Difference | Decision |
| --- | --- |
| The plate root is a Zarr v3 group with `timed_acquisition`, rather than a plain collection directory. | Accept the non-OME group and continue detecting the named TIMED field arrays below it. |
| Inner shard chunks are whole planes `(1, 1, 2500, 2500)`, rather than Arrhenius's `(1, 1, 625, 625)` tiles. | Read each field's chunk and codec layout from its own `zarr.json`; never assume a spatial chunk size. |
| Recreated fields initially omitted their axes. | The operator added `attributes.timed.axes = ["t", "c", "y", "x"]` to all 392 fields, with a complete backup made first. The server requires these axes and does not infer them. |
| Repaired fields may carry `attributes.timed.site_position_fix`. | Validate it and report it in the field summary. Since 0.5.3 the viewer shows no note for it. Field names and pixels are not remapped. |
| Source metadata was one combined plate-map Parquet plus the full acquisition config. | Generate `csv/PB100140_P2.1.csv` and `acquisition/PB100140_P2.1.json`. The runtime keeps the bounded per-plate standard-library loader and has no Parquet path. |
| The combined table spells the cell line `U2OS_Cleo3`, while the acquisition config uses `U2OS_CLEO3`. | Preserve the source spelling in the generated CSV; do not normalize display text. |

Field validation is strict but geometry-independent. A TIMED field must be a
Zarr v3 `uint16` array with four positive `(t, c, y, x)` dimensions, matching
channel and time metadata, one whole-array shard, and the supported indexed
Blosc-Zstd codec family. Both tiled and whole-plane inner chunks are valid.

The plane-level Parquet remains converter provenance and is not a runtime input.
It records the original C03 acquisition positions before the timepoint-zero site
repair, so it must not be used to remap the repaired arrays.

## Data preparation result

The Berzelius update completed successfully:

- 392 fields were found and all 392 gained explicit axes, with none skipped;
- the backup tar contains all 392 original `zarr.json` files and is outside the
  data folder;
- the per-plate metadata contains 196 wells and joins all 392 field names; and
- the source cell-line spelling remains `U2OS_Cleo3`.

Moving the backup between the project and home filesystems produced a harmless
NFS access-control-attribute warning; the tar entry count and copied hashes were
verified afterward.

## Local downtime copy

During the planned Berzelius downtime, an exact read-only working copy is
available on the operator workstation:

- images: `D:/TIMED/zarr/PB100140_P2.1.zarr`;
- per-plate metadata: `D:/TIMED/metadata`;
- segmentation masks for C05, C15, F13 and G07 site 1: `D:/TIMED/corrected`,
  the profile's read-only `masks_root` (layout in
  [CONVERSION.md](data/CONVERSION.md));
- the production segmentation for every field: `D:/TIMED/seg`, the profile's
  read-only `segmentation_root`, a copy of the Arrhenius runs.

Choose **Local TIMED (external drive)** and **TIMED** to use it. The
`local-timed` profile is collection-aware and starts the normal loopback data
server; it does not emulate SSH or Slurm. Windows drive-letter paths are passed
to the child process as individual arguments and are not split at the colon.
The server's registry, connection file and test output remain under TIMED's
local state or the repository test runtime. Nothing is written to `D:/TIMED`.

The opt-in real-copy checks are:

```powershell
$env:TIMED_TEST_REAL_TIMED_COPY = "1"
uv run pytest services/dataserver/tests/test_real_timed_copy.py -q
Remove-Item Env:TIMED_TEST_REAL_TIMED_COPY
$env:TIMED_TEST_REAL_TIMED_MASKS = "1"
uv run pytest services/dataserver/tests/test_real_timed_masks.py -q
Remove-Item Env:TIMED_TEST_REAL_TIMED_MASKS
uv run python scripts/run_e2e.py --real-timed-copy D:/TIMED
```

The server check opens the plate through the HTTP facade, loads all 196 metadata
rows, decodes one real 2500 by 2500 plane from C03 site 1, verifies its repair
metadata and confirms the source metadata files were unchanged. The browser
check follows Connect to Data to View, renders that plane and confirms that the
viewer shows no repair note and, when the copy has `seg/`, that C03 site 1
offers the production model alone. The masks check compares every frame's
nucleus and cell counts on the four masked fields with the export's
`counts.csv` tables: the export's own masks, and the production run's against
the tables' nuclei and TiLu-corrected cells, which differ only at C15 time
index 31. It also compares outlines with zarr's own decoding and confirms both
mask trees are unchanged.

## Manual validation

The local downtime-copy check passed on 2026-11-05. The operator confirmed that
the profile, plate and well metadata, C03 site-position note, time navigation
and disconnect behavior all worked as before. Image viewing was noticeably
faster from the external drive because image bytes no longer crossed the
cluster connection. This is accepted validation for the Berzelius-layout
server and viewer behavior in version 0.5.1.

Pending operator check on Berzelius:

1. Choose **Berzelius (NSC)** and **TIMED**.
2. Open `PB100140_P2.1`, select a field, and move through time.
3. Check that the plate and well metadata are present.
4. Open C03 site 1 and confirm that it renders without a repair note.
5. Disconnect and run
   `sacct -j <job-id> --format=JobID,Partition,AllocCPUS,State,Elapsed`.
6. Confirm the job used `berzelius-cpu`, allocated two CPUs, and ended after
   disconnecting.
