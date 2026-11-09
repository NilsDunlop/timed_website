# Codex brief: Cell Health QC review

**Version 2.** Phase 1 (viewing Cell Health, 0.4.0) is merged. This brief replaces everything about Phase 2 in `docs/plans/idr0080-codex-brief.md`: its sections on masks, QC, the QC screen and the review queue, with their milestones and tests. That brief still holds for the mask-store contract (its Appendix D).

| Part | Branch | Version | What |
| --- | --- | --- | --- |
| A | `fix/cell-health-channels` | 0.4.1 | Correct the Cell Health channel identity. Done by a separate prompt before this brief; section 3 is for reference |
| B | `feature/qc-review` | 0.5.0 | Connect the QC metrics export: core QC page, field and well review, queues and annotations |
| B.1 | separate future branch | TBD | Add the blinded QC interface on top of the server-side blind response mode designed in Part B |
| C | `feature/segmentation-outlines` | 0.6.0 | Nucleus, cell and cytoplasm outlines from CellPrompter's mask stores |

Part B is implemented and passed its manual Arrhenius A2 check on 2026-11-04.
This brief now records the delivered 0.5.0 behavior; the blinded interface and
Part C remain future work.

## 0. Working agreement

- **Read first:** `CLAUDE.md`, `docs/decisions.md` and this brief. For Part C, also read the old brief's section 5.1 and Appendix D.
- **Input:** `docs/data/qc-export/`, a local copy of the core-ready QC export (`version.json` id `d77939bbbf42`). It is gitignored and must never be committed. It contains hidden columns, so never print real hidden names or values into commits, docs, tests or replies; refer to them only by count.
- **Never** connect to Arrhenius, submit Slurm jobs, or touch real data. Steps marked **Operator** are done by the operator: stop there and wait.
- **Stop and ask** when the export, the repository and this brief disagree, or when a value in the export's dictionary has a meaning this brief does not define.
- **Each part:**
  1. Branch from the latest `main`.
  2. While testing on Arrhenius, use the development version (`0.5.0.devN`, `0.6.0.devN`). Set the release version in the final commit.
  3. Run the full baseline, commit and push.
  4. Give the operator a PR title and description. `gh` is unavailable: the operator opens the PR and merges it with a merge commit.
  5. Create no tags.
- **Decisions** go in `docs/decisions.md`.

## 1. Ground rules

- **TIMED unchanged.** QC appears only for datasets with a configured QC export.
- **One write, nowhere else.** The data server stays read-only for all data. Its only write is appending annotation records to the configured annotations folder (section 4.8). It never writes into the export, the image data, the mask stores or any data root.
- **Nothing hard-coded.**
  - Metrics, with their labels, scales, directions, layers and questions, come from the export's `columns.csv`. The code keeps no metric list.
  - Channel identity comes from `channels.csv`.
  - Paths come from the connector profile.
- **Core first, blinded mode next.**
  - CellPrompter's exclusion records stay unread.
  - The 0.5.0 core API receives the complete configured export, while the
    browser uses a compact reason-first presentation with secondary table
    columns behind progressive disclosure. Core requests omit the `blind`
    query parameter.
  - Every QC endpoint nevertheless accepts `blind=1` in 0.5.0. The server then removes every export column marked hidden and the additional attributes listed in section 4.11 before serialising a response. This server-side response mode is the foundation for a separate later blinded-interface PR.
  - Real hidden columns remain confidential in repository history: refer to them only by count in commits, tests, docs and replies.
- **Isolated.** The website reads only the export's files. It never reads or imports the code that produced them, nor CellPrompter's code.
- **The website excludes nothing.** Annotations are review records. No data or exclusion list changes in the website.

## 2. Channel identity

The export's `channels.csv` gives each stain's packed slot and its original Phenix/IDR channel. The evidence: high-pass correlation with the raw Phenix channels is at least 0.98 for the matching channel and at most 0.69 for any other, over 21 sites.

