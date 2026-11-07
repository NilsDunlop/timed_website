// Segmentation outlines for every viewer source: the mask models each source's
// field offers, the selected model's counts, and one outline image per kind
// that is switched on. The settings are shared by all sources.
//
// Listings load in parallel with the image. Counts wait until each source's
// axes are known: a source with a time axis (TIMED) asks for the displayed
// timepoint, so its count line follows the frame on screen, and one without
// (Cell Health) asks once. Outlines follow the displayed timepoint too, so they
// wait until a frame is on screen and never run ahead of it.

import { useEffect, useMemo, useRef, useState } from "react";

import { apiErrorMessage, dataServerClient } from "../../lib/api/client";
import { parseFieldPath } from "../../lib/fieldPath";
import {
  chooseMaskModel,
  maskOutlineUrl,
  sharedMaskModels,
  type MaskCounts,
  type MaskField,
  type MaskModel,
} from "../../lib/maskOverlays";
import { viewerDebug } from "../../lib/viewerDebug";
import type { ViewerOverlay } from "../../lib/viewerOverlays";

export type MaskSource = { key: string; datasetId: string; path: string };

export type MaskSettings = {
  /** The requested model; the default model is used when it is not offered. */
  model: string | null;
  kinds: readonly string[];
  width: number;
};

export type Loaded<T> =
  | { status: "loading" }
  | { status: "ready"; value: T }
  | { status: "error"; message: string };

export type MaskOverlays = {
  /** Models offered for every source, default first; empty hides the panel. */
  models: readonly MaskModel[];
  model: MaskModel | null;
  /** The selected model's counts, by source key, at the displayed timepoint. */
  counts: ReadonlyMap<string, Loaded<MaskCounts>>;
  /** Ready outline images by source key, in drawing order. */
  overlays: ReadonlyMap<string, readonly ViewerOverlay[]>;
  outlinesLoading: boolean;
  outlineErrors: readonly string[];
};

type Listing = { key: string; datasetId: string; field: MaskField | null };

type CountRequest = {
  key: string;
  sourceKey: string;
  datasetId: string;
  model: string;
  field: MaskField;
  t: number | null;
};

type OutlineRequest = {
  key: string;
  sourceKey: string;
  kind: string;
  timepoint: number;
  url: string;
};

const NO_OVERLAYS: ReadonlyMap<string, readonly ViewerOverlay[]> = new Map();

function isAbort(reason: unknown): boolean {
  return reason instanceof DOMException && reason.name === "AbortError";
}

/** A source's listing: its field's, or its dataset's when the path names no field. */
function listingFor(source: MaskSource): Listing {
  const field = parseFieldPath(source.path);
  return {
    key: field ? [source.datasetId, field.well, field.site].join("\u0000") : source.datasetId,
    datasetId: source.datasetId,
    field,
  };
}

async function loadOutline(url: string, signal: AbortSignal): Promise<ImageBitmap> {
  const response = await fetch(url, { signal });
  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = await response.json();
    } catch {
      detail = null;
    }
    throw new Error(apiErrorMessage(detail, "The outline could not be loaded"));
  }
  return createImageBitmap(await response.blob());
}

/**
 * Outlines and counts for every source. `timepoint` is the displayed one;
 * `timeAxes` says, once a source's image is loaded, whether it has a time axis.
 */
