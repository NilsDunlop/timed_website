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

A 128 MiB least-recently-used cache sits below Viv at the decoded-pixel
boundary and wraps both of Viv's access paths. Viv 0.23 renders a
single-resolution loader with `ImageLayer.getRaster`, so TIMED prefetches the
same per-channel rasters rather than display tiles; multiscale tile entries
remain keyed by dataset, field, resolution, spatial tile, and non-spatial
selection. Raster entries omit the spatial coordinates. Concurrent consumers
share one load, and an orphaned load receives a 300 ms adoption window so the
foreground renderer can take over an active prefetch after the 150 ms
time-slider debounce without restarting its byte ranges.

Once a frame is complete, TIMED prefetches the next destination on the selected
time-jump grid after a short idle delay with bounded concurrency and follows
the user's direction through the trajectory. It does not fetch skipped
intermediate frames or prefetch multiscale data without a viewport-aware
resolution policy. Three-channel fields fit roughly three complete decoded
frames in the current budget.

The connector owns one bounded HTTP client for its whole lifespan so range
requests reuse upstream TCP connections. Metadata and complete Zarr resources
return private one-hour freshness, strong ETags, Last-Modified for files,
conditional 304 responses, and If-Range handling. Byte-range requests bypass
the browser HTTP cache and 206 responses are marked private and non-storable.
This avoids Chromium sparse-cache failures when many ranges of one sharded
object are read concurrently. The small immutable shard suffix is instead
shared once in memory by the V3 store, and decoded pixels remain protected by
TIMED's bounded pixel cache. The bearer token remains connector-only.

The approach deliberately does not generate preview pyramids or videos. It
improves interactive access to the authoritative arrays and leaves a later
preview representation as an independent option if remote measurements still
justify one.

## 2026-10-06: make cross-experiment comparison explicit and additive

The data browser keeps up to four user-selected wells in a comparison tray while
the active linked dataset changes. Each tray entry owns its dataset identity,
well, available sites, and chosen field, so identical paths such as
`B01_s1.zarr` remain distinct across experiments. Users choose controls or
treatments directly; TIMED does not infer a matched control in this first
version.

Viewer links continue to use `dataset`, `path`, `compare`, and
`compareDataset`. Additional sources repeat the latter two parameters in the
same order; existing one- and two-source links remain valid. The viewer loads
each dataset's summary, metadata, Zarr source, and cache namespace
independently, while repeated wells from one dataset share their summary and
metadata request. It synchronizes the common time range, channels, pan, and
zoom across all visible fields.

Viv's built-in side-by-side viewer is fixed at two images, so TIMED retains one
controlled Viv canvas per source and shares external view state between them.
Two and four sources use a two-column grid; a third source is centered on the
second row. Metadata columns use intrinsic heights so expanding one does not
reflow another.

Four three-channel 2500 by 2500 uint16 fields occupy about 143 MiB per decoded
timepoint before GPU copies. The decoded-pixel cache therefore has a lazy
256 MiB ceiling, enough to retain one complete four-field prefetch destination
without allocating that memory up front. All sources share one three-request
prefetch budget rather than starting an independent queue per canvas. Four is a
deliberate UI and resource cap; larger cohorts should use a different overview
or pagination design.

## 2026-10-23: keep connector shutdown ordered and bounded

The idr0080 baseline exposed a pre-existing disconnect deadlock after proxied
image requests. The connector began transport cleanup while its shared HTTP
pool still retained keep-alive connections through the SSH forward. Waiting for
that forward to close could therefore wait on sockets owned by a pool which the
application would only close after session cleanup returned.

Explicit disconnect and application shutdown now close the shared HTTP pool
before the SSH transport, and explicit disconnect creates a fresh pool for a
later reconnect. Connection-task cancellation, Slurm cancellation,
connection-file removal, local-process shutdown, SSH shutdown, and port-forward
shutdown each have a bounded cleanup budget. Cleanup always clears the private
transport references and publishes a terminal state: `disconnected` when every
step succeeds, or `error` with the failed or timed-out steps when it does not.

## 2026-10-23: use the repository profile for IDR0080 branch development

The operator runs TIMED with the real Arrhenius connection settings in
`config/profiles.example.yaml`; there is no user-level `~/.timed/profiles.yaml`
on this workstation. IDR0080 branch work therefore updates that repository
profile in C1 and runs with
`uv run python scripts/dev.py --profiles config/profiles.example.yaml`.

