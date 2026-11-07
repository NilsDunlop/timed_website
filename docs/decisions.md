# Decision log

## 2026-11-04: accept validated Berzelius TIMED geometry without a second format

Berzelius temporarily hosts `PB100140_P2.1` while Arrhenius is unavailable. Its
plate root is a non-OME Zarr v3 group and its field shards use whole-plane inner
chunks. These are safe variations of the existing bare TIMED layout: the facade
continues to detect named field arrays below the root and derives every chunk,
shard, and codec setting from the selected field's own metadata. It now rejects
fields whose Zarr version, four-dimensional `(t, c, y, x)` axes, `uint16` dtype,
identity, channel/time lengths, whole-array shard, or indexed Blosc-Zstd codec
family do not satisfy the TIMED contract. Arrhenius's tiled fields are unchanged.

Repaired fields may carry `timed.site_position_fix`. The viewer reports its
grid-x position and repaired timepoints but never remaps a field name or pixel.
Berzelius metadata is generated as the existing bounded per-plate CSV and
acquisition JSON; the server does not gain a consolidated-Parquet runtime path.
The CSV preserves the source spelling `U2OS_Cleo3` without display
normalisation. The cluster profile is CPU-only and omits `--mem`, while the
rendered Arrhenius job retains its existing partition and memory directives.

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

## 2026-10-26: use RxRx hues for composites and Grayscale for validation

