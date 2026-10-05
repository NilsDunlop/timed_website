# TIMED experiment metadata

The metadata tree is separate from the image stores. The production integration
uses two source types:

- `csv/<plate>.csv` contains one row per well with experiment, treatment, and
  biological annotation fields.
- `acquisition/<plate>.json` contains the matching plate acquisition structure,
  including plate geometry, time course, sites, and enabled channels.

The inspected Arrhenius tree contains eight plates and 2,016 well rows. Plates
P1.1 through P1.4 contain 308 wells each; P2.1 through P2.4 contain 196 wells
each. Each image dataset stem matches the acquisition `plate_name`. Its plate
ID selects the CSV filename, the CSV `plate_barcode` is validated against the
dataset barcode, and rows join through a canonical well identifier such as
`A01`.

## Fields used by TIMED

The plate view exposes a concise experiment summary:

- plate ID, barcode, experiment date, project, and replicate;
- cell line, cell modification, passage, seeding density, and dye program; and
- plate model, timepoint count and interval, sites per well, site spacing,
  autofocus state, bottom offset, and enabled channel names.

The well view exposes:

- well type and control status;
- compound and its associated dose in micromolar;
- mechanism-of-action class;
- target, pathway, and detailed mechanism annotation.

Compound and dose are presented together because dose belongs to the compound
assignment rather than acting as an independent plate-wide category. Plate wells
can be filtered by compound, mechanism class, or control/treatment status. Color
is reserved for mechanism classes, with a separate neutral treatment for
controls.

## Runtime loading

Profiles can set an optional `metadata_root`. The data server reads only the
matching per-plate CSV and acquisition JSON when a linked plate requests
`/api/datasets/{dataset_id}/metadata`. It uses the Python standard library, so
the deployed server does not require PyArrow or load the consolidated Parquet
file. Missing metadata is reported as unavailable and does not prevent image
browsing.

The full local inspection report is intentionally gitignored. It contains
environment-specific paths and low-level instrument settings that are not
needed to describe or test the integration. Synthetic metadata under
`test-data/metadata` covers the committed schema and UI behavior.