The Makefile defaults `PROFILES` to the same repository path while retaining an
override for other environments. Because `main` has its own Git copy, adding
the data root and collections on `feature/idr0080` does not change the main
application before merge.

## 2026-10-23: model datasets as optional profile collections

Collections are optional, ordered entries on a connector profile. Their ids
are unique slugs, no more than one is the default, and every path must remain
inside a configured data root. Slurm paths use lexical POSIX containment;
local paths are exposed canonically and are checked for existence and resolved
containment when that profile connects. Deferring the strict local check keeps
profiles for future generated fixtures loadable without weakening the
data-server guard on requests.

Profiles without collections retain the original filesystem browser exactly.
The original C1 design put a Dataset selector followed by Files on the Data
page. That interaction was superseded by the 2026-10-26 active-dataset
decision below. The collection model, validation, canonical scoping, and URL
deep links remain in force.

The browser runner emits both legacy and collection-aware local and test-cluster
profiles. D1 creates the packed idr0080-like fixture, but the C1-only browser
flow keeps its disposable navigation directory until D2 can open a packed
plate. Neither fixture alters the frozen TIMED server snapshot or the
repository's operator data.

## 2026-10-23: represent packed datasets as guarded virtual paths

A packed image index remains one real Parquet file beside its tar shards. The
data server presents `<index>/<experiment>/<plate>` as a virtual hierarchy so
the existing filesystem browser and registry need no dataset-specific API.
Only the real index is resolved through `RootGuard`; catalogue membership then
validates the two virtual components. Parent segments, unsafe catalogue names,
unknown experiments and plates, and symlink escapes fail before a registry key
is accepted.

Detection reads the Parquet footer and distinct shard values, and is cached by
canonical path, modification time, and size. A per-server catalogue cache loads
the required columns lazily in a worker thread, validates null and duplicate
field/channel keys, and publishes immutable plate and field lookups only after
the file has remained stable across the load. Index changes therefore reload
atomically without blocking the event loop.

Registry records keep the existing JSON format. Packed plates store the
validated virtual key, while physical datasets retain strict filesystem
canonicalization. This makes repeated links idempotent and allows a new server
process to reopen the same packed plate without pretending the virtual suffix
exists on disk. D1 exposes traversal and linking; summaries, wells and Zarr
resources remain unavailable until the D2 facade is installed.

## 2026-10-23: cache and serve packed images one field at a time

The first chunk request for a packed field loads and decodes all five channel
planes as one unit. A single-flight, byte-bounded LRU shares that work across
concurrent requests and keeps at most 1 GiB by default; `serve
--packed-cache-mb` can change the budget. The cache key includes the index and
tar revisions, so replacement data cannot reuse stale pixels. At most four
distinct cold fields load at once, bounding their transient encoded and decoded
buffers as well as tar I/O. Read-only tar descriptors are cached for reuse and
closed with the server lifespan.

Adjacent channel members are read in one exact positional span. Non-adjacent
members fall back to concurrent positional reads under one four-operation
limit. All members resolved from one tar must share one descriptor and revision
snapshot; a concurrent replacement retries source resolution instead of mixing
bytes and validators. Every member is bounds checked, short reads fail, and a
header must have the expected USTAR name and size. Header verification includes
the expected member identity, so an index rewrite cannot reuse an unrelated
verification. A mismatch refuses that tar until restart, while transient header
I/O and TIFF contract errors fail only the request. TIFF payloads must be
single-page, uncompressed, 2-D uint16 planes at the profile shape. All tar I/O,
TIFF decoding, and Blosc encoding run in a worker thread.

Packed fields are exposed as an in-memory OME-Zarr 0.5 / Zarr v3 facade rather
than converted data. Group and array metadata, sparse plate summaries, and
well/site listings come from the pinned code profile and catalogue. Chunk
responses use deterministic validators derived from the source member,
revision, coordinates, and codec, and report whether the decoded field cache
was a hit or miss. The E2E harness copies the small generated packed fixture to
a disjoint runtime data root, so collection scoping mirrors Arrhenius while the
same facade is exercised locally and through the SSH/Slurm test cluster.

D2 activates the facade only for the exact generated-fixture profile. Other
detected packed indexes remain listable but return a clear 422 when opened.
D3 replaces that temporary milestone boundary with the pinned real idr0080
profile and the generic inferred-profile fallback required for Phase 1.

