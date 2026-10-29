# Cell Health QC export reference

This document records the structure of the Cell Health QC export used for
TIMED Part B. The inspected snapshot has export id `2e009e837cd1`, definitions
version 3, and 31,104 fields. The repository copy under
`docs/data/qc-export/` is local-only and gitignored; it must never be committed.

The export is replaced as one folder. TIMED reads it as an immutable snapshot,
validates the hashes in `version.json`, and never writes into it.

## Core and blinded data boundaries

`columns.csv` marks each described data column as `show` or `hide`. The snapshot
contains 63 shown dictionary rows and 24 hidden rows. Hidden columns are
described here only by count:

- `site_metrics.csv` has 16 hidden columns: five key columns and eleven context
  columns.
- `review_list.csv` has eight hidden columns.

The 0.5.0 core QC view loads and serves the complete export. A request without
`blind=1` may therefore include both `show` and `hide` rows and attributes. The
server still treats the export as read-only.

Every QC endpoint also accepts `blind=1`. In that mode the server removes all
columns marked `hide` and removes the additional attributes defined by the
blinded-view contract before constructing the response. Redaction happens on
the server; the browser never receives redacted data. The 0.5.0 core UI does
not request blinded responses. The switch that does so belongs to a separate
0.5.1 pull request after the core release is merged.

Real hidden names and values remain absent from logs, error messages, fixtures,
commits, and documentation. This reference records them only by count even
though the unblinded core API is allowed to serve them at runtime.

## Files at a glance

| File | Rows | Columns | Public structure |
| --- | ---: | ---: | --- |
| `site_metrics.csv` | 31,104 | 69 | 53 shown columns and 16 hidden columns; one row per field |
| `columns.csv` | 87 | 12 | Dictionary for `site_metrics.csv` and `review_list.csv` |
| `channels.csv` | 5 | 14 | One row per stain, joining packed and Phenix identities |
| `review_list.csv` | 158 | 18 | 10 shown columns and eight hidden columns |
| `triage_plate_channel.csv` | 54 | 10 | Nine plates by five stains plus the field-level `site` channel |
| `display_ranges.csv` | 45 | 9 | Nine plates by five stains |
| `version.json` | one object | seven top-level keys | Export identity, registry identity, field count, and six file hashes |

PyArrow infers integer identifiers as `int64`, measurements as `double`, the
triage rule flag as `bool`, and other CSV fields as `string`. Blank CSV cells
become nulls where applicable.

## `site_metrics.csv`

There is exactly one row per `(plate, well, site)`. The snapshot covers plates
14610–14618, wells A01–P24, and sites 1–9. The Phenix site is the packed site.

### Dictionary-shown key columns

| Column | Type | Meaning |
| --- | --- | --- |
| `experiment` | string | IDR experiment |
| `cell_line` | string | A549, ES2, or HCC44 |
| `plate` | int64 | Numeric plate id |
| `plate_barcode` | string | `SQ000<plate>` |
| `well` | string | Canonical well address |
| `row` | string | Plate row |
| `column` | int64 | Plate column |
| `site` | int64 | Field id and acquisition order within the well |
| `site_position` | string | Position in the 3 × 3 field mosaic |
| `member_DNA` | string | Packed DNA member name |
| `member_ER` | string | Packed ER member name |
| `member_AGP` | string | Packed AGP member name |
| `member_RNA` | string | Packed RNA member name |
| `member_Mito` | string | Packed Mito member name |

The five member names are validated against the packed catalogue and the slots
declared by `channels.csv`.

### Dictionary-shown context columns

| Columns | Type | Dictionary meaning |
| --- | --- | --- |
| `nuclei_final` | int64 | Final nucleus count and field density context |
| `qc_release_delivery` | string | Acquisition delivery and TIFF-writer context |
| `qc_sbr_otsu__<channel>__raw` | double | Raw foreground-to-background signal ratio for each stain |
| `qc_sbr_otsu__<channel>__control_z` | double | Control-standardised signal ratio for each stain |

`<channel>` expands in packed slot order to DNA, ER, AGP, RNA, and Mito. The
remaining eleven context columns are hidden and are not identified here.

### Dictionary-shown site-review columns

All site-review values are `double`. Their names follow
`<metric>__<channel>__<scale>`; `site` denotes a field-level metric.

| Metric | Channel expansion | Scales | `worse` | `gate` | Status |
| --- | --- | --- | --- | --- | --- |
| `focus_score_normvar` | DNA | `raw`, `ratio` | `low` | `automatic` | `registered` |
| `qc_acq_z_well_dev_um` | site | `raw`, `plate_z` | `both` | `none` | `kept` |
| `qc_acq_z_residual_um` | site | `raw`, `plate_z` | `both` | `none` | `kept` |
| `qc_focus_radial_ratio` | all five stains | `raw`, `control_z` | `low` | `review` | `pending_review` |
| `qc_plateau_pct` | all five stains | `raw` | `high` | `none` | `kept` |
| `qc_coincident_bright_pct` | site | `raw` | `high` | `review` | `pending_review` |
| `qc_bright_area_pct` | all five stains | `raw` | `high` | `none` | `kept` |