| Stain | Packed slot (member suffix) | Phenix / IDR channel | Dye | Exposure |
| --- | --- | --- | --- | --- |
| DNA | 1 (`_ch1.tiff`) | 1 (`-ch1`) | Hoechst 33342 | 0.02 s |
| ER | 2 (`_ch2.tiff`) | 2 (`-ch2`) | Concanavalin A, Alexa Fluor 488 | 0.02 s |
| AGP | 3 (`_ch3.tiff`) | 4 (`-ch4`) | Phalloidin Alexa Fluor 568 + WGA Alexa Fluor 555 | 0.02 s |
| RNA | 4 (`_ch4.tiff`) | 3 (`-ch3`) | SYTO 14 | 0.01 s |
| Mito | 5 (`_ch5.tiff`) | 5 (`-ch5`) | MitoTracker Deep Red | 0.02 s |

The packed slots follow stain order, not Phenix numbering. Always map channels by name: through `channels.csv` where an export exists, and through the code profile otherwise. The two must agree (section 4.3).

## 3. Part A: channel identity fix (0.4.1), for reference

Done by a separate prompt before this brief:

- Slot 3 is labelled AGP and slot 4 RNA. Colours and default visibility follow the stain.
- The contrast windows stay with their slots.
- Channels are listed in slot order: DNA, ER, AGP, RNA, Mito.
- Docs and decisions are corrected.

If Part A is not on `main`, stop and ask.

## 4. Part B: QC review from the export (0.5.0)

### 4.1 The export

The export is at `/nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/scratch/qc_metrics_compute/website_export/`. Each re-export replaces the whole folder.

| File | Contents and use |
| --- | --- |
| `site_metrics.csv` | One row per field (31,104): plate, barcode, well, site; every main metric's raw value and shown scale; the context columns; and each channel's packed member name |
| `columns.csv` | The column dictionary: label, metric, channel, scale, `worse` direction, layer, gate, status, one-sentence question, `blind`, primary, marker and default-rule fields |
| `channels.csv` | Channel identity in both numberings (section 2) |
| `labels.csv` | The ordered annotation-label registry and operator guidance |
| `review_list.csv` | The review queue (178 entries). It has ten hidden columns plus public entry ids and review groups |
| `triage_plate_channel.csv` | The plate × channel overview |
| `display_ranges.csv` | The fixed display range per plate × channel, with its display mode |
| `version.json` | The export id (now `d77939bbbf42`), the registry hash and the seven file hashes |

**First step of Part B: document the export.**

1. Add `docs/data/qc-export/` to `.gitignore` if Part A has not.
2. From the local copy, write `docs/data/QC_EXPORT.md`. It describes every file's columns and types, and the values used in `columns.csv`'s `channel`, `scale`, `worse`, `layer`, `gate`, `status` and `blind`. It also covers the context columns, the triage and display-range layouts, and the structure of `version.json`.
3. For hidden columns, record only how many there are, never their names or values.
4. Stop and ask about anything whose meaning is still unclear after
   `QC_EXPORT.md` and the recorded Part B decisions, or if a replacement export
   does not match this contract.

**Keys.**

- plate: 14610–14618, with barcode `SQ000<plate>`;
- well: A01–P24;
- site: 1–9, the Phenix field id, equal to the packed site.

Metric columns are named `<metric>__<channel>__<scale>`. The channel is a stain name, or `site` for field-level metrics.

**What is shown.** The dictionary controls the UI without a metric list in code:

- `site_review` rows are per-field metrics. One slider is created for each metric and channel from the row marked `primary=true`; the raw value appears beside the primary scale.
- `context` rows are shown and sortable but have no sliders.
- `triage` rows identify the plate and channel keys in the overview file.
- Non-key rows from `triage_plate_channel.csv` provide its overview values;
  their `site_review` and `panel` layers retain the producer's roles.
- `panel` rows in other files describe plate- or well-level values.
- `key` and `review` retain their identifier and review-list roles.
- `retired` rows are never shown. Rows with status `retired`, `dropped`, or an unrecognised value are hidden; unrecognised values are listed in QC status. `pending_review` is shown with a badge.

