# Adding datasets

TIMED can expose native OME-Zarr, TIMED bare-array plate collections, and
packed TIFF indexes without copying or converting the source data. If the data
already matches one of those contracts, adding it is normally a profile change.
A code-level packed profile is needed only for metadata that cannot be inferred
safely.

## Configure a profile and collection

Start from `config/profiles.example.yaml`. A profile supplies the data-server
roots; each collection is a titled entry point shown as **Dataset** on Connect.

```yaml
profiles:
  - id: local-lab
    name: Local lab data
    kind: local
    data_roots:
      - /data/microscopy
    metadata_root: /data/timed-metadata  # optional TIMED-style plate metadata
    collections:
      - id: plates
        title: OME-Zarr plates
        description: Native plates and TIMED collections
        path: /data/microscopy/plates
        default: true
      - id: screen
        title: Packed screen
        path: /data/microscopy/screen/screen.index.parquet
```

Keep the `host`, `username`, and `slurm` fields from the example when
`kind: slurm` is used. Profile and collection ids must match
`[a-z0-9][a-z0-9-]*`; ids are unique, and at most one collection per profile
may have `default: true`. `description` and `default` are optional.

Every collection path must be equal to or below one of that profile's
`data_roots`. Slurm paths are normalized and checked lexically as POSIX paths
when the YAML is loaded. Local roots must be existing directories; local
collection paths must exist and are resolved, including symlinks, before the
containment check. Relative local paths are resolved from the process working
directory (normally the repository root), so operator profiles should prefer
absolute paths. The data server resolves roots again and enforces containment,
including symlink escapes, on every request. Use the narrowest practical roots
and restart the connector after editing its profile file. `metadata_root` is a
separate optional input, not another data root; a configured local metadata
root must also be an existing directory.

Collections affect navigation, not the data-server process. Connect remembers
the active collection separately for each profile. Selection order is an
explicit `collection` URL parameter, the last valid browser choice, the
configured default, then the first collection. The selector remains available
while connected, and Data shows only the active collection and its opened
plates. On Data, a valid `dataset` URL parameter reopens that linked plate
within the collection. A profile without `collections` retains the legacy
filesystem browser.

### Optional QC review

A packed collection offers the QC page when it names its QC export and the
folder that receives reviewers' annotations:

```yaml
      - id: screen
        title: Packed screen
        path: /data/microscopy/screen/screen.index.parquet
        qc_export: /review/screen/qc-export
        qc_annotations: /review/screen/qc-annotations
```

- Set both keys or neither. Slurm profiles need absolute POSIX paths, and local
  paths are resolved like collection paths. No path in the mapping may
  contain `=`.
- The collection path must be the packed index the export describes. The
  connector passes each pair to the data server as `--qc-export INDEX=FOLDER`
  and `--qc-annotations INDEX=FOLDER`. The browser learns only that QC is
  available, never the folders.
- Both folders are trusted configuration, not data roots, and neither may
  contain a data root. The annotations folder must also lie outside every data
  root and must not overlap the export. Neither folder is browsable, even when
  the export sits inside a data root.
- Annotations are saved only while their folder exists and is writable;
  otherwise review stays read-only. [`data/QC_EXPORT.md`](data/QC_EXPORT.md)
  describes the export itself.

### Segmentation masks

