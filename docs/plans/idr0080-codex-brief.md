# idr0080 in the TIMED viewer

The goal is to **view idr0080** and **review its segmentation and QC**:

- Phase 1 makes idr0080 browsable and viewable.
- Phase 2 adds CellPrompter's segmentation outlines, per-field QC measurements, and a QC screen for finding fields that need manual verification.

The QC review is **blind**: the website never reads or shows CellPrompter's own exclusion decisions, so the operator judges fields without knowing what was removed.

The viewer is **isolated from CellPrompter**: it never uses CellPrompter's code. Everything it relies on about CellPrompter's outputs is written down in Appendix D and checked against the real data.

Commit messages should mirror existing commits that already exist within the repository.

## 0. Working agreement

You are Codex, developing in the `timed_website` repository (the TIMED imaging platform) on the operator's workstation. This brief is your whole task. Work through the milestones in section 7 in order.

- **Read first:**
  - This brief.
- **Inputs.** The operator places these before you start.
  - `docs/plans/idr0080-codex-brief.md`: this brief. You commit it in B0.
  - **Data inputs.** These are local only and must be gitignored before your first commit.
    - `docs/data/previews/`: PNG previews of plate 14610, well A01, site 1, from the Arrhenius inspection.
    - `docs/data/packed-display-survey.json`: image statistics. Appendix B is derived from it.
    - `docs/data/idr0080-extras.json`: a blind copy of the inventory of mask stores and QC tables, without CellPrompter's exclusion records, run ledger, profiles or features. Appendix A.2 is derived from it.
    - `docs/data/idr0080-mask-contract.json`: the check of Appendix D against all ten real mask stores (2026-10-23). It passed; Appendix D.4 summarises it.
- **Never** connect to Arrhenius or any other remote host, submit Slurm jobs, or touch real data. Steps marked **Operator** are done by the operator: stop there, say exactly what to run or check, and wait.
- **Stop and ask** when the repository or the inputs contradict this brief (Appendix D included), or when you need a dependency not named here.
- **Each milestone** ends with its own tests and all existing suites green, a commit, and a push. Record design decisions in `docs/decisions.md`.
- **If a tool is unavailable** (network access for `uv` or `pnpm`, Playwright browsers, Docker or WSL for the test cluster), do everything else, then list the exact commands the operator must run.

## 1. Outcome

**Phase 1 (`feature/idr0080`, release 0.4.0): browse and view idr0080.**