## 2026-10-23: pin IDR0080 display semantics and infer conservative packed fallbacks

The real IDR0080 profile is selected only by the exact index filename and its
155,520-row count. Its image and chunk geometry, nine sites, 384-well plate
layout, five channel names, colours, visibility defaults, and cell-line-specific
contrast windows live in one immutable code profile with an exact transcription
test. The reduced generated fixture keeps a separate 150-row pin, so test
geometry cannot be mistaken for production geometry.

Other valid packed indexes use a deliberately conservative generic profile.
The server reads one bounded, deterministic member in a worker thread, verifies
its USTAR name and size, and accepts only a single-page uncompressed 2-D TIFF.
It infers the dtype, image size, tar prefix, rows, columns, sites, experiments,
and stored channel ids; channel labels remain numeric, display windows and
dataset metadata remain absent, and the viewer uses automatic contrast. The
result is cached by both index and probed-tar revision. Facade channel positions
map through the stored channel ids, and the reader validates data against the
selected profile dtype, so a generic uint8 index does not inherit IDR0080's
uint16 assumptions.

IDR0080 well metadata remains beside the index at
`metadata/metadata_idr0080.csv`. A separate packed-metadata catalogue confines
that relative path to the index directory, reads and groups the CSV once, and
caches mapped responses per experiment and plate. Missing metadata returns
`available: false` without affecting image browsing; malformed or unmatched
metadata fails only the metadata request. The response exposes gene and well
type labels, fixed category colours, control semantics, guide details, plate
identity, cell line, and acquisition facts. New response fields are optional
and omitted when absent, preserving byte-for-byte legacy TIMED JSON bodies.

## 2026-10-23: honor declared display metadata and compare compatible fields

Complete channel windows are authoritative: when both bounds are declared, the
viewer uses them verbatim. Channels without a complete window keep the existing
automatic-statistics path. The TIMED facade now declares 65,535 as its window
end, which preserves TIMED's established display while allowing native and
packed datasets to use their intended contrast. OMERO `active` values determine
initial visibility, defaulting to visible only when the value is absent.

Metadata labels and category colours are presentation data rather than
IDR0080-specific UI rules. They drive filter, legend, source-fact and metadata
slots, while legacy responses retain their existing names and missing-value
text. Single-timepoint images omit time navigation and use field-oriented
actions; time-resolved TIMED images retain trajectory wording and controls.

Comparison entries store the effective ordered channel labels, including
axis-derived `Channel N` fallbacks. The tray and viewer share the same label
derivation and reject differing lists before image loading. Plate rendering is
now a presentation-only shared `PlateGrid` with colour, badge, marker and
dimming inputs, so later QC views can reuse the geometry without coupling their
data model to the Data page.

## 2026-10-23: derive perturbation matches from guarded packed metadata

Cross-plate matching is available only for linked packed plates. The data
server reuses the cached metadata CSV, validates every candidate against the
packed catalogue and plate identity, and returns guarded virtual plate paths
rather than physical storage details. The three modes have deterministic
semantics: same well excludes the source plate, same gene excludes only the
source occurrence, and controls are EMPTY wells on the source plate. Results
are sorted by cell line, numeric plate and well, then capped at 200.

The original Phase 1 UI grouped those results in a Related fields panel and
added them directly to the comparison tray. That browser consumer was
superseded by the 2026-10-27 grid-highlighting decision below. The guarded
endpoint and its direct contract tests remain, but the current UI does not call
it.

## 2026-10-26: choose the active dataset on Connect and restore its place

For profiles with collections, the Connect page owns the Dataset choice. It
shows every collection's title and description after the compute profile and
allows the choice both before and after connecting. The browser remembers one
collection id per profile. A valid `collection` URL parameter takes precedence
and becomes the remembered choice; without one, the order is the remembered
choice, the profile default, then the first collection.

The Data page consumes that active collection. It shows the collection title
as its heading with a small **Change dataset** link to Connect, lists only that
collection's plates, and has neither a Dataset dropdown nor a Files option.
The last opened plate and focused well are remembered separately for each
collection. Viewer return links, the Data navigation link, and reload restore
that place. Explicit `collection` and `dataset` URL parameters remain deep
links and override saved state; a deep link to another collection also changes
the remembered active dataset. If a saved plate is absent or cannot be opened,
the Data page falls back to the plate list rather than failing.