The current contract defines 37 primary rows. Automatic gates are upstream
decisions: the website displays their registered markers and never recomputes
them.

A metric added or retired in the export appears or disappears on reload, with no code change.

`default_flag` is a strict one-sided expression (`< threshold` or
`> threshold`). It provides the initial rule for a review slider. The export
also contains dictionary rows for `triage_plate_channel.csv`, and
`entry_id` plus `review_group` in `review_list.csv`.

### 4.2 Configuration

On the Cell Health collection in the connector profile, add two paths and update `config/profiles.example.yaml`:

```yaml
      - id: idr0080
        title: Cell Health
        # … unchanged …
        qc_export: /nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/scratch/qc_metrics_compute/website_export
        qc_annotations: /nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/scratch/qc_review/annotations
```

- **Passing them on.** The connector validates both paths (absolute for Slurm profiles) and passes them to the data server as `--qc-export <index path>=<export folder>` and `--qc-annotations <index path>=<annotations folder>`. They are keyed by the collection's real index path, so the data server still needs no collection concept.
- **Startup checks.** The export folder must be readable. The annotations folder must exist and be writable; the server never creates it. The server reports both in the handshake capabilities (`qc_review`, `qc_annotations_writable`) and in the QC status endpoint.
- **Not browsable.** Neither folder is a data root, and neither can be listed.
  The writable annotations folder must not equal, contain, or sit inside any
  data root or configured QC export.
- **Switching exports.** Switching to another export folder with the same structure is a profile change only.

### 4.3 Loading an export (data server, new module `qc_export.py`)

- **Hash check.** Read `version.json` and verify each listed file's SHA-256 before using it. A mismatch means the folder is being replaced: keep serving the previous valid export, retry shortly, and report it in the status.
- **Parsing and reloading.**
  - Parse with `pyarrow.csv` and hold the loaded export in memory, keyed by export id.
  - On each QC request, check `version.json`'s modification time and size. Load a changed export in a worker thread, and switch to it in one step once it validates.
- **Validation.** Refuse an export, with a clear message in the status and on the QC page, unless:
  - there is exactly one row per (plate, well, site), every row's field exists in the packed catalogue, and every catalogue field has a row;
  - every metric column in `site_metrics.csv` has a dictionary row, and every dictionary row describing a data column has that column;
  - each channel's packed member name equals the index member for that field and the slot `channels.csv` gives;
  - `channels.csv` agrees with the Cell Health code profile (stain to slot). If it does not, disable QC and say why.
- **Blind response shaping.** Load and validate every export column for the core view. Keep the blind classification with the in-memory schema. For `blind=1`, project out every hidden column and the attributes listed in section 4.11 before constructing the response; never depend on the browser to redact data.

### 4.4 Endpoints

All take `path=<the collection's index path>`, validated through `RootGuard`,
and return 404 when no export is configured for that index. Every endpoint also
accepts `blind=1`. Without it, the 0.5.0 core view receives every export column
and configured attribute. With it, the data server removes hidden columns and
the attributes listed in section 4.11 before serialising the response. JSON
responses under `/api/qc/` are gzip-compressed (image routes stay
uncompressed) and carry ETags based on the export id and response mode.

| Endpoint | Returns |
| --- | --- |
| `GET /api/qc/status` | Availability, export id, registry hash, file-hash check, row count, whether annotations are writable, and any export errors |
| `GET /api/qc/dictionary` | Applicable dictionary rows with labels, primary scale, direction, layer, gate, status, questions, markers and default flag bounds |
| `GET /api/qc/fields` | A columnar site table with keys, Phase 1 metadata, export attributes, context values, site-review values and annotation state |
| `GET /api/qc/field?plate=&well=&site=` | One site's primary and raw metrics, context, attributes, questions and annotation state |
| `GET /api/qc/triage` | The plate × channel overview |
| `GET /api/qc/display-ranges` | Range and display mode per plate × channel |
| `GET /api/qc/review-list` | The review queue in file order; `blind=1` returns only the blinded contract in section 4.11 |
| `GET /api/qc/annotations` | Current field- and well-scope annotations from all files in the folder |
| `POST /api/qc/annotations` | Append an annotation or a retraction (section 4.8) |

