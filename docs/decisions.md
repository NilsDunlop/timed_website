# Decision log

## 2026-10-05: preserve the brief's architecture

The prototype keeps the browser, local connector, and remote data server as
separate seams. The browser uses same-origin `/connector` and `/backend` routes;
the connector owns credentials and tunnelling; the data server owns restricted
OME-Zarr access. This directly supports a later Tauri sidecar without coupling
the first web prototype to Tauri.

## 2026-10-05: adapt bare TIMED arrays through the data server

M1 disproved the brief's assumption that each run is an OME-Zarr plate. The
eight outer `.zarr` directories are plain collection folders, not Zarr groups.
Each contains one bare Zarr v3 array per field, named `<well>_s<site>.zarr`, plus
metadata sidecars. The field arrays have no OME namespace, `multiscales`, OMERO
metadata, `dimension_names`, or pyramid. All 4,032 fields share one signature:

- shape `(48, 3, 2500, 2500)` and documented axes `(t, c, y, x)`;
- `uint16`, channels 405/505/625 nm, and 0.3 micrometres per pixel;
- logical chunks `(1, 1, 625, 625)` inside one whole-array
  `(48, 3, 2500, 2500)` shard;
- little-endian bytes plus Blosc Zstd level 5 with byte shuffle; and
- one resolution level.

The section 4 architecture remains unchanged. The data server additionally
recognize a `timed_collection` dataset and expose a read-only synthetic OME-Zarr
metadata facade for each bare field. The facade supplies axes and channel labels
to Viv and maps array metadata/chunk requests to the existing field store. It
does not alter, convert, or copy the remote data. Range requests remain essential
because the sharding index and compressed chunks live within a single shard.

Synthetic `test_plate.zarr` mirrors the collection-plus-bare-array layout,
axes, dtype, single resolution, whole-array sharding, codecs, and wavelength
channel labels at small dimensions. Its `(1, 1, 128, 128)` chunks preserve the
real data's four-by-four spatial chunk grid at 512 by 512 pixels. Separate
standard OME-Zarr 0.4/Zarr v2 and OME-Zarr 0.5/Zarr v3 images keep both native
paths covered. Because a real plane is 2500 by 2500 pixels, the single-resolution
data fits below the 4096 by 4096 texture limit noted in the brief.

## 2026-10-05: keep the TIMED facade metadata-only

M3 implements each bare field as a virtual OME-Zarr 0.5 group at
`<field>.zarr`, with a virtual `s0/zarr.json` array metadata document.
`dimension_names`, physical scale, and channel display metadata are derived
from the verified inventory and conversion notes. Requests below `s0/` map to
the corresponding original path below the bare field, so compressed shard
bytes are never rewritten or copied.

All native OME-Zarr files are served unchanged. Dataset summaries read plate
metadata plus only the first well and first image initially; remaining well
metadata is loaded through the well endpoint on demand. Dataset paths are
validated through one real-path guard before linking or reading, and the JSON
registry and runtime connection file use same-directory atomic replacement.

## 2026-10-05: isolate SSH side effects behind one session backend

M4 keeps the public session state free of credential answers and data-server
tokens by construction. AsyncSSH callbacks await an in-memory prompt future;
the connector's private backend alone holds the connection-file token. Host
keys are checked against the user's read-only `~/.ssh/known_hosts` plus TIMED's
own file. Unknown keys require explicit fingerprint confirmation before being
appended to the TIMED file; host-key checking is never disabled.

The connector uses the same state engine for HTTP and terminal flows. Its HPC
backend owns versioned wheel installation, Slurm submission and polling, SFTP
connection-file reads, port forwarding, and cleanup. A backend protocol allows
deterministic cross-platform state tests, while the complete real backend is
tested through the POSIX AsyncSSH/Slurm harness under WSL2.

## 2026-10-05: keep the browser typed and format-aware

M6 generates and commits TypeScript schemas from both FastAPI applications,
then calls them through `openapi-fetch`. The connector's opaque `/backend`
proxy and schema-hidden HEAD handlers are intentionally excluded from OpenAPI:
their payloads are defined by the remote data server and Zarr store rather than
the connector.