export function useMaskOverlays(
  sources: readonly MaskSource[],
  settings: MaskSettings,
  timepoint: number | null,
  timeAxes: ReadonlyMap<string, boolean>,
): MaskOverlays {
  const listings = useMemo(() => {
    const unique = new Map<string, Listing>();
    for (const source of sources) {
      const listing = listingFor(source);
      unique.set(listing.key, listing);
    }
    return [...unique.values()];
  }, [sources]);
  const [catalogues, setCatalogues] = useState<
    ReadonlyMap<string, Loaded<readonly MaskModel[]>>
  >(() => new Map());

  useEffect(() => {
    const controller = new AbortController();
    setCatalogues(new Map(listings.map((listing) => [listing.key, { status: "loading" }])));
    for (const listing of listings) {
      const started = performance.now();
      void dataServerClient
        .GET("/api/datasets/{dataset_id}/masks", {
          params: {
            path: { dataset_id: listing.datasetId },
            query: listing.field
              ? { well: listing.field.well, site: listing.field.site }
              : undefined,
          },
          signal: controller.signal,
        })
        .then(({ data, error }) => {
          viewerDebug("masks:listing-ready", {
            datasetId: listing.datasetId,
            models: data?.models.length ?? null,
            ms: Math.round(performance.now() - started),
          });
          setCatalogues((current) =>
            new Map(current).set(
              listing.key,
              data
                ? { status: "ready", value: data.models }
                : {
                    status: "error",
                    message: apiErrorMessage(error, "The mask models could not be loaded"),
                  },
            ),
          );
        })
        .catch((reason: unknown) => {
          if (controller.signal.aborted || isAbort(reason)) return;
          setCatalogues((current) =>
            new Map(current).set(listing.key, {
              status: "error",
              message: "The mask models could not be loaded",
            }),
          );
        });
    }
    return () => controller.abort();
  }, [listings]);

  const models = useMemo(
    () =>
      sharedMaskModels(
        sources.map((source) => {
          const catalogue = catalogues.get(listingFor(source).key);
          return catalogue?.status === "ready" ? catalogue.value : null;
        }),
      ),
    [catalogues, sources],
  );
  const model = chooseMaskModel(models, settings.model);
  const modelTag = model?.model_tag ?? null;

  const countRequests = useMemo<CountRequest[]>(() => {
    if (!modelTag) return [];
    return sources.flatMap((source) => {
      const field = parseFieldPath(source.path);
      const timed = timeAxes.get(source.key);
      if (!field || timed === undefined || (timed && timepoint == null)) return [];
      const t = timed ? timepoint : null;
      return [
        {
          key: [source.key, modelTag, t ?? ""].join("\u0000"),
          sourceKey: source.key,
          datasetId: source.datasetId,
          model: modelTag,
          field,
          t,
        },
      ];
    });
  }, [modelTag, sources, timeAxes, timepoint]);

  const [countResults, setCountResults] = useState<
    ReadonlyMap<string, Loaded<MaskCounts>>
  >(() => new Map());
  const countResultsRef = useRef(countResults);
  const countsInflight = useRef(new Map<string, AbortController>());
  useEffect(() => {
    countResultsRef.current = countResults;
  }, [countResults]);

  useEffect(() => {
    const wanted = new Set(countRequests.map((request) => request.key));
    for (const [key, controller] of countsInflight.current) {
      if (!wanted.has(key)) {
        controller.abort();
        countsInflight.current.delete(key);
      }
    }
    for (const request of countRequests) {
      if (countsInflight.current.has(request.key) || countResultsRef.current.has(request.key)) {
        continue;
      }
      const controller = new AbortController();
      countsInflight.current.set(request.key, controller);
      const started = performance.now();
      // A key asked for again after an abort has a new controller; leave it alone.
      const release = () => {
        if (countsInflight.current.get(request.key) === controller) {
          countsInflight.current.delete(request.key);
        }
      };
      const settle = (value: Loaded<MaskCounts>) => {
        release();
        setCountResults((current) => new Map(current).set(request.key, value));
      };
      void dataServerClient
        .GET("/api/datasets/{dataset_id}/masks/{model}/{well}/{site}", {
          params: {
            path: {
              dataset_id: request.datasetId,
              model: request.model,
              well: request.field.well,
              site: request.field.site,
            },
            query: request.t == null ? undefined : { t: request.t },
          },
          signal: controller.signal,
        })
        .then(({ data, error }) => {
          if (controller.signal.aborted) return;
          viewerDebug("masks:counts-ready", {
            source: request.sourceKey,
            model: request.model,
            timepoint: request.t == null ? null : request.t + 1,
            ms: Math.round(performance.now() - started),
          });
          settle(
            data
              ? { status: "ready", value: data.counts }
              : {
                  status: "error",
                  message: apiErrorMessage(error, "The mask counts could not be loaded"),
                },
          );
        })
        .catch((reason: unknown) => {
          if (controller.signal.aborted || isAbort(reason)) {
            release();
            return;
          }
          settle({ status: "error", message: "The mask counts could not be loaded" });
        });
    }
  }, [countRequests]);

  const counts = useMemo(() => {
    const bySource = new Map<string, Loaded<MaskCounts>>();
    for (const request of countRequests) {
      bySource.set(request.sourceKey, countResults.get(request.key) ?? { status: "loading" });
    }
    return bySource;
  }, [countRequests, countResults]);

  const kindsKey = settings.kinds.join(",");
  const requests = useMemo<OutlineRequest[]>(() => {
    if (!model || timepoint == null) return [];
    const visible = model.kinds.filter((kind) => kindsKey.split(",").includes(kind));
    return sources.flatMap((source) => {
      const field = parseFieldPath(source.path);
      const timed = timeAxes.get(source.key);
      const sourceCounts = counts.get(source.key);
      if (!field || timed === undefined || sourceCounts?.status !== "ready") return [];
      // Later layers draw on top: nuclei, the smallest objects, come last.
      return [...visible]
        .reverse()
        .filter((kind) => kind in sourceCounts.value)
        .map((kind) => {
          const url = maskOutlineUrl(
            source.datasetId,
            model.model_tag,
            field,
            kind,
            settings.width,
            timed ? timepoint : null,
          );
          return {
            key: `${source.key}\u0000${timepoint}\u0000${url}`,
            sourceKey: source.key,
            kind,
            timepoint,
            url,
          };
        });
    });
  }, [counts, kindsKey, model, settings.width, sources, timeAxes, timepoint]);

  const [outlines, setOutlines] = useState<ReadonlyMap<string, Loaded<ImageBitmap>>>(
    () => new Map(),
  );
  const outlinesRef = useRef(outlines);
  const inflight = useRef(new Map<string, AbortController>());
  useEffect(() => {
    outlinesRef.current = outlines;
  }, [outlines]);

  useEffect(() => {
    const wanted = new Set(requests.map((request) => request.key));
    for (const [key, controller] of inflight.current) {
      if (!wanted.has(key)) {
        controller.abort();
        inflight.current.delete(key);
      }
    }
    const dropped = [...outlinesRef.current].filter(([key]) => !wanted.has(key));
    if (dropped.length > 0) {
      for (const [, entry] of dropped) {
        if (entry.status === "ready") entry.value.close();
      }
      setOutlines((current) => {
        const next = new Map(current);
        for (const [key] of dropped) next.delete(key);
        return next;
      });
    }
    for (const request of requests) {
      if (inflight.current.has(request.key) || outlinesRef.current.has(request.key)) continue;
      const controller = new AbortController();
      inflight.current.set(request.key, controller);
      setOutlines((current) => new Map(current).set(request.key, { status: "loading" }));
      const started = performance.now();
      // A key asked for again after an abort has a new controller; leave it alone.
      const release = () => {
        if (inflight.current.get(request.key) === controller) inflight.current.delete(request.key);
      };
      loadOutline(request.url, controller.signal).then(
        (bitmap) => {
          release();
          if (controller.signal.aborted) {
            bitmap.close();
            return;
          }
          viewerDebug("outline:ready", {
            source: request.sourceKey,
            kind: request.kind,
            timepoint: request.timepoint + 1,
            ms: Math.round(performance.now() - started),
          });
          setOutlines((current) =>
            new Map(current).set(request.key, { status: "ready", value: bitmap }),
          );
        },
        (reason: unknown) => {
          release();
          if (controller.signal.aborted || isAbort(reason)) return;
          setOutlines((current) =>
            new Map(current).set(request.key, {
              status: "error",
              message: reason instanceof Error ? reason.message : String(reason),
            }),
          );
        },
      );
    }
  }, [requests]);

  useEffect(
    () => () => {
      for (const controller of inflight.current.values()) controller.abort();
      inflight.current.clear();
      for (const controller of countsInflight.current.values()) controller.abort();
      countsInflight.current.clear();
      for (const entry of outlinesRef.current.values()) {
        if (entry.status === "ready") entry.value.close();
      }
    },
    [],
  );

  const overlays = useMemo(() => {
    if (requests.length === 0) return NO_OVERLAYS;
    const bySource = new Map<string, ViewerOverlay[]>();
    for (const request of requests) {
      const loaded = outlines.get(request.key);
      if (loaded?.status !== "ready") continue;
      const list = bySource.get(request.sourceKey) ?? [];
      list.push({ kind: request.kind, image: loaded.value });
      bySource.set(request.sourceKey, list);
    }
    return bySource;
  }, [outlines, requests]);

  const outlineStates = requests.map((request) => outlines.get(request.key));
  return {
    models,
    model,
    counts,
    overlays,
    outlinesLoading: outlineStates.some((state) => state == null || state.status === "loading"),
    outlineErrors: [
      ...new Set(
        outlineStates.flatMap((state) => (state?.status === "error" ? [state.message] : [])),
      ),
    ],
  };
}