### 4.5 QC page

A page `qc.astro` with a `QcBrowser` island, linked as "QC" in the
navigation when the active dataset has QC. TIMED and other datasets without QC
show no link. The page uses the active dataset from Connect and has no dataset
selector of its own. The core page makes unblinded requests.

- **Header:** the dataset name and Review queue action. Export id and shortened
  registry hash are available under **Export details**. If the export changes
  while the page is open, show "New export available" with a Reload button.
- **Plate overview:** a collapsed disclosure presents
  `triage_plate_channel.csv` as plates by channels, including `site`. A
  **Measure** dropdown shows one dictionary-defined value at a time and defaults
  to **Automatic exclusion** when that measure exists. Labels and questions
  come from the dictionary. Clicking a cell limits everything below it to that
  plate and channel.
- **Plate maps:** before filtering, all nine 384-well plates appear as small
  shared `PlateGrid` maps in a 3 × 3 layout. Category and range filters hide
  plates with no included sites, so selecting A549 leaves plates 14610–14612.
  Each well is shaded by the number of its nine sites
  flagged under the current sliders. A zero-to-nine legend and information
  button explain the shading and exact count badge. Hover lists only the sites
  and metrics that actually triggered active review rules; clicking opens the
  well.
- **Metric controls:** exactly one control exists per site-review metric and
  channel, chosen by `primary=true`. A readable, channel-grouped **Metric**
  dropdown selects one control card at a time; operators never need to search
  for an underscored column id. The card shows its dictionary label and
  question, primary scale and `worse` direction, gate and status, histogram,
  and bounds. Raw companion values remain available in the expanded table and
  the compact field-review metric table.
  - `automatic` was decided upstream by a registered rule. The website never
    recomputes or changes it. Registered thresholds from `markers` are shown on
    the slider.
  - `review` is a flag slider initialised from the exported `default_flag`
    expression. It is one-sided (`< threshold` or `> threshold`). A site is
    flagged when it lies beyond the slider, and the slider shows its own
    flagged-site count.
  - `none` is a filter slider, initially fully open. It never flags a site or
    feeds a queue.
  - `worse` is `low`, `high` or `both`, with `both` producing a two-sided
    direction.
  - `pending_review` is shown as a badge. Retired, dropped and unrecognised
    statuses are hidden; QC status lists unrecognised values.
- **Combining sliders:** the table contains sites inside every filter range. If
  any flag slider is active, it contains only sites beyond at least one active
  flag slider.
- **Categorical filters:** cell line and plate remain immediately visible.
  **More filters** contains control status, gene and annotation status (not
  reviewed, reviewed, or a specific label). Dataset-internal well-type and
  protection fields are not operator filters.
- **Live counts:** sites, wells and control sites, both overall and per flag
  slider. The UI calls individual acquisitions **sites**, not fields.
- **Tables:** a **Wells | Sites** toggle changes the virtualised, sortable
  table. The default table is deliberately compact and includes a prominent
  **Flagged by** column naming the active review metric or metrics for each row.
  **Additional table columns** reveals context, attributes and metric values.
  - Sites initially include plate, well, site, flagged reason, gene, control
    identity and annotation status. The disclosure adds every context column,
    every attribute including automatic-exclusion state and protection, and
    primary and raw metric values.
  - Wells initially include plate, well, flagged-site count, flagged reason,
    gene, control identity and annotation status. The disclosure adds the worst
    value among its sites for each active metric plus context and attribute
    summaries.
  - Annotation status is exact-scope: Sites use field annotations for that
    site, and Wells use whole-well annotations. The field viewer still shows
    both the current field and its applicable whole-well annotations.
