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

The section 4 architecture remains unchanged. The data server will additionally
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