Profiles without collections keep the original Connect and filesystem Data
pages unchanged. The Phase 2 QC page consumes the same active dataset instead
of owning another selector, so Data and QC cannot silently review different
collections.

The user-facing title for the `idr0080` collection is **Cell Health**. The
technical id remains `idr0080` in descriptions, ids, experiment names, and
paths. The packed code profile uses the same display name, producing plate
names that combine Cell Health, the cell line, and the plate without changing
the underlying virtual path.

## 2026-10-26: use RxRx hues for composites and grayscale for validation

The composite palette comes from the `RGB_MAP` in Recursion's official
[`rxrx1-utils` image helper](https://github.com/recursionpharma/rxrx1-utils/blob/31e6f9b826c6de84f531cd2b4f67bfc158036d7d/rxrx/io.py#L17-L42).
Matching those channel colours by stain gives DNA `1300F9`, ER `2AFF1F`, RNA
`2DFFFC`, AGP `FF0019`, and Mito `FA00FD`. RxRx's separate PM channel uses
`FEFF28`; TIMED records that value for later but IDR0080 does not expose a
separate PM channel.

DNA, ER, and AGP remain visible by default, and every per-cell-line contrast
window remains unchanged. The hue-separated colours make a five-channel
composite easier to read.

The superseded Cell Health palette was made for separate, single-channel
panels. Its figure helper also stretches every cropped channel independently
from its 1st to 99th percentile. The A1 comparison with Cell Health v2.0 is
therefore performed one channel at a time in grayscale. TIMED retains the fixed
Appendix B.2 windows during that comparison because a stable intensity mapping
keeps brightness comparable between wells.

## 2026-10-27: make Fluorescence and Grayscale explicit viewer modes

The previous Grayscale section at the bottom of the channel controls is
superseded. A **Fluorescence | Grayscale** mode control now belongs at the top.
Fluorescence is the default colour composite and keeps the existing channel
visibility controls and the full list of contrast sliders. Grayscale shows one
selected channel in white with that channel's current contrast window and shows
only that channel's contrast sliders; selecting another channel switches both
the grayscale source and the visible sliders.

Switching modes preserves the Fluorescence visibility state, and contrast
remains adjustable in either mode. The chosen mode persists while moving
between fields. One mode applies to all viewer panels, including comparisons
and every source type. Split view and per-channel Solo buttons remain out of
scope.

The completed A1 grayscale comparison matched Cell Health's reference exactly.
That independent result confirms the plate, well, site, orientation, and
channel order while leaving the fixed cross-well contrast windows intact.

## 2026-10-27: use plate-grid highlights instead of Related fields

The Related fields result list and its add-to-comparison actions are removed.
When a well is selected, **Same gene** and **Controls** act as plate-grid
highlight toggles for the active plate. They are mutually exclusive: choosing
one clears the other, and clicking the active button again clears the
highlight. Within Cell Health, switching plates preserves the selected well,
making same-position comparison a direct plate-switching workflow; TIMED and
other collections keep their existing switching behaviour. A carried well is
appended while the comparison tray has room. At the four-field limit it
replaces the focused source from the plate being left, in the same tray slot,
so the coordinate remains selected without evicting an unrelated source. Site
choices, the four-field limit, and the channel guard otherwise remain
unchanged.

The packed matches endpoint is deliberately retained for possible later use.
Its guarded paths, deterministic modes, limit, and direct tests remain useful,
but the current browser makes no request to it.

## 2026-10-27: deliver scale bars in Phase 1 S1

Physical scale is a viewer concern, not a segmentation dependency, so S1 moves
from Phase 2 into Phase 1 before R1. TIMED uses its documented calibration of
0.3 µm/px. The operator checked the Phenix `Index.idx.xml` files for all nine
Cell Health plates, `SQ00014610` through `SQ00014618`. Every plate reports both
X and Y as `2.9898804047838085E-07` m at 20×, so Cell Health stores the full
converted value `0.29898804047838085` µm/px in its packed profile. Display text
may round that value, but metadata and calculations retain the full precision.

Every image panel derives its scale bar from its own source metadata. This also
applies in comparison mode, where panels can have different calibrations. A
panel with no valid pixel size has no scale bar. Phase 2 may reuse the same
metadata for physical area units, but does not own the scale-bar feature.