- **Actions:** "Review these" opens the current site or well rows as a session
  queue (section 4.7).
- **State in the URL:** every interactive QC-page choice, including the selected
  metric and bounds, categorical filters, plate and channel scope, additional
  table-column visibility, Wells/Sites mode and sorting, so reloads and shared
  links restore the view.
- **Speed:** filtering runs in the browser and must update in under 100 ms for
  31,104 sites.

### 4.6 Field view

The existing viewer with a QC panel, opened from the QC page or a queue
(`qc=1`). A site opens directly. A well opens at site 1 with a site stepper.

- **Review reason.** Queue navigation places **Flagged by** above the compact
  review table so the operator immediately sees why the image is being
  reviewed.
- **Display ranges.** Each channel always uses its plate × channel low/high
  range from `display_ranges.csv` as a plain linear mapping. These windows are
  fixed during QC review so every operator sees the upstream review
  presentation; the exact minimum and maximum remain visible beside the locked
  channel sliders without a separate explanatory banner. There is no competing
  dataset-range selector.
- **Review metrics.** A compact table contains only primary metrics registered
  as review rules. Stain rows come first in DNA, ER, AGP, RNA and Mito order;
  whole-field rows come last. Each row shows the site/raw value and its
  control-relative or median-relative review value when supplied. The UI calls
  `control_z` **Relative to controls** and explains that zero is typical of
  EMPTY controls while negative and positive values are lower and higher. The
  dictionary question moves into a focusable information button. When the same
  metric repeats across channels, only its first row carries that button. The
  first tooltip opens below its icon so the table boundary cannot clip it. Otsu and
  unrelated context or attribute rows are not part of the review surface.
  Internal rule-family names are translated out of both labels and questions.
- **Well site cues.** A well opened from the active QC results names its exact
  flagged sites and links directly to each one. A review-list well that does
  not identify sites says so explicitly.
- **Annotation form:**
  - scope is the current field or whole well; channel is All or one stain;
  - label, confidence (low, medium or high), and an optional note, then Save;
  - current annotations show time, export id and mode, each with Retract.
- **Keys:** Left Arrow and Right Arrow move to the previous and next queue
  entry unless focus is in an interactive editing control.

### 4.7 Review queues

- **Review list.** "Review queue" opens the first entry of `review_list.csv`
  in file order. A field entry opens its site. A well entry opens site 1 and
  retains the site stepper. Each queue entry carries the readable metric and
  channel that caused the review. The viewer shows that reason prominently,
  followed by the QC panel and a three-part review bar: progress on the left,
  the flag reason in the middle, and Previous/Next on the right. It also shows how many
  entries already have a current annotation for the same scope and target.
  Target identity includes both the numeric plate and its barcode, so a later
  export cannot inherit reviews from a different plate that reused the number. An
  All-channel annotation covers a stain entry; a stain annotation does not
  mark an All-channel composite entry.
- **Session queue.** "Review these" does the same for the current Sites or Wells
  table rows, in their displayed order, preserving the active review reason or
  reasons. Well entries also retain the exact sites that matched the active
  review rules. One compact, validated queue is stored in `sessionStorage`; repeated
  reason strings are deduplicated so all 31,104 fields remain within a
  conservative browser quota. Stale export, collection, or queue identities
  fail safely back to QC.

### 4.8 Annotations: the one write

- **Files.** The server writes only into the configured annotations folder,
  which the operator creates. Per reviewer it writes
  `annotations_<reviewer>.jsonl`.
- **Reviewer.** The reviewer is the user running the data server, reduced to `[A-Za-z0-9_-]`. No part of a file path comes from a request.
- **Append only.**
  - Open with `O_WRONLY | O_APPEND | O_CREAT` and write each record as one line in a single `write`, followed by `fsync`.
  - Never truncate, rewrite, rename or delete.
  - A process-wide lock keyed by canonical writer file serialises writes, even
    when more than one configured store resolves to that file.