This produces 27 shown site-review columns. The current snapshot has no
dictionary rows with a `panel` layer. Under the next-export contract,
`site_review` rows supply per-field sliders, `context` rows are sortable without
sliders, and `panel` rows describe plate- or well-level views.

## `columns.csv`

| Column | Type | Observed values or role |
| --- | --- | --- |
| `file` | string | `site_metrics.csv`, `review_list.csv` |
| `column` | string | Column described by the row |
| `metric` | string | Stable metric id, or blank for structural rows |
| `version` | nullable int64 | Metric version, or blank for structural rows |
| `channel` | string | blank, DNA, ER, AGP, RNA, Mito, `site` |
| `scale` | string | blank, `raw`, `ratio`, `plate_z`, `control_z` |
| `worse` | string | blank, `low`, `high`, `both`, `context` |
| `layer` | string | `key`, `context`, `site_review`, `review` |
| `gate` | string | blank, `automatic`, `none`, `review` |
| `status` | string | blank, `registered`, `kept`, `pending_review` |
| `question` | string | One-sentence description or review prompt |
| `blind` | string | `show` or `hide` |

`worse` is the API's direction: `low`, `high`, or two-sided `both`; `context`
describes a non-slider context value. Layers have these roles:

- `site_review`: per-field metrics with sliders;
- `context`: values shown and sortable beside slider metrics, without sliders;
- `panel`: plate- or well-level views;
- `retired`: never shown;
- `key`: identifiers; and
- `review`: review-list columns.

The gate controls slider behaviour:

- `automatic` is decided upstream by a registered rule. TIMED does not
  recompute or change that rule; registered thresholds appear as markers. The
  current automatic decision is D62 on `focus_score_normvar`.
- `review` is a flag slider. Its exported default bounds define the initial
  rule, with a blank bound meaning one-sided. A value is flagged beyond the
  slider, and the slider reports its own flagged count.
- `none` is a filter slider that starts fully open, never flags a value, and
  never contributes to a review queue.

Filter ranges combine with AND. When one or more flag sliders are active, a row
must also be beyond at least one active flag rule.

`pending_review` is shown with a badge. Rows with `retired`, `dropped`, or an
unrecognised status are hidden; unrecognised statuses are reported by the QC
status endpoint. Blank status is valid for structural dictionary rows.

The next export adds these dictionary columns:

| Column | Role |
| --- | --- |
| `primary` | Selects one slider column per metric and channel; 19 are expected |
| `default_flag_lo`, `default_flag_hi` | Initial flag bounds; either may be blank for a one-sided rule |
| `markers` | Registered upstream thresholds displayed on automatic sliders |

Each primary slider also displays the corresponding raw value. The current
snapshot predates these columns and has no `panel`, `retired`, or `dropped`
rows.

## `channels.csv`

| Column | Type |
| --- | --- |
| `channel`, `stain`, `dye`, `dye_source`, `target` | string |
| `packed_slot`, `phenix_channel_id` | int64 |
| `packed_member_suffix`, `phenix_channel_name`, `idr_file_token` | string |
| `excitation_nm`, `emission_nm`, `exposure_s` | double |
| `identity_evidence` | string |

The identity mapping is:

| Channel | Packed slot | Member suffix | Phenix / IDR channel | Exposure |
| --- | ---: | --- | ---: | ---: |
| DNA | 1 | `_ch1.tiff` | 1 | 0.02 s |
| ER | 2 | `_ch2.tiff` | 2 | 0.02 s |
| AGP | 3 | `_ch3.tiff` | 4 | 0.02 s |
| RNA | 4 | `_ch4.tiff` | 3 | 0.01 s |
| Mito | 5 | `_ch5.tiff` | 5 | 0.02 s |

This must agree with the Cell Health code profile before QC is enabled.

## `review_list.csv`

The queue has 158 rows in file order. Its shown columns are:

| Column | Type | Meaning |
| --- | --- | --- |
| `scope` | string | `field` or `well` |
| `plate` | int64 | Plate id |
| `cell_line` | string | Cell line |
| `well` | string | Well address |
| `site` | nullable int64 | Site for a field entry; blank for a well entry |
| `channel` | string | Stain name or `site` |
| `metric` | string | Metric behind the queue entry |
| `metric_version` | int64 | Metric version |
| `value` | double | Metric value |
| `unit` | string | Display unit |