Reserved for Part C. Mask stores are declared in the code profile, relative to
the packed index, rather than as collection keys; see the
[mask-store contract](data/IDR0080.md#mask-store-contract-part-c).

## Supported data layouts

### Native OME-Zarr

Point the collection at a directory from which users can browse one or more
stores, rather than inside a store. Detection uses metadata, never a `.zarr`
suffix:

- OME-Zarr 0.4 / Zarr v2 groups use `.zgroup` and `.zattrs`.
- OME-Zarr 0.5 / Zarr v3 groups use `zarr.json` and the `ome` attributes.
- Plate and image groups are linkable; individual well groups are not.
- A bioformats2raw root whose native OME group is under `OME/` is also
  recognized.

Axes, multiscales, coordinate transforms, and OMERO channel metadata remain the
source of truth. TIMED does not rewrite the store.

### TIMED bare-array collections

A TIMED plate is a directory of Zarr v3 arrays, not an OME-Zarr group:

```text
PlateName.zarr/
  A01_s1.zarr/zarr.json
  A01_s2.zarr/zarr.json
  ...
```

Field names must match `<row><column>_s<site>.zarr`. Each field is a bare array
with a `timed` attribute containing at least `axes` and `channels_nm`; the
established contract is uniform `(t, c, y, x)` fields and also records `well`,
`site`, and `t_indices`. The server presents each field as a synthetic,
read-only OME-Zarr 0.5 group with the unchanged array below `s0`.

Optional TIMED plate metadata comes from `metadata_root`. The recommended
layout is `acquisition/<plate-stem>.json` plus
`csv/<plate-stem>.csv`; no matching files simply means metadata is unavailable.

### Packed TIFF indexes

For packed data, the collection path is the `*.index.parquet` file. It is
recognized from its schema and adjacent tar files, not its basename alone. The
required columns are `relpath`, `experiment`, `plate`, `well`, `site`,
`channel`, `offset`, and `size`. `relpath`, `experiment`, and `well` are string
fields; `plate`, `site`, `channel`, `offset`, and `size` are integer fields.
Either:

- a string `shard` column names tar files beside the index, or
- `<index-stem>.tar` exists beside an index without a usable shard mapping.

Required values must be non-null, `(experiment, plate, well, site, channel)`
keys must be unique, and path components must be safe. Each offset and size
addresses a regular TIFF member in the adjacent tar. The UI exposes the virtual
hierarchy `<index>/<experiment>/<plate>`; linked plates and field OME-Zarr
metadata are virtual. The Parquet file, tar shards, and TIFF payloads stay
unchanged.

An unrecognized packed dataset gets a conservative generic profile. TIMED
probes the first indexed USTAR member as a single-page, uncompressed 2-D TIFF,
then infers image shape and dtype, member prefix, experiments, plate geometry,
sites, and a consistent channel set. Generic channels use their numeric ids,
are all active, have no declared colors or windows, and use automatic contrast.
Generic datasets have no well metadata, grayscale declaration, or pixel size.

Use a pinned `PackedDatasetProfile` in
`services/dataserver/src/timed_dataserver/packed_profiles.py` when the dataset
needs verified semantics that cannot be inferred: human channel labels,
colors, active channels, fixed display windows, experiment labels, plate
naming, a relative metadata CSV, calibrated pixel size, or grayscale support.
A pin is selected only by the exact index basename **and** row count; add it to
`_KNOWN_PROFILES` and test both the match and a near miss. `metadata_path` is
relative to the index directory, may not escape it, and a missing file makes
metadata unavailable without blocking image browsing.

## Display and calibration defaults

| Source | Channel/display defaults | Grayscale | Pixel size |
| --- | --- | --- | --- |
| Native OME-Zarr | OMERO labels, colors, `active`, and windows; missing values use viewer fallbacks and computed contrast | Not declared by the current native adapter | Read from an axis unit and positive multiscale transform |
| TIMED collection | Wavelength labels from `timed.channels_nm`, established RGB colors and windows | Not declared | 0.3 micrometres per pixel |
| Generic packed | Numeric labels, all channels active, automatic contrast | Not declared | None |
| Pinned packed | Values from `PackedDatasetProfile` | `grayscale_available` | `pixel_size_um` |

The Color/Grayscale switch appears only when every loaded source explicitly
declares grayscale support. Each viewer panel derives its own scale bar from
its image metadata; a source without a valid unit and positive scale gets no
scale bar. Never guess a calibration. If a new TIMED collection does not use
the established 0.3 micrometre calibration, the adapter and tests must be
extended rather than relabelling the data.

## Fixtures, tests, and generated API files

Keep fixtures deterministic and synthetic. Extend `scripts/make_test_data.py`
for a new layout or packed pin, and add focused coverage beside the relevant
implementation:

- `services/connector/tests/test_collections.py` for profile validation;
- `services/dataserver/tests/test_dataserver.py` and `test_packed*.py` for
  detection, containment, summaries, metadata, and facade bytes;
- `apps/web/tests/e2e/collection-flow.spec.ts` (and the helpers in
  `scripts/run_e2e.py`) for a user-visible collection workflow.

From the repository root, run:

```bash
make test-data
make test
make test-e2e
make lint
```

On Windows without GNU Make, generate fixtures with
`uv run python scripts/make_test_data.py`; the direct checks are:

```bash
uv run pytest
pnpm -C apps/web check
pnpm -C apps/web build
```

If a Pydantic response model or route changes, run `make gen-api` and commit
the regenerated connector/data-server OpenAPI JSON documents and TypeScript
schemas under `apps/web/src/lib/api`. A profile-only addition or internal
packed pin does not require regeneration when the API schema is unchanged.

## Read-only and security rules

- Never create, modify, lock, convert, or delete anything inside a source
  dataset. Open indexes, metadata, tar shards, and TIFFs read-only.
- Linking writes only the server registry in its state directory; removing a
  link removes that record, never image data.
- Do not weaken `RootGuard`, parent-segment rejection, symlink containment, or
  packed member bounds/header checks. Add traversal and escaping-symlink tests
  for new access paths.
- Do not place passwords, verification codes, key passphrases, or data-server
  tokens in profiles, logs, URLs, fixtures, or screenshots.
- Do not commit real image data. Commit only small synthetic fixtures or the
  approved metadata inventory/documentation described in `CLAUDE.md`.

To exercise the checked-in configuration, run from the repository root:

```bash
uv run python scripts/dev.py --profiles config/profiles.example.yaml
```

Then choose the profile and Dataset on Connect. `make dev` runs the same
configuration.