- **Annotation record:** `schema_version` 1, `record` "annotation", `id`
  (UUID), `time` (UTC, ISO 8601), `reviewer`, `export_id`, `registry_hash`,
  `scope` (`field` or `well`), `plate`, `barcode`, `well`, nullable `site`,
  `channel` (a stain name or "all"), `label`, `confidence`, `note`, `blinded`
  and `app_version`. Core-view writes have `blinded=false`; the server derives
  the value from request mode.
- **Retraction:** `record` "retraction", carrying the `id` it retracts. The
  latest unretracted record per reviewer, scope, plate and barcode, field or
  well, and channel is current.
- **Labels:** the ordered identifiers and their descriptions come from
  `labels.csv`; the browser has no second hard-coded list.
- **Validation.** The label must be in the active export registry, the channel a name in
  `channels.csv` or "all", and the field or well must exist in the export.
  Field scope requires a site; well scope does not accept one. Confidence must
  be one of the three values, and the note at most 2,000 characters. Anything
  else returns 422 and writes nothing.
- **Reading.** Read every `*.jsonl` file in the folder. Skip malformed lines, but count them and report them in the status.
- **Requests** go through the connector like the existing POST routes, with the same client header.

### 4.9 Tests

**Fixture export.** Extend `scripts/make_test_data.py` to write `test-data/qc-export/`, following the real structure in `QC_EXPORT.md` and covering the idr0080-like fixture plates. It includes:

- site-review, context, panel, key, review and retired rows, plus dropped and
  unrecognised statuses;
- primary scales, automatic markers, and one-sided default flag expressions;
- hidden columns, including ten hidden review-list columns;
- the ordered `labels.csv` annotation registry;
- dictionary-described triage values and plain-linear display ranges;
- field- and well-scope review entries with entry ids and review groups;
- a `channels.csv` with AGP in slot 3 and RNA in slot 4;
- a `version.json` with correct hashes.

Add a second fixture export with a different metric set.

**Server tests:**

- **What is shown.** Core responses contain all export columns. UI metrics and
  attributes follow layer, primary, gate and status dynamically. Retiring or
  adding a row in a copy changes the result on reload.
- **Blind response mode.** Scan every endpoint's `blind=1` JSON and prove that
  hidden columns and the section 4.11 attributes are absent. The corresponding
  core response retains them.
- **Gates.** Automatic markers are read but never recomputed; review defaults
  use the export's strict one-sided expression; none-gated filters never flag
  or queue a site.
- **Validation.** Keys, coverage and member names are checked, and `channels.csv` is compared with the profile.
- **Replacement.** A hash mismatch keeps the previous export, and replacing the folder during a run switches cleanly.
- **Annotations:**
  - writing is append-only, with earlier bytes unchanged;
  - validation errors write nothing;
  - retraction and the current-record rule work;
  - malformed lines are skipped;
  - nothing is written outside the folder;
  - a missing or read-only folder is handled.
- **Switching exports.** Pointing the profile at the second export changes what is shown, with no code change.

**End-to-end tests:**

- the QC link appears only for Cell Health;
- filters are built from primary dictionary rows, with automatic markers and
  review defaults;
- the metric dropdown uses readable labels, the overview exposes one measure at
  a time, and secondary filters and columns remain behind disclosures;
- moving a slider updates overall and per-slider counts to known fixture values;
- a triage click filters the table;
- all nine plate maps and the Sites/Wells tables follow the current filters;
- categorical filters, sorting and URL restoration work;
- tables and queues name the metric that flagged each row, and the field view
  keeps that reason prominent while showing only review metrics, fixed contrast
  bounds and the annotation workflow;
- both site and well queues move forward and back, including keyboard keys;
- saving and retracting field- and well-scope annotations works;
- every QC endpoint redacts its fixture-hidden data under `blind=1`;
- TIMED is unchanged.

**Real-export check.** An opt-in test loads `docs/data/qc-export/` without images and checks its structure, the dictionary, hidden columns and counts.

### 4.10 Arrhenius check (A2) and pull request