The other eight columns are marked hidden. The core queue preserves file order
and may use all 18 columns. With `blind=1`, the server returns no hidden columns
and reduces each queue entry to its blinded identity and grouping. The next
export adds the public `entry_id` and `review_group` columns required for that
blinded response.

A field-scoped row opens its site. A well-scoped row opens site 1 and provides
the normal site stepper for the other eight sites.

## `triage_plate_channel.csv`

The 54 rows form nine plates by six channel values: the five stains plus
`site` for field-level summaries.

| Column | Type |
| --- | --- |
| `plate`, `channel` | int64, string |
| `share_qc_focus_radial_ratio_v2` | double |
| `share_qc_coincident_bright_pct_v1` | double |
| `share_d62_automatic` | double |
| `share_d62_review` | double |
| `panel_cv_share_wells_over_15pct` | double |
| `panel_cv_rule_flag` | nullable bool |
| `panel_flatfield_max_over_min` | double |
| `panel_plateau_share` | double |

Cells are null where a measure does not apply to that channel value. The next
export adds dictionary rows for this file. Those rows provide labels and legend
metadata; the website does not hard-code them.

## `display_ranges.csv`

There is one row for every plate and stain.

| Column | Type | Meaning |
| --- | --- | --- |
| `plate` | int64 | Plate id |
| `plate_barcode` | string | `SQ000<plate>` |
| `channel` | string | Stain name |
| `display_lo`, `display_hi` | double | Fixed display bounds |
| `display_mode` | string | `fixed per plate x channel` in this snapshot |
| `quantile_lo`, `quantile_hi` | double | 0.01 and 0.995 provenance quantiles |
| `reference_fields` | int64 | 108 in every row |

`fixed per plate x channel` means a plain linear mapping between `display_lo`
and `display_hi`. The two quantiles and reference-field count are provenance
shown beside the range, not an additional image transform.

## `version.json`

The current object contains:

| Key | Type | Current role |
| --- | --- | --- |
| `id` | string | Export id `2e009e837cd1` |
| `label` | string | Human-readable snapshot label |
| `created_utc` | ISO 8601 string | Export creation time |
| `definitions_version` | integer | Dictionary definitions version, currently 3 |
| `fields` | integer | Expected field count, currently 31,104 |
| `registry_sha256` | SHA-256 string | Registry identity |
| `files` | object | Six required CSV filenames mapped to their SHA-256 digests |

The loader verifies every entry in `files` before publishing a snapshot. API
names such as `export_id` and `registry_hash` are derived from `id` and
`registry_sha256`; the source JSON keys remain unchanged.

## Core-view joins and derived state

The core 0.5.0 view combines this export with Phase 1 well metadata. Controls
come from the Phase 1 EMPTY-control classification. Protection and the other
export attributes remain export-defined; the browser treats them as
dictionary-driven categorical data rather than hard-coding their encoding.

Site and well tables expose every export attribute in the unblinded core view.
The Wells table aggregates the nine sites: it shows the flagged-site count and
the worst site value for each active slider. Both tables carry annotation
status. Live counts report fields, wells, controls, and fields under protection both
overall and per active flag slider.

Annotations are the only write. They may have field or well scope and record
their label, confidence, note, export identity, and whether the request used
`blind=1`. The core view therefore writes `blinded=false`. Version 0.5.0 has no
rule-proposal endpoint or proposal file.

The annotations folder is separate from every data root and export. The server
does not create it. It appends one JSON object per line to the fixed
`annotations_<reviewer>.jsonl` file derived from the server's operating-system
user, using one write and `fsync` under a process-wide per-file lock. Reads fold
all regular JSONL files into the latest unretracted record per reviewer,
target scope, target identity, and channel; malformed lines are skipped and
reported only by count.

Table annotation status uses exact scope: site rows use field records and well
rows use whole-well records. Queue counts also require the same scope and
target, including both the numeric plate and barcode. This prevents a later
export from attaching an old review to a different plate that reused the
number. An All-channel record covers a stain-specific queue entry, while a
stain-specific record does not mark an All-channel composite entry. The
review-list queue preserves CSV order. "Review these" preserves current table
order in one validated `sessionStorage` value; it does not modify the export.

## Next-export additions

The current snapshot is sufficient for configuration, base loading and
validation, the annotation store, and features that do not depend on the new
dictionary contract. The replacement export will add:

- `primary`, `default_flag_lo`, `default_flag_hi`, and `markers` to
  `columns.csv`;
- dictionary rows for `triage_plate_channel.csv`; and
- `entry_id` and `review_group` to `review_list.csv`.

Fixtures for 0.5.0 follow that next-export contract. The real-data A2 check must
use the replacement export rather than this older snapshot.