- **Dataset selector.** The Data page has a "Dataset" selector offering TIMED (the default), idr0080, and "Files" (today's browser).
- **Browsing.** idr0080 is browsed by cell line (A549, ES2, HCC44), then plate, then well, then site (nine per well). Gene, guide and well type come from the metadata CSV.
- **Viewing.** All five stains are available, with a DNA/ER/AGP composite by default and contrast fixed per cell line.
- **Comparing.** Compare the same well on other plates, the same gene elsewhere, or the controls on the same plate, up to four fields side by side.
- **TIMED unchanged.** TIMED renders and behaves exactly as today.

**Phase 2 (`feature/idr0080-masks`, release 0.5.0): segmentation and QC review.**

- **Outlines.** Nuclei, cell and cytoplasm outlines from CellPrompter's mask stores, over any field. `idr0080_cpsam_B90` is the default model.
- **QC information.** The viewer shows the open field's segmentation QC measurements: object counts, morphology, intensity, focus and saturation. The Data page's site picker shows each site's cell count.
- **QC screen.** A new page, laid out like the Data page.
  - Pick a plate, then add filters on QC metrics, each with a range slider over the metric's distribution.
  - The wells and sites that fall within the thresholds are highlighted on the plate grid and listed.
  - A review queue steps through them in the viewer with outlines on, so the operator knows exactly where to look for manual verification.
- **Scale bar.** Added once the operator supplies the pixel size.

**Not in this brief:**

- plate heatmaps;
- per-object feature lookup;
- CellPrompter's exclusion records, and the rest of the `profiles/` and `features/` trees (section 2);
- RxRx3-core;
- recording review decisions.

## 2. Ground rules

- **TIMED stays identical**: same pixels, contrast, controls and workflow. Regression tests committed first (B0) prove it on every later commit. The QC screen shows a short "no QC data" message for TIMED plates.
- **Old profiles see the old page.** Profiles without `collections` get exactly today's Data page, so the existing suites run unchanged.
- **Blind QC.** The website must not read, store or show CellPrompter's exclusion decisions, or anything derived from them. These are the `*_mad_exclusions.json` records, everything else under `profiles/`, and `run_ledger.jsonl`.
  - The only QC source is the per-field measurement table in each mask store (section 5.2).
  - Your inputs are already blind copies. If any section about exclusions, the run ledger or `profiles/` appears anyway, ignore it.
  - A test enforces this (section 8.5).
- **Isolated from CellPrompter.** Never clone, import, vendor or read CellPrompter's code, and never add it as a dependency.
  - What the viewer needs about CellPrompter's outputs is in Appendix D, confirmed on the real data by `idr0080-mask-contract.json`.
  - If the data disagrees with Appendix D, stop and ask.
- **Read-only, including CellPrompter's stores.**
  - All data stays read-only. Nothing is written, locked or created in dataset folders, and no converted copies are made.
  - Never create, modify, lock (`flock`) or delete CellPrompter's `.lease`, write-ahead-log, tar, index or QC files. Reading them is fine.
  - Open every data file read-only.
- **Build for idr0080 without closing doors.** Key plates by `(experiment, plate)` and keep packed-index detection generic, but implement and test only what idr0080 needs.
- **main stays releasable.** Each phase branches from the latest `main` and merges only after the operator's Arrhenius check.
- **Dependencies.**
  - Phase 1 adds `pyarrow` and `tifffile`.
  - Phase 2 adds none: PNG encoding uses the standard library's `zlib`, and histograms use numpy.
  - Pin versions exactly, as the repository does.
- **Conventions**: typed models, regenerated API clients after API changes (`make gen-api` or its direct equivalent), and tests in every milestone.

## 3. Branches, versions and workflow (milestone B0)

1. On `main`, check that the working tree is clean apart from the operator's untracked inputs in `docs/plans/` and `docs/data/`. Run `uv sync` and `pnpm install --frozen-lockfile`, and confirm the full Python and Playwright suites are green.
2. Tag the TIMED-only state: `git tag -a v0.3.1-timed -m "TIMED-only viewer before idr0080 work"`, then push the tag.
3. Run `git switch -c feature/idr0080`, push it, and open a draft pull request to `main`.
4. Make the first commit on the branch:
   - `.gitignore` entries for the four data inputs: `docs/data/inspection-2026-10-22/`, `docs/data/packed-display-survey.json`, `docs/data/idr0080-extras.json` and `docs/data/idr0080-mask-contract.json`. Check with `git status` that none of them is staged.
   - `AGENTS.md` at the repository root. It should say that agents follow `CLAUDE.md`, the single source of repository conventions.
   - This brief.
5. Use the repository profile for branch development.
   - `config/profiles.example.yaml` is the operator's active Arrhenius profile. Add the idr0080 data root and both collections from section 4.1 there in C1.
   - Run the branch with `uv run python scripts/dev.py --profiles config/profiles.example.yaml`.
   - Main keeps its own copy of the file, so its profile and application remain unchanged until the branch is merged.
   - Default the Makefile's `PROFILES` variable to `config/profiles.example.yaml`.
6. Make Arrhenius test runs always use the code just built.
   - Set the data-server version to `0.4.0.dev0`: in its `pyproject.toml` and `__init__`, and in the root and connector pins.
   - Change `prepare_remote_server` so that for `.dev` versions, the remote install directory is keyed by the version plus the first 12 hex digits of the wheel's SHA-256. Each test then runs the build just made, and a job still running an older build is never overwritten.
   - Release versions keep today's install-once behaviour.
7. Commit the TIMED regression tests (section 8.1) before any other change. They pass on unchanged code.
8. Switching back to `main` stays safe.
   - Data server 0.3.1 remains installed on Arrhenius beside the dev builds.
   - Both share the remote registry `datasets.json`, whose format must not change.
   - idr0080 plates opened on a branch appear in main's list but do not open there.
9. **Versions.**

   | Phase | Branch | Development version | Release |
   | --- | --- | --- | --- |
   | 1 | `feature/idr0080` | `0.4.0.devN` | `0.4.0` (R1) |
   | 2 | `feature/idr0080-masks` | `0.5.0.devN` | `0.5.0` (R2) |

## 4. Phase 1 design

### 4.1 Collections and the dataset selector

A collection is a titled entry point into data the server can already open. Collections are defined in the connector profile:

```yaml
  - id: arrhenius
    # … connection and Slurm settings unchanged …
    data_roots:
      - /nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/TIMED/zarr
      - /nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/biomorph/idr0080
    metadata_root: /nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/TIMED/metadata
    collections:
      - id: timed
        title: TIMED
        description: Live-cell time-lapse · 8 plates · 48 h, hourly · 3 channels
        path: /nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/TIMED/zarr
        default: true          # move to idr0080 to open that dataset first
      - id: idr0080
        title: idr0080 · Cell Health
        description: CRISPR Cell Painting · A549, ES2, HCC44 · 9 plates · 5 channels
        path: /nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/biomorph/idr0080/idr0080-raw.index.parquet
```

- **Connector model.** Add `CollectionConfig {id (slug), title, description?, path, default = false}` and `Profile.collections: list[CollectionConfig] = []`.
- **Validation.**
  - Ids are unique and at most one collection is the default.
  - Each path lies inside a data root: checked lexically for Slurm profiles, and resolved and required to exist for local profiles.
  - The data server still enforces containment on every request.
- **No new data-server API.** The Data page (and, in Phase 2, the QC page) reads collections from `/connector/profiles` and the active `profile_id` from `/connector/session`.
- **Selector.**
  - A labelled "Dataset" control lists the collections, then "Files".
  - The initial choice is the `?collection=` parameter when valid, else the default, else the first collection.
  - Changing it updates the URL with `history.replaceState`, shows the description, and resets the browser.
  - The comparison tray persists across datasets.
- **Plates panel.** This is the existing browser, scoped to the collection path.
  - Breadcrumbs start at the collection title.
  - Plain files are hidden, and names drop `.zarr`.
  - Entries show the server's `detail` line, and listings over 30 entries get a filter box.
  - Plates and images have one button, **Open**, which links and opens in one step; the registry already returns the existing record for a known path.
- **Opened plates.** The registry panel shows the current dataset's plates (matched by path prefix), titled "Opened plates", with "Remove".
- **Files.** Exactly today's browser, registry panel and wording.
- **Links.** Viewer links carry `collection`. "Back to data" returns to `/data?collection=<id>&dataset=<id>`, which reopens that plate.
- **Example profiles.**
  - `config/profiles.example.yaml` gets the `arrhenius` entries above.
  - `local` and `test-cluster` stay unchanged.
  - New `local-datasets` and `test-cluster-datasets` profiles use the TIMED fixtures and the idr0080-like fixture as their collections.
  - `scripts/run_e2e.py` writes all four.

### 4.2 How idr0080 maps onto the viewer

| idr0080 | In the viewer |
| --- | --- |
| Index experiment `idr0080-A549`, `-ES2`, `-HCC44` | Groups in the plates panel, one per cell line ("3 plates") |
| Plate 14610–14618 | A plate named `SQ000<plate>`, linked as "idr0080 · A549 · SQ00014610"; 384 wells, A–P × 1–24 |
| Well and site 1–9 | Field `<well>_s<site>.zarr`, so the site picker and tray work unchanged |
| Channels 1–5 | DNA, ER, RNA, AGP, Mito (Appendix B.1) |
| `metadata_idr0080.csv` row | Well metadata (section 4.4) |

### 4.3 Packed adapter (data server)

**Dependencies.** Pin `pyarrow` and `tifffile` exactly; pyarrow 26.0.0 and tifffile 2026.10.15 installed cleanly on Arrhenius's Python 3.12.15. Blosc comes from `numcodecs`, which `zarr` already depends on. Add `Capabilities.packed_datasets`.

**Detection (new module `packed.py`).** A file named `*.index.parquet` is a packed image index when both hold:

1. Its footer schema has `relpath, experiment, plate, well, site, channel, offset, size`, with integer `plate, site, channel, offset, size`.
2. Either the distinct values of its `shard` column all name files beside it, or `<stem>.tar` exists beside it.

Cache the result by path, modification time and size. Mask indexes (they have `kind` and no `channel`) and `index_fragments/*.index.parquet` (their shards are not beside them) are not detected.

**Virtual paths.**

- Paths take the form `<real index path>`, `…/<experiment>` or `…/<experiment>/<plate>`.
- Parse from the right. Resolve the component ending in `.index.parquet` with `RootGuard` (symlink and root rules unchanged) and require that it is detected.
- The remaining components must exist in the catalogue; otherwise return 403 or 404 as today.
- Branch on this before `guard.resolve()` in `linked()`, `fs/list` and link. Never run the "inside of a Zarr store" walk on virtual paths.
- `DatasetRegistry.link()` canonicalises with `path.resolve(strict=True)`, which fails for virtual paths. Pass it the key `<resolved index path>/<experiment>/<plate>` instead, keeping the `datasets.json` format unchanged.
- Listing `detail` lines:
  - index: "3 cell lines · 9 plates";
  - experiment: "3 plates";
  - plate: "384 wells · 9 sites · 5 channels".

**Catalogue.**

- Load the index lazily in a worker thread, the first time it is listed or opened.
- Dictionary-encode `experiment`, `well` and `shard`, and group with pyarrow or numpy.
- Build two structures: per `(experiment, plate)`, the wells, sites and channels; and a field lookup from `(experiment, plate, well, site)` to the per-channel `(tar, offset, size)`.
- Initialise once under a lock. Reload when the index's modification time or size changes.
- The survey loaded and grouped the full index in 1.3 s.

**Reading fields.**

- **Unit of work.** The first chunk request for a field loads all five channels once (single-flight per field) into a decoded-plane cache bounded by bytes (default 1 GiB, about 23 fields; settable with `serve --packed-cache-mb`).
- **How to read.** A field's five members are adjacent in their shard, so read them with one `os.pread` (about 47 MB) and slice. Keep a fallback of concurrent reads for members that are not adjacent. Bound concurrent tar I/O with a semaphore of four, over cached read-only file descriptors.
- **Bounds.** Check that `offset >= 512` and that `offset + size` fits within the tar.
- **Header verification.** Verify the 512-byte ustar header before the offset (name is `idr0080/raw/` plus `relpath`, size matches) for the first member read from each tar, and again whenever a decode fails. On a mismatch, log loudly and refuse that tar until restart.
- **Decoding.** Decode with tifffile from memory. The result must be 2-D, 2160 × 2160 uint16; a violation fails only that request.
- **Index sanity.** At load, refuse an index with null keys or duplicate `(experiment, plate, well, site, channel)` rows, with a clear error (Appendix D.1).
- **Exact reads.** Read exactly `size` bytes, looping over short `pread` results. Fewer bytes than that is an error for that request.

**Facade.**

- **Group metadata.** `<field>.zarr/zarr.json` has `ome.version` "0.5" and one multiscale:
  - axes `c` (channel), `y` and `x` (space; units only after S1);
  - dataset `s0` with scale `[1, 1, 1]`;
  - `omero.channels` from Appendix B: `label`, `color`, `active`, and `window {min, max, start, end}` for the field's cell line.
- **Array metadata.** `s0/zarr.json`:

```json
{"zarr_format": 3, "node_type": "array", "shape": [5, 2160, 2160], "data_type": "uint16",
 "chunk_grid": {"name": "regular", "configuration": {"chunk_shape": [1, 1080, 1080]}},
 "chunk_key_encoding": {"name": "default", "configuration": {"separator": "/"}},
 "fill_value": 0,
 "codecs": [{"name": "bytes", "configuration": {"endian": "little"}},
            {"name": "blosc", "configuration": {"typesize": 2, "cname": "zstd", "clevel": 3,
                                                "shuffle": "shuffle", "blocksize": 0}}],
 "dimension_names": ["c", "y", "x"], "attributes": {}}
```

- **Chunk keys.** `s0/c/<c>/<i>/<j>` maps channel index *c* to channel *c + 1*. Out-of-range indices and any other key return 404.
- **Chunk responses.**
  - Slice the chunk from the cached plane, encode it with `numcodecs.Blosc`, and return it through `_memory_response`.
  - Give `_memory_response` an optional precomputed ETag built from the tar's modification time, offset, size, chunk index and codec.
  - Set `X-TIMED-Cache: hit|miss`.
- **Threads.** All reading, decoding and encoding runs in a thread (`anyio.to_thread.run_sync`), never on the event loop.

**Summary and wells.**

- `PlateSummary` rows A–P and columns 1–24 come from the code profile, with wells from the index.
- `first_image` has axes `c, y, x`, level `s0` with shape `[5, 2160, 2160]` and chunks `[1, 1080, 1080]`, plus the profile's channels and windows for that cell line.
- `GET …/wells/{well}` returns `<well>_s1.zarr` to `<well>_s9.zarr`; an unknown well returns 404.

**Code profile.**

- Write the idr0080 profile as a constant in `packed_profiles.py`, matched on the index file name **and** its row count (155,520).
- It holds everything in Appendix B, and a pin test fails loudly on any transcription error.
- Any other detected index gets a generic profile: numeric channel labels, inferred plate format, auto-contrast and no metadata.

### 4.4 Metadata

- Read `metadata/metadata_idr0080.csv` once (3,456 rows) and group it by `(experiment_name, int(plate))`. The join with the index is complete.
- Cache per-plate responses. A missing CSV returns `available: false`; images remain browsable.

| Response field | idr0080 source |
| --- | --- |
| `compound` (label "Gene") | `gene`; `EMPTY_control` is shown as "EMPTY control" |
| `is_control` | `treatment == "EMPTY_control"` (56 wells per plate) |
| `moa` (label "Well type"; fixed colours, Appendix B.4) | `well_type_label`: Query guides, Cutting controls (Chr2), Non-cutting controls (Luc, LacZ); empty only on EMPTY wells |
| `well_type` | `perturbation_type` (always CRISPR) |
| `details` | Guide (`idr_guide`), Ensembl ID (`idr_gene_identifier`), Control category (`idr_control_category`) |
| Plate | id and barcode `SQ000<plate>`, cell line `cell_type`, project "idr0080 · Cell Health" |
| Acquisition | 1 timepoint, 9 sites per well, channel names |
| `labels` | `compound` "Gene", `moa` "Well type", `control` "EMPTY control" |
| `category_colors` | Appendix B.4 |

Optional model additions:

- `ChannelSummary.active`, also parsed from OMERO for native stores;
- `WellMetadataSummary.details` (a list of `{label, value}`);
- `PlateMetadataResponse.labels` and `PlateMetadataResponse.category_colors`;
- `FilesystemEntry.detail`.

Tests that assert exact TIMED response bodies gain the new optional keys and nothing else.

### 4.5 Viewer and plate view

- **Contrast fix that keeps TIMED identical.**
  - In `ImageViewer.tsx` `channelStates()`: when a window has both `start` and `end`, make the initial contrast `[start, end]`. Today it is `[start, domain max]`, and `end` is discarded. Leave the auto-statistics path unchanged.
  - In the same commit, make TIMED's facade (`_bare_image_summary`) declare `end` 65,535, which is what TIMED has always displayed.
  - Native OME-Zarr windows, such as the test images' 100–8,000, now apply as declared; update tests that asserted the old maximum.
- **Default visibility.** Set each channel's visibility from OMERO `active ?? true`.
- **Single timepoint.** When `timeCount <= 1`, hide the `t x / y` status item and title the controls "Channels". The plate view says "Open field" and "Compare *N* fields"; TIMED keeps "Open trajectory".
- **Labels.** `ViewerSourceFacts`, the filters, the legend and `PlateMetadataPanel` take their slot names from `metadata.labels`.
- **Metadata panel.**
  - Render `details`.
  - When `labels` is present, omit null fields instead of printing "Not specified".
  - Write "1 timepoint", singular.
- **Colours.** Well colours use `category_colors` when present, else the existing palette.
- **Comparison guard.** Store each tray entry's channel labels. Disable comparison, with a one-line reason, when the lists differ.
- **Shared grid.** Extract the well grid from `DataBrowser.tsx` into a shared `PlateGrid` component (props for colours, badges, markers and dimming) without changing its behaviour. The QC screen reuses it.
- **Text.** Change `data.astro`'s heading and lede so they describe datasets in general.

### 4.6 Same perturbation across plates

All nine plates share one plate map (inventory): a well position carries the same guide on every plate. So "the same well on other plates" gives exact replicates: two more in the same cell line and three in each of the other two.

- **Server.** Add `GET /api/datasets/{dataset_id}/matches?well=<well>&by=well|gene|control` for packed plates; other stores return 404.
  - `well` returns the same well on the other eight plates.
  - `gene` returns wells with the same gene on all nine plates (other guides included), excluding this well.
  - `control` returns the EMPTY control wells on the same plate.
  - Each match carries the virtual plate path, plate label, cell line, well, guide, `is_control` and well type. Matches are sorted by cell line, plate and well, at most 200.
  - The source is the cached metadata CSV.
- **UI.**
  - With a well selected in the plate panel, offer "Same well on other plates", "Same gene" and "Controls on this plate".
  - Each lists its matches grouped by cell line, with **Add to comparison**: link the plate (idempotent), fetch the well's fields, and add the well at site 1 to the tray, where the site can be changed.
  - The four-field comparison limit and the channel guard apply.

## 5. Phase 2 design: segmentation and QC review

All Phase 2 sources sit under the idr0080 root, so no new data roots are needed. Appendix A.2 lists them.

### 5.1 Mask stores and outlines

**Where.** Each model is a folder `<root>/masks/<model_tag>/` with `manifest.json`. Its `schema.store_format` is `tar+wal/v1`.

Each plate has five files: `<model_tag>/<experiment>/Plate<plate>.{masks.tar, wal.jsonl, index.parquet, qc.parquet, lease}`.

| Model | Plates covered | Notes |
| --- | --- | --- |
| `idr0080_cpsam_B90` | All 9, every field and kind | **Default.** Recipe IDR0080_B90 (nuclei from DNA; cells from ER and AGP, diameter 90). This model fed production profiling |
| `cpsam_stock_R2_top999` | `idr0080-A549 / 14610` only | Recipe R2. Offer it only where its store exists |

Both manifests say `frozen: false`.

**Read contract.** Read the stores exactly as Appendix D.2 states.

The operator's check of this contract against all ten stores passed (Appendix D.4). Record the contract in `docs/decisions.md` at the start of M1. In brief:

- **The log is authoritative.** The write-ahead log has one JSON line per committed field. The last record wins per `(well, site)`, and `tar_end` marks the valid tar length after that field.
- **Corruption.**
  - A final line without a newline is a write in progress; ignore it.
  - Any other unreadable line, or a tar shorter than the largest `tar_end`, makes the store corrupt.
- **Offsets.** Each mask member's `offset` points at its data, with a 512-byte USTAR header before it, named `<experiment>/Plate<plate>/<well>_s<site>_<kind>.tiff`. Plate 14610's first field has nuclei at 512 (146,456 bytes), cells at 147,968 and cytoplasm at 339,456, and `tar_end` 576,000.
- **Masks.** A mask is a single-page, zlib-compressed uint16 TIFF at the image's size. 0 is background and each object has its own label.
- **Snapshots.** `index.parquet` and `qc.parquet` are written when a plate is finalised.
- **Readers.** Readers take no locks and never open `.lease` files. Leases left by the writers' Slurm jobs of 2026-10-15 are still present; ignore them.

**Server module (`masks.py` in the data server).**

- **`MaskCatalog`.** Lists the models for a packed plate. A model is available when its tar and log exist for that `(experiment, plate)`. The response returns `model_tag`, recipe, kinds, `frozen`, `created_at`, `available` and `default`.
- **`PlateMaskStore`.** Builds the offset table from the log. It records a revision `(log size, log mtime_ns, tar size)`, stats the log and tar on each request (cheap), and rebuilds when they change, so a non-frozen store that grows is picked up. On the first read from each store it verifies the USTAR header before an offset: name and size, as in Appendix D.2.
- **Mask cache.** Decoded label images are cached in an LRU bounded by bytes (default 512 MB, settable with `serve --mask-cache-mb`; about 9.3 MB per mask).
- **Outlines.** An outline pixel is a non-zero label that differs from any 4-neighbour (edges padded). Width *w* in 1–4 dilates the outline by *w* − 1 pixels using numpy shifts; no SciPy.
- **PNG.** A 1-bit indexed PNG at plane size: colour type 3, a two-entry palette, and a `tRNS` chunk making index 0 transparent and index 1 the requested colour. Pack rows with `numpy.packbits`, prefix each row with filter byte 0, and compress with `zlib`. Encoding then takes a few milliseconds and responses stay small.
  - Cache encoded PNGs in an LRU (default 256 MB).
  - The ETag comes from the store revision, offset, size, kind, colour and width.
  - Responses use `Cache-Control: private, max-age=3600`.
- **Threads.** All of this runs in worker threads.

**Endpoints** (packed plates only; others return 404):

- `GET /api/datasets/{id}/masks` lists the models for the plate.
- `GET /api/datasets/{id}/masks/{model}/{well}/{site}/{kind}.png?color=RRGGBB&width=2` returns the outline image.
- A missing record or kind returns 404; a corrupt store returns 409 with the reason.

**Viewer.**

- **Panel.** A "Segmentation" panel appears in the viewer controls when the plate has masks. It has:
  - a model selector (`idr0080_cpsam_B90` first);
  - a "May still change" badge when `frozen` is false;
  - per-kind toggles with colours (Nuclei `FFD400`, Cells `FFFFFF`, Cytoplasm `FF8C00`), all off by default;
  - outline width (default 2) and opacity;
  - a count line such as "731 nuclei · 616 cells" from the field's QC (section 5.2).
- **Drawing.** One deck.gl `BitmapLayer` per visible kind, with `image` set to the outline URL under `/backend/…` and `bounds` `[0, H, W, 0]`, so it aligns with the image in pixel coordinates.
  - Add the layers through the Viv viewer's deck properties, checking how the pinned Viv version accepts extra layers.
  - Each comparison source gets its own layers for its own field; the overlay settings are shared.
- **URL preset.** A viewer URL may carry `outlines=nuclei,cells` (and `model=`) to start with those kinds switched on. The QC screen's review queue uses this; it has no effect on TIMED.

### 5.2 QC data on the server

**Source** (read-only): the per-field segmentation QC table, `masks/<model>/<experiment>/Plate<plate>.qc.parquet`. It has one row per field (3,456 per plate) and 60 columns (Appendix A.3).

- Each model's store has its own table; the default is `idr0080_cpsam_B90`.
- If the table is missing (a plate not yet finalised), QC is unavailable for that plate: `available: false`, with the reason "QC table not finalised".
- These are measurements made at segmentation time, not decisions about what to remove. Nothing else feeds the QC features (section 2, blind QC).

**Metric catalogue.**

- Build the catalogue from the QC table's schema: every numeric column that is not a key or bookkeeping column (Appendix A.3).
- Use the curated label, group and unit from Appendix A.3. A column named `<family>_<stain>` that is not listed gets the generic label "<Family> · <stain>" in the group "Other image metrics".
- Keep labels, groups, units and descriptions in a catalogue file shipped with the data server (`qc_catalogue.json`), filled from Appendix A.3, so the operator can refine the wording without code changes. A metric missing from the file uses the generic rule and shows its column name as its description.
- Add one derived metric, `saturation_pct_max`: the largest `saturation_pct_<stain>` of the field.

**Endpoints** (packed plates only; others return 404). Each takes `model=` (default `idr0080_cpsam_B90`); everything is cached per table revision.

| Endpoint | Returns |
| --- | --- |
| `GET /api/datasets/{id}/qc` | `available`, and the source used: table path, model tag, row count, revision, and whether it came from `qc.parquet` or the log |
| `GET /api/datasets/{id}/qc/fields` | The plate's table in columnar JSON: `well[]`, `site[]`, and `metrics: {name: [number or null]}` for every catalogue metric. NaN becomes null. ETag from the table's revision |
| `GET /api/datasets/{id}/qc/field?well=&site=` | One field's metrics and the plate medians, for the viewer |
| `GET /api/datasets/{id}/qc/metrics?reference=plate\|cell_line\|dataset` | The catalogue (name, label, group, stain, unit, description, `derived`), with statistics over the reference set: n, nulls, min, p01, p02, p05, p25, p50, p75, p95, p98, p99, max; and a 40-bin histogram between p0.5 and p99.5, with counts below and above |
| `GET /api/datasets/{id}/qc/summary?f=…&combine=&scope=&types=` | Counts of matching sites and wells per plate, for the plate and its siblings in the same index. Each `f` is `<metric>,<lo>,<hi>,<inside\|outside>`; `scope` is `cell_line` or `dataset`. Each plate is returned with virtual path, label and cell line |

**Filter semantics.** These are shared by the server and the browser and are tested against one set of cases.

- A filter is `{metric, lo, hi, mode}`. `inside` matches `lo ≤ v ≤ hi`; `outside` matches `v < lo` or `v > hi`. A null never matches.
- `combine` is `any` (the default: flag a field when any filter matches) or `all`.
- A well-type filter (all, or any of Query guides, Cutting controls, Non-cutting controls, EMPTY control) applies first.
- A well matches when at least one of its sites matches.

### 5.3 QC information on the Data page and in the viewer

- **Site picker** (tray and plate panel): each site shows its cell count, as in "Site 4 · 612 cells".
- **Viewer.** A "Field QC" block for the open field:
  - nuclei, cells and cytoplasm counts;
  - median nucleus and cell area (µm² after S1);
  - DNA mean intensity;
  - DNA focus against the plate median;
  - maximum saturation.
- **No exclusion markers.** The Data page's plate grid is unchanged.

### 5.4 The QC screen

A new page, `qc.astro`, with a `QcBrowser` island and a "QC" link in the site navigation. Like the Data page, it needs a connected session.

**Layout.**

- **Header.** The "Dataset" selector (same behaviour as section 4.1, without "Files"), a plate picker grouped by cell line, and a model selector (default `idr0080_cpsam_B90`; the stock model only on 14610).
  - The plate picker lists plates from the same virtual listing as the Data page, and choosing a plate links it (idempotent).
  - A dataset without QC tables, such as TIMED, shows "QC metrics are not available for this dataset."
- **Filters panel.**
  - **Add filter** opens a metric picker grouped as in Appendix A.3 (Segmentation, Morphology, Intensity, Focus, Exposure, Other image metrics), with search.
  - Each filter row shows:
    - the label, unit and description;
    - a histogram strip of the reference distribution;
    - a two-thumb range slider with numeric inputs;
    - markers at the reference median and the 2nd and 98th percentiles;
    - an **inside** or **outside** toggle;
    - a remove button.
  - Panel-level controls:
    - **Reference distribution**: this plate, the cell line, or all plates; it sets the histogram, the markers and the presets, never the stored thresholds;
    - **Match**: any or all filters;
    - **Well types**: all, or any of Query guides, Cutting controls, Non-cutting controls and EMPTY control.
  - **Presets.** These are starting points the operator adjusts. Thresholds come from the current reference distribution.

    | Preset | Filter |
    | --- | --- |
    | Few cells | `cell_final` below p02 |
    | Crowded | `cell_final` above p98 |
    | Soft focus (DNA) | `focus_score_normvar_DNA` below p02 |
    | Saturation | `saturation_pct_max` above 0 |

- **Plate grid** (the shared `PlateGrid`).
  - Wells with matching sites are highlighted, shaded by how many of their nine sites match, with the count as a badge.
  - Other wells are dimmed.
  - The tooltip lists the matching sites with their values for the active filters.
  - Selecting a well shows its nine sites as numbered tiles: match state and key values. The tiles are numbered, not placed spatially.
- **Matches table.** One row per matching site.
  - **Columns:** well, site, gene, well type, and the value of each active filter (with the multiple of the plate median).
  - **Sorting:** by any column; the default is the first filter's metric, worst first.
  - **Row actions:** **Open** (viewer at that field, outlines on) and **Add to comparison** (tray).
  - **Table actions:** **Review in viewer** (section 5.5) and **Download CSV** (the visible rows and values).
- **Other plates.** A compact list of match counts per plate in the cell line, or all nine. It comes from `qc/summary`, refreshed 300 ms after the sliders stop moving. Clicking a plate switches to it with the same filters.

**Behaviour.**

- Load `qc/fields` once per plate and `qc/metrics` once per reference.
- Filter the plate in the browser on every slider movement; 3,456 rows must update in under 50 ms.
- Keep the whole state in the URL: dataset, plate, model, filters, match mode, reference and well types. Use the same `f=` encoding as `qc/summary`. A reload or a shared link restores the view exactly.

### 5.5 Review queue

- **Starting.** **Review in viewer** stores the current matches, in table order, under a new key in `sessionStorage` (`timed.review.<id>`). Each entry holds the dataset id, field path, well, site and label. The QC URL is saved with them.
- **Opening.** It then opens the viewer at the first match with `review=<id>&index=0`, plus the outline preset from section 5.1. The default preset is nuclei and cells.
- **Review bar.** With `review` present, the viewer shows "Review 3 of 27":
  - **Previous** and **Next** buttons, with the keys `[` and `]`;
  - the active filters' values for the current field;
  - **Back to QC**.

  Moving keeps the overlay settings and the channel contrast.
- **TIMED.** Without `review`, the viewer is unchanged, so TIMED is unaffected.

### 5.6 Scale bar

**Operator:** read the Phenix acquisition index for the pixel size and report the output:

```bash
cd /nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/biomorph/idr0080/source
for f in SQ*/Images/Index.idx.xml; do
  echo "$f: $(grep -m1 -o '<ImageResolutionX[^<]*' "$f") $(grep -m1 -o '<ImageResolutionY[^<]*' "$f") $(grep -m1 -o '<ObjectiveMagnification[^<]*' "$f")"
done
```

Then, in the code:

- Store the value (metres converted to micrometres) as `pixel_size_um` in the idr0080 profile, after confirming that all nine plates agree.
- Give the facade's `y` and `x` axes the unit `micrometer` and the scale `[1, s, s]`, so Viv's scale bar appears as it does for TIMED.
- Show areas in µm² as well as px in the viewer's Field QC block and the QC screen.

If the value is missing or the plates disagree, skip S1 and record why.

## 6. Performance targets

| Action | Target |
| --- | --- |
| First open of an idr0080 field | Server work under 0.5 s (one read of about 47 MB, decode, encode); about 28 MB through the tunnel |
| First outline of one kind | Server work under 0.2 s (mask decode measured at about 15 ms); under 20 ms cached |
| `qc/fields` for a plate | Under 1 s cold; cached afterwards |
| `qc/metrics` for the whole dataset | Under 2 s cold (nine QC tables, about 0.7 MB each) |
| Slider movement on the QC screen | Under 50 ms to refilter and redraw 3,456 sites, in the browser |
| `qc/summary` | Under 300 ms with cached tables |
| Memory | Plane cache 1 GiB, mask cache 512 MB, PNG cache 256 MB. The job keeps 2 CPUs and 8 GB unless a measurement says otherwise |

## 7. Milestones

Each milestone ends with its tests green, the existing suites green, and a push.

**Phase 1** (`feature/idr0080`):

- **B0. Branch, baseline, `AGENTS.md`, dev-version installs, TIMED regression tests** (section 3).
- **C1. Collections and the dataset selector** (section 4.1). Built against today's server.
  *Done when* the new end-to-end tests and the existing suites pass.
- **D1. Packed foundation**: detection, virtual paths and listing, registry key, catalogue, idr0080-like fixture.
  *Done when* its server tests pass.
- **D2. Packed facade**: field loading, chunks, summary and wells.
  *Done when* all server tests pass, including a zarr-python round trip over HTTP, and it runs on the test cluster.
- **D3. idr0080 profile and metadata** (sections 4.3–4.4).
  *Done when* the pin test and mapping tests pass.
- **V1. Viewer and plate view**, including the shared `PlateGrid` (section 4.5).
  *Done when* the idr0080 end-to-end tests pass and the TIMED regression tests are unchanged.
- **X1. Same perturbation across plates** (section 4.6).
- **A1. Operator, on Arrhenius**, with `config/profiles.example.yaml`:
  1. TIMED is preselected and identical to main.
  2. Open idr0080, A549, 14610, A01 site 1, and compare it with the inspection previews.
  3. Compare A01 across three plates in different cell lines, plus an EMPTY control.
  4. Record timings in `docs/arrhenius.md`.
  5. If a field takes more than 3 s to open, try in order and keep what helps: full-plane chunks; building Viv selections only for visible channels; then 4 CPUs.
- **R1. Merge.**
  - Update `README.md`, `CLAUDE.md` and `docs/decisions.md`. Add `docs/data/IDR0080.md` (Appendices A–B) and `docs/ADDING_DATASETS.md`.
  - Set the version to `0.4.0`.
  - Keep the released collections in `config/profiles.example.yaml`; they reach `main` with the merge.
  - Merge and tag `v0.4.0`.

**Phase 2** (`feature/idr0080-masks`, from `main` after R1):

- **M0. Done (2026-10-23).** The operator ran a read-only contract check on the cluster, and every check passed (Appendix D.4).
- **M1. Mask reading and outlines on the server** (section 5.1). Start by recording Appendix D and its verification in `docs/decisions.md`.
  *Done when* the mask fixture tests in section 8.5 pass.
- **M2. Segmentation panel, overlay and outline preset in the viewer.**
  *Done when* toggling outlines changes the canvas at the fixture label boundaries in the end-to-end test, including in comparison mode.
- **Q1. QC data on the server** (section 5.2): the five endpoints, the metric catalogue, and the shared filter cases.
  *Done when* the QC server tests pass.
- **Q2. QC information on the Data page and in the viewer** (section 5.3).
  *Done when* site labels and the Field QC block are covered by end-to-end tests.
- **Q3. The QC screen** (section 5.4).
  *Done when* the QC end-to-end tests pass and the browser filter agrees with the shared cases.
- **Q4. The review queue** (section 5.5).
  *Done when* the review end-to-end test passes and TIMED's viewer is unchanged.
- **S1. Scale bar** (section 5.6), after the operator reports the pixel size.
- **A2. Operator, on Arrhenius.**
  1. On 14610 A01 site 1, nucleus outlines (731 nuclei) and cell outlines (616) align with the image, and the stock model is offered only on 14610.
  2. The viewer's Field QC block for that field shows the same counts.
  3. On the QC screen, apply each preset to 14610 with the plate as reference, and check that the highlighted sites agree with the histograms.
  4. Review the matches in the viewer with outlines on.
  5. Try "Few cells" across all plates, checking the per-plate counts.
  6. Check the scale bar, if S1 was done.
  7. Record outline and QC timings in `docs/arrhenius.md`.
- **R2.** Merge as `0.5.0`.

## 8. Tests

### 8.1 TIMED regression baseline (B0; passes on unchanged code)

- **End-to-end.**
  - Open a TIMED fixture field through today's flow.
  - Assert channels "405 nm", "505 nm" and "625 nm", each showing contrast minimum 100 and maximum 65,535.
  - Assert the time slider and jump control are present, and the button reads "Open trajectory".
- **Server.** Snapshot the TIMED facade metadata and plate summary for `test_plate.zarr` (all but the window end, which V1 changes to 65,535) and the fixture root listing.
- These stay green in every later milestone.

### 8.2 Collections (C1)

- **Connector validation**: unique ids, a single default, paths inside the data roots, and clear errors.
- **Without collections**: no selector and today's page.
- **Selector**: default and `?collection=` handling.
- **Scoped browsing**: breadcrumbs stop at the collection, plain files are hidden, and Open is idempotent.
- **Navigation**: the back link reopens the plate, the tray persists across datasets, and Files matches today's browser.

### 8.3 idr0080 fixture (D1)

Extend `scripts/make_test_data.py` to write `test-data/packed/idr0080-like/`. Write tars with `tarfile`, then **reopen them to read `offset_data`**; that attribute is only populated on read.

- **Experiments.** `idr0080-A549` has two plates and `idr0080-ES2` one. Each plate is its own shard tar, named by a `shard` column, with member prefix `idr0080/raw/`.
- **Wells.** Every plate uses one plate map: A01 (EMPTY control), A02 (Chr2 cutting control), B01 (LacZ non-cutting control), C01 (a KRAS guide) and P24 (a second KRAS guide).
- **Images.** Two sites per well; five channels of 64 × 64 uint16, stored contiguously per site.
- **Metadata.** A CSV with all 19 real columns, and a fixture code profile.
- **Decoys.** A fragment index with no tars beside it, and a mask-style index with a `kind` column.

### 8.4 Phase 1 tests

**Server.**

- Detection: the positive case and both decoys.
- Virtual listing and path safety (`..`, unknown experiment or plate, symlinked index outside the roots).
- The registry key and persistence across restarts.
- Summary and wells.
- Facade metadata (validated with `ome-zarr-models`): per-cell-line windows, `active` flags, codec.
- Chunk content: every chunk decodes to the matching slice of the source TIFF.
- Field loading: one contiguous read per field, and single-flight under concurrent requests.
- Errors: 404s for bad keys; ETag and 304; header mismatch, out-of-bounds member and dtype mismatch each fail cleanly.
- Metadata mapping.
- Matches: same well, same gene, controls, the 200 limit, 404 for non-packed stores.
- The pin test.
- A zarr-python round trip.

**End-to-end** (`local-datasets`, one flow repeated on the test cluster):

- Select idr0080, then A549, then a plate; check the well-type colours and the "Gene" filter.
- Open a field and wait for "Image ready". Then check that:
  - pixels are not uniform and there is no time slider;
  - DNA, ER and AGP are checked and RNA and Mito unchecked;
  - the contrast maximum equals the cell line's window end.
- Compare C01 across the three plates plus a control.
- Confirm the guard blocks mixing TIMED and idr0080 fields.

### 8.5 Phase 2 fixtures and tests

**Mask stores.** Extend the generator to write `tar+wal/v1` stores beside the idr0080-like fixture, following Appendix D.2.

- Labels are known squares and discs numbered contiguously from 1. They are stored as zlib TIFFs in members named `<experiment>/Plate<plate>/<well>_s<site>_<kind>.tiff`, with 512-byte USTAR headers and padding.
- One log line per field, carrying `tar_end`, mask offsets, a `qc` record and `git_sha`.
- One field rewritten later: a second log record pointing at a member appended to the tar, so the last record must win.
- `index.parquet`, `manifest.json` (`frozen: false`) and a lease file.
- A second model covering one plate only.
- A log whose last line is half-written (no newline), and bytes after the last committed `tar_end`.
- Separate corrupt stores: one whose log's `tar_end` exceeds its tar, and one with an unreadable line in the middle of its log.

**QC tables.**

- `qc.parquet` per fixture plate, with the real 60 columns of Appendix A.3, plus one unlisted `<family>_<stain>` family to exercise the generic label.
- Engineered values give known matches for each preset: low and high cell counts, low DNA focus, non-zero saturation, and a few nulls.
- One plate has no `qc.parquet`, to exercise the unavailable state.

**Shared filter cases.** `tests/fixtures/qc-filter-cases.json` holds inputs and expected matching sites for: inside and outside ranges, any and all, nulls, and well types. Both pytest and the web unit tests run them.

**Server tests.**

- Model listing and availability per plate.
- Last-record-wins deduplication; a half-written last line and bytes after the last `tar_end` are ignored.
- Both corrupt stores return 409.
- Revision change: append a log line during the test and the new offsets are served.
- Lease and lock: `.lease` paths are never opened (instrument `os.open` and `open`), and no `flock` is taken.
- Header verification.
- Outline correctness on the known shapes for widths 1–4.
- PNG validity: parse it, and check the palette, the `tRNS` chunk, the dimensions and the outline pixels.
- ETag and 304.
- The metric catalogue: curated labels, the generic rule, excluded bookkeeping columns, and the derived maximum saturation.
- Statistics and histograms against numpy, for each reference set.
- `qc/fields` shape and nulls; `qc/field`; the `qc` status, including the unavailable state.
- **Blind QC.** Instrument file opens across the whole Phase 2 test run and assert that nothing under a `profiles/` folder, and no `run_ledger.jsonl`, is opened. The fixture includes a decoy `profiles/…_mad_exclusions.json` and a decoy `run_ledger.jsonl` to make this meaningful.
- `qc/summary` counts against the shared cases.
- Pixel-size facade metadata (S1).

**End-to-end.**

- **Viewer.**
  - The segmentation panel appears only for packed plates, and toggling a kind colours pixels at fixture boundaries.
  - The model selector, badge and `outlines=` preset work.
- **Data page.** Site labels show cell counts, and the plate grid is unchanged.
- **QC screen.**
  - TIMED shows the "not available" message.
  - On an idr0080 fixture plate, each preset highlights exactly the expected wells and sites.
  - Typing thresholds into the numeric inputs updates the grid and table.
  - Other plates' counts update, and a reload restores the state from the URL.
  - The CSV contains the visible rows.
- **Review queue.** Open the queue, move with Next, Previous and the keys, keep outlines on, and use Back to QC.

## 9. Later

- **CellPrompter's exclusion records** (`profiles/idr0080_cpsam_B90/per_experiment/<experiment>_plate<plate>_mad_exclusions.json`). For example, compare them with the operator's own review once it is done.
- **Recording review decisions** (for example "checked, fine" or "should be excluded") in the data server's state folder, never in the dataset.
- **RxRx3-core** (Appendix C).
- **Masks from the second model** on more plates, as CellPrompter produces them.
- **TIMED contrast windows** derived from a survey, whenever the operator wants to change TIMED's look.

## Appendix A. idr0080 facts

### A.1 Images and metadata

| | |
| --- | --- |
| Root | `/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/biomorph/idr0080` |
| Index | `idr0080-raw.index.parquet`: 155,520 rows (one per plane), 31,104 fields; `metadata/index_scope.json` marks it complete |
| Columns | `relpath, experiment, plate, well, site, channel, offset, size, shard` |
| Tar files | Nine shards `idr0080-raw-SQ000146xx.tar` beside the index, named by `shard`; member name `idr0080/raw/` + `relpath`; `offset` is the member data |
| Experiments and plates | `idr0080-A549`, `-ES2`, `-HCC44`, three plates each (14610–14618, unique); barcode `SQ000<plate>` |
| Wells, fields, channels | All 384 wells (A–P × 01–24), sites 1–9, channels 1–5; one plate map shared by all nine plates |
| Planes | Uncompressed TIFF, 2160 × 2160 uint16; a site's five channels are contiguous in its shard |
| Metadata | `metadata/metadata_idr0080.csv`, 3,456 wells, 19 columns, all joined |
| Join keys | CSV `experiment_name, plate (text), address` = index `experiment, plate, well` |

The survey confirmed this sample of the data: 450 sampled planes (90 random fields) had the expected shape and dtype, and all 450 tar headers matched the index.

### A.2 Masks and QC (inventory, 2026-10-23)

| Item | Location (under the root) | Size and shape |
| --- | --- | --- |
| Default mask store | `masks/idr0080_cpsam_B90/<experiment>/Plate<plate>.*` | Tar about 1.34 GB per plate (12.0 GB total); log about 5.7 MB per plate with 3,456 lines; index 10,368 rows; 93,312 masks; complete coverage |
| Per-field QC table | `masks/idr0080_cpsam_B90/<experiment>/Plate<plate>.qc.parquet` | 3,456 rows × 60 columns (about 0.7 MB) |
| Second mask store | `masks/cpsam_stock_R2_top999/idr0080-A549/Plate14610.*` | One plate, with its own QC table |
| Manifests | `masks/<model>/manifest.json` | `frozen: false`, `tar+wal/v1`, uint16, kinds nuclei/cells/cytoplasm, recipe, software provenance |
| Leases | `Plate<plate>.lease` | JSON `{pid, host, slurm_job_id, slurm_array_task_id, acquired_at}` from 2026-10-15; never touched |
| Acquisition index | `source/SQ000<plate>/Images/Index.idx.xml` | Phenix metadata; pixel size (S1) |

Plate 14610, well A01, site 1 (`idr0080_cpsam_B90`): 774 raw nuclei, 43 removed at the border, **731 final**; **616 cells** after 32 orphans removed; 616 cytoplasm; median nucleus area 1,684 px.

### A.3 Per-field QC columns

Every store has the same 60 columns (verified, Appendix D.4):

- 4 keys;
- 8 counts, 5 morphology and 2 intensity measurements;
- 6 bookkeeping columns;
- 30 per-stain measurements (6 families × 5 stains);
- 5 provenance columns.

That makes 45 metrics, plus one derived.

**Keys:** `experiment` (string), `plate` (int64), `well` (string), `site` (int64).

**Bookkeeping and provenance (not metrics):** `nuclei_slot_channels`, `nuclei_slots_filled`, `cells_slot_channels`, `cells_slots_filled`, `diameter_arg`, `segment_seconds`, `model_tag`, `cellpose_version`, `weight_sha256`, `recipe`, `git_sha`.

Counts are int64; all other metrics are double.

This table is the starting content of `qc_catalogue.json`; the operator may refine the wording there.

| Column | Label | Group | Unit | Description |
| --- | --- | --- | --- | --- |
| `nuclei_final` | Nuclei | Segmentation | count | Nuclei kept after removing those at the image border |
| `cell_final` | Cells | Segmentation | count | Cells kept after removing cells without a nucleus |
| `cytoplasm_final` | Cytoplasm objects | Segmentation | count | Cytoplasm objects, one per kept cell |
| `nuclei_raw` | Nuclei before filtering | Segmentation | count | Nuclei found by segmentation |
| `nuclei_border_removed` | Nuclei removed at the border | Segmentation | count | Nuclei touching the image border |
| `cell_raw` | Cells before filtering | Segmentation | count | Cells found by segmentation |
| `cell_orphan_removed` | Cells removed without a nucleus | Segmentation | count | Cells with no nucleus inside |
| `cytoplasm_empty` | Empty cytoplasm objects | Segmentation | count | Cells whose cytoplasm has no pixels |
| `median_nucleus_area_px` | Median nucleus area | Morphology | px (µm² after S1) | Median area of the kept nuclei |
| `median_nucleus_diameter_px` | Median nucleus diameter | Morphology | px (µm after S1) | Median diameter of the kept nuclei |
| `median_cell_area_px` | Median cell area | Morphology | px (µm² after S1) | Median area of the kept cells |
| `median_cell_diameter_px` | Median cell diameter | Morphology | px (µm after S1) | Median diameter of the kept cells |
| `median_cytoplasm_area_px` | Median cytoplasm area | Morphology | px (µm² after S1) | Median area of the cytoplasm objects |
| `dna_mean` | DNA mean intensity | Intensity | grey levels | Mean DNA-channel intensity over the field |
| `dna_max` | DNA maximum intensity | Intensity | grey levels | Highest DNA-channel intensity in the field |
| `focus_score_normvar_<stain>` | Focus (normalised variance) · stain | Focus | — | Sharpness from normalised intensity variance; lower is blurrier |
| `focus_score_laplacian_<stain>` | Focus (Laplacian) · stain | Focus | — | Sharpness from the Laplacian; lower is blurrier |
| `saturation_pct_<stain>` | Saturated pixels · stain | Exposure | % | Share of saturated pixels |
| `bright_outlier_pct_<stain>` | Bright outlier pixels · stain | Exposure | % | Share of pixels flagged as bright outliers |
| `percent_minimal_<stain>` | Minimal pixels · stain | Exposure | % | Share of pixels flagged as minimal |
| `sbr_<stain>` | Signal-to-background · stain | Intensity | ratio | Signal-to-background ratio of the stain |
| Other `<family>_<stain>` (none today) | "<Family> · stain" | Other image metrics | — | The column name |
| `saturation_pct_max` (derived) | Saturated pixels · any stain | Exposure | % | The largest saturated share across the stains |

The stains are DNA, ER, AGP, RNA and Mito.

## Appendix B. Display values (survey on n213, 2026-10-22)

### B.1 Channels

| Index channel | Stain | Colour | Shown by default |
| --- | --- | --- | --- |
| 1 | DNA | `0000FF` | yes |
| 2 | ER | `00FF00` | yes |
| 3 | RNA | `00FFFF` | no |
| 4 | AGP | `FF0000` | yes |
| 5 | Mito | `FF00FF` | no |

The order comes from CellPrompter's records, which map the original source files `ch1…ch5` to these stains. "Shown by default: no" is encoded as OMERO's `"active": false`.

### B.2 Contrast windows

The default window is the pooled 1st to 99.9th percentile of 30 random fields per cell line; windows are per cell line. The slider range is 0 to twice the highest 99.9th percentile, rounded up to 1,000.

| Channel | A549 | ES2 | HCC44 | Slider range |
| --- | --- | --- | --- | --- |
| DNA | 292–11,552 | 234–12,971 | 254–13,104 | 0–27,000 |
| ER | 438–17,554 | 309–18,238 | 318–22,196 | 0–45,000 |
| RNA | 554–9,283 | 375–10,793 | 497–12,756 | 0–26,000 |
| AGP | 151–5,035 | 108–5,778 | 117–6,968 | 0–14,000 |
| Mito | 324–4,066 | 334–7,120 | 394–5,711 | 0–15,000 |

### B.3 Chunk encoding

Blosc with Zstd level 3, byte shuffle and `typesize` 2: 1.66× in 20 ms per 9.3 MB plane. For comparison, zlib level 1 (as HTTP gzip) gave 1.29× in 175 ms, so do not add HTTP compression middleware. Chunks are 1080 × 1080.

### B.4 Metadata semantics

- **Controls.** `is_control` is `treatment == "EMPTY_control"`: 504 wells, 56 per plate. Cutting (Chr2) and non-cutting (Luc, LacZ) guides are perturbations with their own colour classes.
- **Well types:**

  | Well type | Control category | Wells | Colour |
  | --- | --- | --- | --- |
  | Query guides | targeting | 2,376 | `#3b82a0` |
  | Cutting controls | Chr2 | 432 | `#b86b35` |
  | Non-cutting controls | Luc, LacZ | 144 | `#9367a8` |
  | (empty) | EMPTY | 504 | neutral control style |

- **Genes and treatments.** 58 gene values; 119 treatments (118 guides plus EMPTY_control). Every well is CRISPR; concentration and SMILES are empty.

## Appendix C. RxRx3-core, for later

Nothing here is built now.

- **Location.** `/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/rxrx3-core`, with one `rxrx3-core-raw.tar` and `rxrx3-core-raw.index.parquet` (1,335,606 rows, no `shard` column, offset-ordered).
- **Plates.** Plate key `(experiment, plate)`: 180 experiments, 1,744 sparse 1536-format plates (rows A–AF, multi-letter).
- **Images.** 512 × 512 uint8 planes, site 1 only.
- **Channels and windows.** DNA, ER, AGP, RNA, Mito, PM, with windows 2–89, 4–187, 3–49, 3–87, 2–34 and 5–115.
- **Encoding.** Blosc-Zstd 3 without shuffle.
- **Metadata.** `metadata/metadata_rxrx3_core.csv`, 222,601 wells. Controls are `EMPTY_control`; `CRISPR_control` wells are perturbations. `concentration` is in µM.
- **Masks.** `masks/` and `masks_gene/cpsam_sharpen23_R2_top999` (frozen).

## Appendix D. CellPrompter data contract

The viewer relies on these statements about CellPrompter's outputs, never on its code. The operator's read-only contract check tested D.2 and D.3 on all ten real stores, with `imagecodecs` blocked. Every check passed (D.4); the output is `docs/data/idr0080-mask-contract.json`. If the data ever disagrees with anything here, stop and ask.

### D.1 Raw images (Phase 1)

- **Index rows.** There is one index row per plane. `(experiment, plate, well, site, channel)` is never null and never repeated. Refuse an index that breaks this.
- **Tar members.** `shard` names a tar beside the index, and `offset` is where the member's data starts. The 512 bytes before it are the member's USTAR header: its name is `idr0080/raw/` + `relpath`, and its size is `size`.
- **Reads.** Read exactly `size` bytes.
- **Payload.** A single-page uncompressed TIFF that decodes to one 2-D uint16 plane of 2160 × 2160. Anything else is rejected, never cast.

### D.2 Mask stores (`tar+wal/v1`, Phase 2)

- **Models.** Each model is a folder `masks/<model_tag>/` with `manifest.json`, which holds:
  - `frozen`;
  - `schema.store_format`, which is `"tar+wal/v1"`;
  - `schema.mask_dtype`, which is `"uint16"`;
  - `schema.kinds`.

  Skip folders with any other format.
- **Plate files.** `<experiment>/Plate<plate>.masks.tar`, `.wal.jsonl`, `.index.parquet`, `.qc.parquet` and `.lease`. A model is available for a plate when its tar and log exist.
- **Write order** (as the data shows). A writer appends a field's three mask members to the tar, then appends one line to the log. Therefore:
  1. **Lines.** The log is UTF-8 JSON Lines. Each complete line is one committed field: `{well, site, tar_end, masks: [{kind, offset, size}, …], qc: {…}, git_sha}`.
  2. **Half-written lines.** A final line without a newline is a write in progress; ignore it. Any other line that does not parse makes the store corrupt.
  3. **Rewrites.** The last record wins for each `(well, site)`. A field segmented again later gets new members and a new line.
  4. **Committed length.** `tar_end` is the valid tar length after that field, a multiple of 512, and it never decreases. Bytes after the largest `tar_end` are uncommitted; ignore them. A tar shorter than the largest `tar_end` makes the store corrupt.
  5. **Member layout.** Each member's `offset` points at its data. The 512 bytes before it are its USTAR header, with name `<experiment>/Plate<plate>/<well>_s<site>_<kind>.tiff` and size `size`. The payload is padded to a multiple of 512, and a finalised tar ends with standard end-of-archive padding after the last commit.
- **Payload.** A single-page TIFF, zlib (deflate) compressed, uint16, at the image plane's shape. 0 is background, and objects are labelled contiguously from 1. The number of labels equals the field's QC count for that kind (`nuclei_final`, `cell_final` or `cytoplasm_final`). Decode without casting; tifffile needs no `imagecodecs` for this.
- **Snapshots.** `index.parquet` (`experiment, plate, well, site, kind, offset, size`) and `qc.parquet` are written when a plate is finalised. The log stays authoritative for offsets: use the index only to cross-check, and on a mismatch log it and keep the log.
- **Readers.** There can be any number of readers, from any node. They take no locks, never open `.lease` (a writer's lease; leftovers from finished jobs are normal), and open every file read-only.
- **Revision.** `(log size, log mtime_ns, tar size)`. Rebuild the offset table whenever it changes.

### D.3 Per-field QC table (Phase 2)

- **Rows.** `Plate<plate>.qc.parquet` has one row per `(well, site)`, with the columns of Appendix A.3.
- **Values.** 55 of its columns are the log's `qc` records flattened: each key becomes a column as it is, and each `image_qc[metric][stain]` becomes `<metric>_<stain>`. The remaining five are provenance added when the table is written (`model_tag`, `cellpose_version`, `weight_sha256`, `recipe`, `git_sha`).
- **Missing table.** A plate without the table has no QC in the viewer (section 5.2).

### D.4 Verification (2026-10-23, node n216, `imagecodecs` blocked)

| Check | Result |
| --- | --- |
| Stores | 10: nine `idr0080_cpsam_B90` (all plates) and one `cpsam_stock_R2_top999` (14610). All are `tar+wal/v1`, uint16, `frozen: false` |
| Logs | 3,456 complete lines per store, no half-written line, no parse errors |
| Re-segmented fields | None: no superseded records |
| `tar_end` | Always increasing, multiples of 512, every member inside its record |
| Tar length | 1,536–9,728 bytes longer than the last commit (end-of-archive padding) |
| Index against log | Identical for all 10,368 entries in every store |
| Headers | 270 sampled; all USTAR, sizes match, named `<experiment>/Plate<plate>/<well>_s<site>_<kind>.tiff` |
| Masks | 180 decoded: single-page deflate TIFFs, uint16, 2160 × 2160. Labels are contiguous from 1, and every count equals the QC count. Decoding took about 15 ms per mask (median) |
| QC table | The same 60-column schema in every store. The 55 log-derived columns equal the flattened log on every row, and the 5 provenance columns are added at finalisation |