Status: passed manually on Arrhenius on 2026-11-04 with the final development
build `0.5.0.dev12`. The acceptance record is in `docs/arrhenius.md`.

**Operator, before A2:** create the annotations folder:

```bash
mkdir -p /nobackup/proj/disk/naiss2024-5-630/personal/nilsdu/scratch/qc_review/annotations
```

**Operator, A2:**

Before connecting, disconnect with job cancellation enabled and start a fresh
Slurm job. Do not reuse an existing job. Confirm the handshake reports the
expected 0.5.0 development version.

1. The QC page shows the export id from the new `version.json`.
2. The collapsed triage overview, all nine plate maps, readable Metric dropdown,
   core and More filters, Sites/Wells toggle and live counts behave as expected.
   Annotation status is under More filters. Plate shading has a numeric legend
   and its information button explains that the badge is the exact flagged-site count.
3. A triage cell narrows the overview, maps and table to its plate and channel.
4. The compact table names **Flagged by** and expands its additional columns on
   request. The overview calls the operator measure **Automatic exclusion**.
5. Site and well views put **Flagged by** first. The compact table shows only
   review metrics, channel rows before whole-field rows, site/raw and
   comparison values, and one information button per unique metric. The plain-linear QC display
   ranges are fixed and their exact channel minima/maxima are visible. A well
   starts at site 1, names any flagged sites supplied by the queue, and steps
   through all sites.
6. Both queues preserve order and move with buttons and Left/Right Arrow keys.
   The review bar has progress, flag reason and navigation in three columns and
   no duplicate Back to QC action.
7. Annotate fields and wells and retract one annotation. On Arrhenius, inspect
   the annotation file and check the records, unblinded mode and export id.
   Reload and see the current annotations in the website.
8. Record QC page load and metric-control response timings in `docs/arrhenius.md`.

Then set version 0.5.0 and give the operator the PR text.

### 4.11 Blinded mode after Part B (later release)

Build the user-facing blinded mode only after 0.5.0 is merged, in its own PR.

- Add a switch on the QC page and viewer, off by default. When enabled, every
  QC request uses `blind=1`.
- The server removes all export columns marked hidden plus Phase 1 perturbation
  identity, well type and control attributes, D62 state, protection, reference
  membership, position risk, automatic-rule markers and viewer header facts.
- Blinded counts include only fields and wells.
- The blinded review queue exposes only `entry_id` and `review_group` plus the
  data required to open the image. A channel entry opens that stain in
  grayscale; a site entry opens the colour composite. The thumbnail shows the
  whole field; selecting a spot opens native 1:1 resolution.
- Annotations created through blinded requests record `blinded=true`.

## 5. Part C: segmentation outlines (0.6.0)

Build it as specified in the old brief's section 5.1 (mask stores and outlines) and Appendix D (the verified contract), with these changes:

- **No `qc.parquet`.** The segmentation panel's counts ("731 nuclei · 616 cells") come from the masks themselves: labels run from 1 to N, as verified.
- **QC field view.** The QC field view gets the outline toggles too.
- **Milestones:** M1 (server) and M2 (viewer), with the old brief's mask fixtures and tests.
- **Operator check.** On plate 14610, well A01, site 1:
  - 731 nucleus outlines and 616 cell outlines align with the image;
  - the stock model is offered only on 14610;
  - outline timings are recorded.

Then set version 0.6.0 and give the operator the PR text.

## 6. Order of work

1. **Part A** (separate prompt): operator check, PR, merge.
2. **Part B:**
   1. Write `QC_EXPORT.md`, and stop if anything is unclear.
   2. Configuration and loading.
   3. Endpoints and the server-side `blind=1` response contract.
   4. Overview, plate maps, Sites/Wells tables and filters.
   5. Review in the viewer.
   6. Queues and annotations.
   7. Operator check (A2), version 0.5.0, PR and merge.
3. **Blinded mode (later release):** add the QC/viewer switch and
   blinded image-review interaction in a separate PR.
4. **Part C:** operator check, PR, merge.