The composite palette comes from the `RGB_MAP` in Recursion's official
[`rxrx1-utils` image helper](https://github.com/recursionpharma/rxrx1-utils/blob/31e6f9b826c6de84f531cd2b4f67bfc158036d7d/rxrx/io.py#L17-L42).
Matching those channel colours by stain gives DNA `1300F9`, ER `2AFF1F`, AGP
`FF0019`, RNA `2DFFFC`, and Mito `FA00FD`. RxRx's separate PM channel uses
`FEFF28`; TIMED records that value for later but IDR0080 does not expose a
separate PM channel.

DNA, ER, and AGP remain visible by default, and every per-cell-line contrast
window remains unchanged. The hue-separated colours make a five-channel
composite easier to read.

The superseded Cell Health palette was made for separate, single-channel
panels. Its figure helper also stretches every cropped channel independently
from its 1st to 99th percentile. The A1 comparison with Cell Health v2.0 is
therefore performed one channel at a time in **Grayscale** mode. TIMED retains
the fixed Appendix B.2 windows during that comparison because a stable
intensity mapping keeps brightness comparable between wells.

## 2026-10-27: make Color and Grayscale explicit viewer modes

The previous Grayscale section at the bottom of the channel controls is
superseded. A radio group labelled **Display** now belongs at the top and offers
**Color** and **Grayscale** in a responsive segmented control. The segments
share the available width equally, sit side by side when space permits, and
stack when the control becomes narrow. Their native radio circles are visually
hidden without removing the inputs or their radio semantics: arrow keys move
between options, and the keyboard-focused segment has a visible focus ring.

Color is the default colour composite and keeps the existing channel visibility
controls and the full list of contrast sliders. Grayscale shows one selected
channel in white with that channel's current contrast window and shows only that
channel's contrast sliders; selecting another channel switches both the
grayscale source and the visible sliders.

Switching modes preserves the Color visibility state, and contrast
remains adjustable in either mode. The chosen mode persists while moving
between fields. One mode applies to all viewer panels, including comparisons
and every source type. Split view and per-channel Solo buttons remain out of
scope.

The completed A1 Grayscale comparison matched Cell Health's reference visually.
It confirmed the plate, well, site, orientation, and source imagery, but did
not independently establish the packed-slot-to-stain mapping. The later 0.4.1
channel-identity correction records why the AGP/RNA swap escaped this check.

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

## 2026-10-27: gate Color and Grayscale on a server declaration

The earlier decision that made the Display switch universal is superseded only
in its scope. `ImageSummary` gains an optional `grayscale_available` field that
the data server emits as `true` only for datasets that declare the capability.
Missing and `false` mean unavailable. `PackedDatasetProfile` defaults the field
to `False`; the real and fixture Cell Health profiles set it to `True`.

The viewer shows the **Display** radio group only when every loaded source
declares `grayscale_available: true`, because one mode still applies to all
panels. Cell Health-to-Cell Health comparisons therefore retain the shared
Color/Grayscale switch. If any source does not declare it, the viewer renders
the original colour composite, channel visibility controls and full contrast
list with no Display or grayscale-channel switch, regardless of any stored
grayscale preference.

TIMED deliberately omits the field, preserving its existing response and
viewer controls. Other native or packed datasets get the same legacy behaviour
unless their data server explicitly opts them in.

## 2026-10-27: freeze the accepted Phase 1 release boundary

Phase 1 closes on the real-data A1 result rather than on another display or
performance redesign. On Arrhenius, TIMED remained the preselected, unchanged
workflow; Cell Health A549 `SQ00014610` A01 site 1 matched the inspection
preview; and the independent ES2 `SQ00014613` A01 site 1 grayscale crop matched
Cell Health v2.0 visually. The latter is acceptance evidence for plate, well,
site, orientation, and imagery, but not for the packed-slot-to-stain mapping.
The requested cross-cell-line MCL1
comparison and EMPTY control loaded together and stayed synchronized.

The accepted field-opening timing is about 4–5 seconds on the operator's work
network and about 2 seconds at home. Disconnect reached `disconnected` in about
2 seconds. Those results are comparable to loading a TIMED trajectory, so the
optional performance fallbacks are not part of Phase 1 and must not be enabled
for R1.

The final dataset workflow is the Connect-owned choice recorded above: Data
follows the active collection and restores that collection's last plate and
well after viewer return, Data navigation, or reload. The A1 run exercised the
choice between Cell Health and TIMED and confirmed that restoration behavior.
Profiles without collections remain on the legacy filesystem workflow.

The release boundary keeps the grayscale declaration and calibration rules
source-specific. Cell Health opts into shared Color/Grayscale viewing; the
switch is present only when every loaded source opts in. TIMED and undeclared
sources retain their original colour controls with no switch. Independently,
each panel derives a scale bar from its own metadata: TIMED uses 0.3 µm/px,
Cell Health uses `0.29898804047838085` µm/px, and uncalibrated sources show no
bar. These Phase 1 rules also apply to comparison panels and are not deferred
to the Phase 2 segmentation work.

A1 and the source-calibration evidence are complete. R1 prepares the release
documents and version `0.4.0` on `feature/idr0080`; merging and tagging remain
operator-controlled steps after review.

## 2026-10-28: correct packed Cell Health channel identity

Cell Health's packed `channel` column is a packed-slot number, not the original
Phenix / IDR channel number. The export's `channels.csv` resolves the identity
by high-pass correlation over 21 sites: every packed plane correlates at least
0.98 with its matching raw channel, and no more than 0.69 with any other raw
channel. The resulting mapping is DNA slot 1 to Phenix 1, ER slot 2 to Phenix
2, AGP slot 3 to Phenix 4, RNA slot 4 to Phenix 3, and Mito slot 5 to Phenix 5.

Phase 1 assumed that packed slot and Phenix channel were the same. That was
wrong because CellPrompter's records number the raw Phenix files rather than
the repacked member suffixes. The mistake exchanged AGP and RNA labels while
leaving the underlying planes intact. The A1 grayscale comparison therefore
confirmed image content and geometry but did not catch the identity error.

The code profile now lists packed slots as DNA, ER, AGP, RNA, and Mito. Colours
and default visibility follow the corrected stains: AGP is red (`FF0019`) and
active; RNA is cyan (`2DFFFC`) and inactive. Surveyed contrast windows remain
attached to their packed slots, so slot 3 keeps 554–9,283 / 375–10,793 /
497–12,756 with a 0–26,000 slider, while slot 4 keeps 151–5,035 / 108–5,778 /
117–6,968 with a 0–14,000 slider. RNA's dimmer slot is consistent with its
0.01 s exposure compared with AGP's 0.02 s exposure.

The Arrhenius re-check passed on a newly submitted `0.4.1.dev0` Slurm job.
The viewer listed DNA, ER, AGP, RNA, and Mito in packed slot order; ES2 AGP
used 375–10,793 and showed Golgi and cell-edge signal, while RNA used
108–5,778 and showed nucleolar signal. The first attempt had reused the old
Slurm job, so it was discarded rather than treated as validation evidence.

## 2026-10-28: ship the complete core QC view before its blinded UI

Cell Health QC is split into two releases. Version 0.5.0 is the complete core
review workflow. Its API receives the full configured export, while its
reason-first browser keeps secondary values behind progressive disclosure and
limits the field viewer to review-relevant information. A later 0.5.1 pull
request adds the user-facing blinded-mode switch. Part C segmentation remains
separate at 0.6.0.

The server boundary is designed for both modes now. Every QC endpoint accepts
`blind=1`; that option removes all 65 columns marked hidden in the replacement
export plus the additional Phase 1 and viewer attributes defined by the
blinded contract before a response is built. The 0.5.0 browser does not set
that option. Real hidden names and values remain absent from documentation,
commits, fixtures, logs, and errors even though the unblinded core API may
serve them at runtime.

The export dictionary owns QC presentation. `site_review` rows define field
sliders, `context` rows are shown and sortable without sliders, `triage` rows
identify the plate and channel keys in the overview file, `panel` rows describe
plate- or well-level views, and `retired` rows are never shown. All non-key
dictionary rows belonging to the overview file are rendered there, including
its `site_review` and `panel` values. Key and review
rows retain their structural roles. The `worse` value is the slider
direction. Unknown statuses, along with retired and dropped rows, are hidden
and reported by the status endpoint; `pending_review` remains visible with a
badge.

One primary column per metric and channel creates a slider; the replacement
contract defines 37. Automatic gates show their exported registered markers
and are never recomputed by TIMED. Review gates start from a strict exported
`< threshold` or `> threshold` rule and report flagged counts. None gates
start fully open and filter without flagging or feeding queues. Filter ranges
combine with AND; active flag sliders additionally keep rows beyond at least
one flag rule. Context values, including the export-defined attributes, remain
available to sorting and categorical filters. Control identity continues to
come from the Phase 1 EMPTY-control metadata.

The core page combines the dictionary-labelled plate/channel overview with all
nine 384-well PlateGrids, Sites and Wells tables, categorical filters, live
overall and per-slider counts, and fully URL-restorable interactive state.
Browser filtering must update in under 100 ms for 31,104 sites. A well view
opens at site 1 and retains a site stepper. Review-list order is authoritative;
session queues may contain sites or wells. Plate/channel image bounds are plain
linear windows, while their quantiles and reference-field counts are
provenance.

Annotations are the data server's only write. Field- and well-scoped records
are append-only and include whether the request was blinded. The core view
writes unblinded records. Rule proposals, their endpoint, their file, and their
UI are removed from 0.5.0.

The core-ready export includes the primary and marker dictionary fields,
one-sided default flag expressions, triage dictionary rows, queue identifiers,
crop centres and the annotation-label registry. After the 0.5.0 merge, blinded
mode is the next PR, followed only later by Part C.

Queue state is browser-session state, not export state. The review-list queue
keeps file order; "Review these" keeps the currently displayed Sites or Wells
order in one compact, validated `sessionStorage` record. Its export, registry,
collection, and random queue identities must all match before the viewer will
navigate it. Annotation status and queue counts use exact target scope and both
the numeric plate and barcode, preventing annotations from crossing to a
different plate if a later export reuses the number. An
All-channel annotation covers a stain-specific queue entry, but a stain record
does not mark an All-channel composite entry.

The annotation write boundary is narrower than the configured data boundary.
The annotations folder may not overlap a data root or any configured QC export
in either direction. The writer path comes only from the configured folder and
the sanitised operating-system user. Every record is one append plus `fsync`,
and a process-wide lock keyed by canonical writer file serialises stores that
resolve to the same file. Connector-proxied state-changing requests also
require the existing `X-Timed-Client: web` guard; the connector removes that
local guard header before forwarding the authenticated request.

## 2026-10-29: expire connector HTTP keep-alives before the data server

The QC reload regression exposed a stale pooled-connection race: Uvicorn closes
an idle upstream connection after five seconds, while the connector previously
kept it eligible for reuse for thirty. A reload could therefore select a socket
as the server closed it and return an isolated 502, even while concurrent data-
server requests succeeded. The connector now expires pooled upstream sockets
after four seconds, preserving normal reuse while staying below the server's
idle timeout.

## 2026-10-29: make the replacement QC export the runtime contract

Export `d77939bbbf42` is the core-ready Part B snapshot: 31,104 fields, 102
dictionary rows, 178 queue entries and seven hashed CSV files. Its additional
file is `labels.csv`, an ordered registry of 14 annotation identifiers and
their operator guidance. The data server validates annotation writes against
the active registry and returns it with QC payloads; the browser renders those
choices and descriptions instead of maintaining a hard-coded list. Historical
stored labels remain readable, but a new write must use the active registry.

The final producer contract uses `default_flag` as a strict one-sided
comparison (`< threshold` or `> threshold`). It does not use the earlier split
low/high fields. This keeps the upstream rule intact and lets TIMED initialise
review sliders without deriving thresholds. The loader also accepts the
export's dedicated `triage` key layer while retaining `site_review` and
`panel` for its overview values.

The manifest allowlist is intentionally exact. Adding `labels.csv` without a
matching server update produced the visible “unsupported file hashes” error;
silently ignoring the seventh hash would have bypassed both its integrity
check and its role in annotation validation. The replacement is now validated
as one atomic seven-file snapshot. Private data remain described only by
count: 55 hidden site columns and ten hidden review-list columns.

## 2026-10-29: make QC review progressive and reason-first

The QC export remains complete and dictionary-driven, but its full schema is
not the default presentation. This first compact pass used progressive
disclosure for export identity, the plate overview, secondary categorical
filters, additional table columns and viewer details. The later review-only
decision below removes nonessential viewer details entirely. No export column
or endpoint was removed.

The metric search is replaced by a channel-grouped dropdown with readable
labels and one control card at a time. The plate overview similarly shows one
selected measure across plates and channels and is collapsed by default. The
Sites and Wells tables begin with identity, essential metadata, annotation
state and a prominent **Flagged by** column; context, attributes and primary and
raw metric values are behind **Additional table columns**. Core filters remain
visible and secondary context is grouped under **More filters**.

The export's D62-prefixed operator measures are translated at the UI boundary:
"share excluded automatically" is shown as **Automatic exclusion** and "share
flagged for review" as **Flagged for review**. This is a display alias only;
dictionary identity, values and filtering semantics remain unchanged.

Review-list entries derive their readable reason from the matching primary
dictionary metric and channel. Session queues derive it from the active review
rules that matched each site or well. The reason is carried into the viewer and
shown before secondary details. Queue storage deduplicates repeated reason
strings so a complete 31,104-field session queue remains within a conservative
browser-storage quota while retaining the existing export, registry,
collection and target validation.

## 2026-10-29: keep the QC interface review-only

The final Part B review surface removes information that does not help decide
the current image. The overview calls individual acquisitions **Sites** and
shows Sites, Wells and Control sites; protection is neither a count nor a
filter. Cell line and plate remain primary filters, while Control status, Gene
and Annotation status live under **More filters**. Dataset-internal well-type
and arbitrary context fields are not offered as operator filters. Plate maps
pair their colour ramp with a numeric legend and an information tooltip, and a
well hover names only the sites and metrics that actually triggered an active
review rule. Plate visibility follows category and range filters: the unfiltered
view has all nine plates, while A549 shows only its three plates.

The QC export's plate-by-channel display windows are now always applied in the
viewer. They are the upstream review presentation, so allowing an alternate
dataset window would make two operators judge different renderings. The exact
minimum and maximum remain visible in the locked channel controls; the former
range switch, display-provenance panel and explanatory banner are removed.

Field review shows one compact, review-only metric table. Stain channels come
first, whole-field metrics last, and each row pairs the site/raw value with its
control-relative or median-relative review value. The UI calls `control_z`
**Relative to controls** and states that zero is typical of EMPTY controls while
negative and positive values are lower and higher. This removes statistical
jargon without implying a percentage or fold change. Dictionary questions are
focusable information tooltips. Unrelated Otsu measures, context, attributes
and protection details are intentionally absent. A repeated metric exposes its
question only on the first channel row. Internal rule-family prefixes are
removed from ordinary operator-facing metric labels and help questions, while the established
**Automatic exclusion** alias remains. The first help tooltip opens below its
icon so the table boundary cannot clip it. Whole-field metrics remain
because they account for most entries in the supplied review queue; removing
them would hide the primary review reason rather than reduce noise.

The **Site value** and **Relative to controls** labels reserve one shared width,
so their numeric values align vertically without changing the metric data.

Well queues carry their exact flagged site numbers when the active filters can
provide them. The field viewer makes those sites direct navigation targets; a
file-supplied well entry without site identities says so explicitly. The queue
bar is progress on the left, flag reason in the middle and Previous/Next on the
right. Left and Right Arrow replace bracket shortcuts, and the duplicate Back
to QC action is removed while the global QC navigation remains available.

## 2026-11-04: accept Part B on Arrhenius and prepare 0.5.0

The operator completed A2 manually on Arrhenius against the final development
build `0.5.0.dev12`. The compact QC page, plate maps and filters, Sites/Wells
tables, review reasons, both queue types, fixed display ranges, field review
metrics and annotation lifecycle were accepted. Interactive responsiveness was
accepted during normal use; no separate timing figures were supplied.

Version 0.5.0 therefore contains the unblinded, dictionary-driven QC review
workflow and the server-side blinded-response contract. Hidden export data are
handled only by classification and count in repository material; `blind=1`
removes all columns marked hidden before response serialization, while the
0.5.0 browser deliberately makes unblinded requests. Append-only annotation
records in the configured annotations folder remain the data server's only
write. The user-facing blinded mode and all segmentation-outline work are
explicitly deferred.

## 2026-11-05: use the external Berzelius mirror during planned downtime

The external-drive copy at `D:/TIMED` mirrors the Berzelius `zarr/` and
`metadata/` layout, including all 392 field arrays, explicit axes and the two
per-plate runtime metadata files. A separate collection-aware `local-timed`
profile makes this copy the main validation target while the cluster is
unavailable; the Berzelius Slurm profile remains configured for later operator
validation.

Local mode treats the Windows drive-letter paths as filesystem paths and passes
them to the data-server child as individual subprocess arguments. Runtime
state, the dataset registry and test artefacts stay outside the external copy.
An opt-in integration test reads one complete whole-plane chunk through the
HTTP facade, loads the 196-well metadata catalogue and verifies C03 site 1's
repair metadata. A matching opt-in browser flow verifies the rendered image and
operator-facing repair note. Both checks are read-only; the server integration
test additionally guards representative source metadata with before-and-after
hashes.

The operator accepted the local mirror on 2026-11-05: profile selection,
metadata, time navigation, the C03 repair note and disconnect all behaved as
expected, and local image viewing was noticeably faster than the tunneled
cluster path. This compatibility work is released as 0.5.1. The former 0.5.1
reservation for the still-future blinded QC interface is superseded; that work
has no assigned release number. Segmentation remains separate and starts later
from a fresh branch.

## 2026-11-06: remove no-op viewer props and dead code, declare used dependencies

Viv 0.23 renders single-resolution fields with `ImageLayer`, which reads neither
`maxRequests` nor `excludeBackground`; only `MultiscaleImageLayer` does. The
viewer no longer passes them or computes a request budget. The 2026-10-06 entry
credits `excludeBackground` with keeping the last TIMED frame visible while the
next one loads; `ImageLayer` does that itself, because it keeps its previous
rasters until every raster of the new selection has arrived.

The unused Related fields component and its styles, the dataset-contrast restore
that only the removed QC range switch needed, and the test-only copy of the
Slurm job template are deleted. The packed matches endpoint stays, as decided on
2026-10-27.

The data server now declares `numpy` and `numcodecs`, which it imports, at their
locked versions; the `numcodecs` version is part of every packed chunk ETag.
`zarr` stays declared although the server does not import it, because the tests
and `scripts/make_test_data.py` do. `.gitattributes` keeps the generated OpenAPI
JSON files LF on every platform, and the MCP browser is pinned to
`@playwright/mcp` 0.0.80 with Playwright's Chromium, the build the browser suite
installs.

## 2026-11-06: restructure the data server and viewer before segmentation (0.5.2)

Release 0.5.2 changes structure only: behaviour, the API, URLs and stored data
are unchanged. Committed-versus-exported OpenAPI checks, token, path and write
guards, and temporary characterisation snapshots of server responses, fixture
bytes, viewer links and viewer state, removed again at the end, proved each
step. Field opening on the external-drive TIMED copy was timed before and after.

- **Data server.** Shared revision, tar-member and byte-bounded single-flight
  cache primitives (`revisions.py`, `tar_io.py`, `caches.py`); HTTP cache and
  error modules (`http_cache.py`, `errors.py`); `PackedProfileResolver`; and a
  `ServerContext` (`context.py`) that `APIRouter` modules under `routes/`
  receive through `Depends(get_context)` for the handshake, filesystem, dataset
  and Zarr routes. The QC routes stay in `create_app` until the review-queue
  work. FastAPI 0.142 keeps each included router as one lazy entry in
  `app.routes`, so tests enumerate routes with `iter_route_contexts`.
- **Web.** Pure viewer helpers in `lib/` (`viewerChannels`, `viewerSources`,
  `viewerSourceIdentity`, `fieldPath`, `plateMetadata`), one `/view` link reader
  and two writers (`lib/viewerLink.ts`, `lib/qc/qcViewerLink.ts`) that keep each
  builder's parameter order, and `components/viewer/VivCanvas.tsx` with an
  `overlayLayers` composition point that `lib/viewerOverlays.ts` will feed.
- **Tests.** The cluster test runs inside the connector lifespan again, and every
  browser flow fails on an unexpected console error.

The mask-store contract for Part C now lives in `docs/data/IDR0080.md`. It
condenses the idr0080 brief's section 5.1 and Appendix D with Part C's
amendments: no `qc.parquet`, counts from the masks' labels, and outline toggles
in the viewer controls the QC field view shares.

## 2026-11-06: fix the review findings (0.5.3)

Release 0.5.3 fixes what the refactor review found. Each fix came with a test
that failed first.

- **Contrast sliders.** Integer data with a whole-valued domain and window
  steps by 1, and other data accepts any value, so a declared window is an
  exact slider position. A step of span / 1000 had moved TIMED's 100 to 131.07.
  QC mode keeps "any".
- **No absolute paths in error details.** They name a file by its name or
  inside its dataset, metadata root or index folder, and give an OS error's
  reason without its file name. Startup and configuration errors still name the
  configured path the operator must fix; server logs keep full paths.
- **QC folders stay private.** They are neither browsable, nor linkable, nor
  served through `/zarr`, even inside a linked store, because `blind=1`
  protects only `/api/qc`.
- **Windows reviewer.** The writer file follows `USERNAME`, then the getpass
  variables, then `reviewer`: a shell's `USER` cannot rename it, and a missing
  variable no longer fails every QC request.
- **Connection file.** Lifespan shutdown removes it, and Uvicorn runs that
  before it re-raises SIGTERM; the process still ends by the signal.
- **Remote install.** `uv pip install -c` uses the data server's runtime
  dependencies exported from `uv.lock` and uploaded beside the wheel. A
  development install key covers the wheel and those constraints, so a lock
  change reinstalls; a release keeps its version as the key.
- **No site-position note.** At the operator's request the viewer no longer
  shows the repair note for repaired TIMED fields, superseding the 0.5.1 note.
  The server still validates `site_position_fix` and reports it in the field
  summary, and field names and pixels are still never remapped.