The viewer pins Viv 0.23, deck.gl 9.4.0, and luma.gl 9.4.2. OME-Zarr 0.4/Zarr
v2 uses Viv's URL loader. OME-Zarr 0.5/Zarr v3, including the virtual TIMED
field facade, uses a small same-origin Zarrita store. The store suppresses
Zarrita's v2 discovery probes for known v3 data, so valid v3 loads do not emit
misleading 404 console errors. Zarr resources support both GET and HEAD because
the v3 sharding codec asks for object length before fetching byte ranges.

The end-to-end browser suite owns an isolated SSH/Slurm harness and connector,
walks all three pages, renders the TIMED plate plus both native fixture formats,
checks nonuniform pixels and time-dependent frame changes, rejects browser
console errors, disconnects, and verifies the Slurm queue is empty.

## 2026-10-05: make local mode a real backend, not simulated Slurm

M8 dispatches `local` profiles to a dedicated `LocalBackend`. It launches the
same authenticated `timed-dataserver` package as a child process bound to
`127.0.0.1` on a dynamic port, reads its atomic connection file, performs the
same versioned handshake, and gives the existing reverse proxy its loopback
target. Local mode therefore exercises the production data server and browser
without introducing SSH, fake jobs, or a second API.

The token-bearing connection file uses a private temporary directory which is
removed on every cleanup path. The non-secret linked-dataset registry persists
separately in `~/.timed/local`. Child output feeds the existing 200-line session
log, unexpected exit moves the public state to `error`, and disconnect always
terminates the process. The end-to-end harness gives local mode an isolated
registry and verifies plate rendering, a changed time frame, clean browser
console output, and process shutdown alongside the unchanged SSH/Slurm flow.

## 2026-10-05: join experiment metadata at the plate boundary

Experiment metadata remains beside the image tree and is configured through an
optional profile `metadata_root`. A linked dataset name identifies the matching
per-plate CSV and acquisition JSON; canonical well IDs then join treatment rows
to the OME-Zarr plate. The server reads those two bounded files with the Python
standard library rather than deploying PyArrow or loading the consolidated
Parquet table.

The browser treats compound and dose as one treatment assignment: the compound
filter displays its associated dose instead of introducing a separate global
dose category. Mechanism classes supply a small stable color palette, controls
remain visually distinct, and filtering dims non-matching wells. Selecting a
well exposes compound, dose, mechanism, target, and pathway alongside concise
plate and acquisition information in both the plate browser and image viewer.

The raw inspection report is local-only because it contains environment paths
and low-level instrument details. Only its high-level structure, join rules, and
sanitized synthetic fixture are committed.

## 2026-10-06: optimize the real TIMED time axis without preview images

The viewer keeps the original uint16 TIMED arrays as its only image
representation. Single-resolution fields use Viv's lower-level detail view so
`excludeBackground` can retain the last complete frame while the next frame is
loading. Requested, rendering, and displayed timepoints are tracked separately:
rapid slider input is debounced for 150 ms, and the status and footer change to
the new time only after Viv reports that its viewport is complete.

A 128 MiB least-recently-used cache sits below Viv at the decoded-tile boundary.
It keys tiles by dataset, field, resolution, spatial tile, and non-spatial
selection. Concurrent consumers share one load; cancelling a stale foreground
request cannot cancel a still-useful prefetch. Once a frame is complete, TIMED
prefetches the adjacent frame after a short idle delay with bounded concurrency
and follows the user's direction through the trajectory. Three-channel fields
fit roughly three complete decoded frames in the current budget.

The connector owns one bounded HTTP client for its whole lifespan so range
requests reuse upstream TCP connections. Zarr resources return private
one-hour freshness, strong ETags, Last-Modified for files, conditional 304
responses, and If-Range handling. These headers may be stored only by the
user's private browser cache; the bearer token remains connector-only.

The approach deliberately does not generate preview pyramids or videos. It
improves interactive access to the authoritative arrays and leaves a later
preview representation as an independent option if remote measurements still
justify one.
