# Decision log

## 2026-10-05: preserve the brief's architecture

The prototype keeps the browser, local connector, and remote data server as
separate seams. The browser uses same-origin `/connector` and `/backend` routes;
the connector owns credentials and tunnelling; the data server owns restricted
OME-Zarr access. This directly supports a later Tauri sidecar without coupling
the first web prototype to Tauri.

## Pending M1: real OME-Zarr layout

The implementation will follow the metadata inventory rather than infer format
from `.zarr` names. Until that inventory is available, test-data work uses the
documented OME-Zarr 0.4 / Zarr v2 defaults and also covers a v0.5 / Zarr v3 image.
