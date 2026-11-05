# Cell Health QC export reference

This document records the structure of the Cell Health QC export used for
TIMED Part B. The inspected snapshot has export id `d77939bbbf42`, definitions
version 3, and 31,104 fields. The repository copy under
`docs/data/qc-export/` is local-only and gitignored; it must never be committed.

The export is replaced as one folder. TIMED reads it as an immutable snapshot,
validates the hashes in `version.json`, and never writes into it.

## Core and blinded data boundaries

`columns.csv` marks each described data column as `show` or `hide`. The snapshot
contains 37 shown dictionary rows and 65 hidden rows. Hidden columns are
described here only by count:

- `site_metrics.csv` has 55 hidden columns.
- `review_list.csv` has ten hidden columns.
- `triage_plate_channel.csv` has no hidden columns.

The 0.5.0 core QC view loads and serves the complete export. A request without
`blind=1` may therefore include both `show` and `hide` rows and attributes. The
server still treats the export as read-only.

Every QC endpoint also accepts `blind=1`. In that mode the server removes all
columns marked `hide` and removes the additional attributes defined by the
blinded-view contract before constructing the response. Redaction happens on
the server; the browser never receives redacted data. The 0.5.0 core UI does
not request blinded responses. The switch that does so belongs to a separate
later pull request.

Real hidden names and values remain absent from logs, error messages, fixtures,
commits, and documentation. This reference records them only by count even
though the unblinded core API is allowed to serve them at runtime.

## Files at a glance

| File | Rows | Columns | Public structure |
| --- | ---: | ---: | --- |
| `site_metrics.csv` | 31,104 | 69 | 14 shown columns and 55 hidden columns; one row per field |
| `columns.csv` | 102 | 16 | Dictionary for site, review-list and triage columns |
| `channels.csv` | 5 | 14 | One row per stain, joining packed and Phenix identities |
| `labels.csv` | 14 | 2 | Ordered annotation labels and operator guidance |
| `review_list.csv` | 178 | 23 | 13 shown columns and ten hidden columns |
| `triage_plate_channel.csv` | 54 | 10 | Nine plates by five stains plus the field-level `site` channel |
| `display_ranges.csv` | 45 | 9 | Nine plates by five stains |
| `version.json` | one object | seven top-level keys | Export identity, registry identity, field count, and seven file hashes |

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

### Hidden metric and attribute columns

The other 55 columns are dictionary-described metrics and attributes. Their
names and values are intentionally not reproduced here. The unblinded core view
uses their labels, questions, scales and rules from `columns.csv`; a `blind=1`
response removes them before serialisation.

## `columns.csv`

| Column | Type | Observed values or role |
| --- | --- | --- |
| `file` | string | `site_metrics.csv`, `review_list.csv`, `triage_plate_channel.csv` |
| `column` | string | Column described by the row |
| `label` | nullable string | Operator-facing label when the source supplies one |
| `metric` | string | Stable metric id, or blank for structural rows |
| `version` | nullable int64 | Metric version, or blank for structural rows |
| `channel` | string | blank, DNA, ER, AGP, RNA, Mito, `site` |
| `scale` | string | blank, `raw`, `ratio`, `plate_z`, `control_z` |
| `primary` | bool | Selects one slider column per metric and channel |
| `default_flag` | nullable string | Initial one-sided rule as `< threshold` or `> threshold` |
| `markers` | nullable string | Registered upstream thresholds for automatic rules |
| `worse` | string | blank, `low`, `high`, `both`, `context` |
| `layer` | string | `key`, `context`, `site_review`, `review`, `triage`, `panel` |
| `gate` | string | blank, `automatic`, `none`, `review` |
| `status` | string | blank, `registered`, `kept`, `pending_review` |
| `question` | string | One-sentence description or review prompt |
| `blind` | string | `show` or `hide` |

`worse` is the API's direction: `low`, `high`, or two-sided `both`; `context`
describes a non-slider context value. Layers have these roles:

`control_z` is an upstream control-relative standardised score using the
EMPTY-control population. Zero is the producer's control centre; negative and
positive values lie below and above it. The export does not encode whether the
producer used a conventional or robust estimator, and TIMED does not recompute
the value.

- `site_review`: per-field metrics with sliders;
- `context`: values shown and sortable beside slider metrics, without sliders;
- `triage`: structural plate and channel keys for the overview;
- `panel`: other plate- or well-level values;
- `retired`: never shown;
- `key`: identifiers; and
- `review`: review-list columns.

The gate controls slider behaviour:

- `automatic` is decided upstream by a registered rule. TIMED does not
  recompute or change that rule; registered thresholds appear as markers.
- `review` is a flag slider. Its exported `default_flag` is a strict `<` or `>`
  comparison and defines the initial one-sided rule. A value is flagged beyond
  the slider, and the slider reports its own flagged count.
- `none` is a filter slider that starts fully open, never flags a value, and
  never contributes to a review queue.

Filter ranges combine with AND. When one or more flag sliders are active, a row
must also be beyond at least one active flag rule.

`pending_review` is shown with a badge. Rows with `retired`, `dropped`, or an
unrecognised status are hidden; unrecognised statuses are reported by the QC
status endpoint. Blank status is valid for structural dictionary rows.

The snapshot contains 37 primary rows. Each primary slider also displays its
corresponding raw value when one is registered. Every non-key dictionary row
for `triage_plate_channel.csv` supplies an overview value; its `site_review` or
`panel` layer describes the value's role without a hard-coded metric list.

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

## `labels.csv`

The file is the authoritative ordered registry for annotation labels. It has a
`label` column containing a safe lowercase identifier and a `description`
column containing the guidance shown beside the annotation form. The current
registry is: `ok`, `blurred`, `partial_blur`, `saturated`, `debris`,
`bright_biological`, `dark_or_bubble`, `uneven_illumination`,
`dim_or_staining`, `noisy`, `sparse_biological`, `known_fixed_shadow`, `other`
and `unsure`.

The data server validates writes against this registry. The browser does not
keep a second hard-coded label list, and stored historical labels remain
readable even when a later export changes the registry.

## `review_list.csv`

The queue has 178 rows in file order. Its shown columns are:

| Column | Type | Meaning |
| --- | --- | --- |
| `entry_id` | string | Stable queue identity |
| `review_group` | string | Stable queue grouping |
| `scope` | string | `field` or `well` |
| `plate` | int64 | Plate id |
| `cell_line` | string | Cell line |
| `well` | string | Well address |
| `site` | nullable int64 | Site for a field entry; blank for a well entry |
| `channel` | string | Stain name or `site` |
| `metric` | string | Metric behind the queue entry |
| `metric_version` | int64 | Metric version |
| `unit` | string | Display unit |
| `crop_x`, `crop_y` | int64 | Suggested image review centre |

The other ten columns are marked hidden. The core queue preserves file order
and may use all 23 columns. With `blind=1`, the server returns no hidden columns
and reduces each queue entry to its blinded identity and grouping.

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

Cells are null where a measure does not apply to that channel value. Dictionary
rows for this file provide labels, questions, layers and rule metadata; the
website does not hard-code them.

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
| `id` | string | Export id `d77939bbbf42` |
| `label` | string | Human-readable snapshot label |
| `created_utc` | ISO 8601 string | Export creation time |
| `definitions_version` | integer | Dictionary definitions version, currently 3 |
| `fields` | integer | Expected field count, currently 31,104 |
| `registry_sha256` | SHA-256 string | Registry identity |
| `files` | object | Seven required CSV filenames mapped to their SHA-256 digests |

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

## Publication contract

This replacement snapshot is core-ready. The loader requires exactly the seven
hashed CSV files above, verifies their hashes before publication, validates the
dictionary and annotation registry, and publishes the snapshot atomically. A
partially replaced or unsupported folder never becomes the active QC export.
