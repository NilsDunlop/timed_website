// Segmentation outlines for every viewer source: each source's mask models,
// the selected model's counts for its field, and one outline image per kind
// that is switched on. The settings are shared by all sources.
//
// Listings and counts load in parallel with the image. Outlines follow the
// displayed timepoint, so they wait until a frame is on screen and never run
// ahead of it; Cell Health fields have a single timepoint.

import { useEffect, useMemo, useRef, useState } from "react";

import { apiErrorMessage, dataServerClient } from "../../lib/api/client";
import { parseFieldPath } from "../../lib/fieldPath";
import {
  chooseMaskModel,
  maskOutlineUrl,
  sharedMaskModels,
  type MaskCounts,
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
  /** The selected model's counts, by source key. */
  counts: ReadonlyMap<string, Loaded<MaskCounts>>;
  /** Ready outline images by source key, in drawing order. */
  overlays: ReadonlyMap<string, readonly ViewerOverlay[]>;
  outlinesLoading: boolean;
  outlineErrors: readonly string[];
};

type OutlineRequest = {
  key: string;
  sourceKey: string;
  kind: string;
  url: string;
};

const NO_OVERLAYS: ReadonlyMap<string, readonly ViewerOverlay[]> = new Map();

function isAbort(reason: unknown): boolean {
  return reason instanceof DOMException && reason.name === "AbortError";
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

export function useMaskOverlays(
  sources: readonly MaskSource[],
  settings: MaskSettings,
  timepoint: number | null,
): MaskOverlays {
  const datasetIds = useMemo(
    () => [...new Set(sources.map((source) => source.datasetId))],
    [sources],
  );
  const [catalogues, setCatalogues] = useState<
    ReadonlyMap<string, Loaded<readonly MaskModel[]>>
  >(() => new Map());

  useEffect(() => {
    const controller = new AbortController();
    setCatalogues(new Map(datasetIds.map((id) => [id, { status: "loading" }])));
    for (const datasetId of datasetIds) {
      const started = performance.now();
      void dataServerClient
        .GET("/api/datasets/{dataset_id}/masks", {
          params: { path: { dataset_id: datasetId } },
          signal: controller.signal,
        })
        .then(({ data, error }) => {
          viewerDebug("masks:listing-ready", {
            datasetId,
            models: data?.models.length ?? null,
            ms: Math.round(performance.now() - started),
          });
          setCatalogues((current) =>
            new Map(current).set(
              datasetId,
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
            new Map(current).set(datasetId, {
              status: "error",
              message: "The mask models could not be loaded",
            }),
          );
        });
    }
    return () => controller.abort();
  }, [datasetIds]);

  const models = useMemo(
    () =>
      sharedMaskModels(
        sources.map((source) => {
          const catalogue = catalogues.get(source.datasetId);
          return catalogue?.status === "ready" ? catalogue.value : null;
        }),
      ),
    [catalogues, sources],
  );
  const model = chooseMaskModel(models, settings.model);
  const modelTag = model?.model_tag ?? null;

  const [counts, setCounts] = useState<ReadonlyMap<string, Loaded<MaskCounts>>>(
    () => new Map(),
  );
  useEffect(() => {
    if (!modelTag) {
      setCounts(new Map());
      return;
    }
    const controller = new AbortController();
    const fields = sources.flatMap((source) => {
      const field = parseFieldPath(source.path);
      return field ? [{ source, field }] : [];
    });
    setCounts(new Map(fields.map(({ source }) => [source.key, { status: "loading" }])));
    for (const { source, field } of fields) {
      const started = performance.now();
      void dataServerClient
        .GET("/api/datasets/{dataset_id}/masks/{model}/{well}/{site}", {
          params: {
            path: {
              dataset_id: source.datasetId,
              model: modelTag,
              well: field.well,
              site: field.site,
            },
          },
          signal: controller.signal,
        })
        .then(({ data, error }) => {
          viewerDebug("masks:counts-ready", {
            source: source.key,
            model: modelTag,
            ms: Math.round(performance.now() - started),
          });
          setCounts((current) =>
            new Map(current).set(
              source.key,
              data
                ? { status: "ready", value: data.counts }
                : {
                    status: "error",
                    message: apiErrorMessage(error, "The mask counts could not be loaded"),
                  },
            ),
          );
        })
        .catch((reason: unknown) => {
          if (controller.signal.aborted || isAbort(reason)) return;
          setCounts((current) =>
            new Map(current).set(source.key, {
              status: "error",
              message: "The mask counts could not be loaded",
            }),
          );
        });
    }
    return () => controller.abort();
  }, [modelTag, sources]);

  const kindsKey = settings.kinds.join(",");
  const requests = useMemo<OutlineRequest[]>(() => {
    if (!model || timepoint == null) return [];
    const visible = model.kinds.filter((kind) => kindsKey.split(",").includes(kind));
    return sources.flatMap((source) => {
      const field = parseFieldPath(source.path);
      const sourceCounts = counts.get(source.key);
      if (!field || sourceCounts?.status !== "ready") return [];
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
          );
          return {
            key: `${source.key}\u0000${timepoint}\u0000${url}`,
            sourceKey: source.key,
            kind,
            url,
          };
        });
    });
  }, [counts, kindsKey, model, settings.width, sources, timepoint]);

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
      loadOutline(request.url, controller.signal).then(
        (bitmap) => {
          inflight.current.delete(request.key);
          if (controller.signal.aborted) {
            bitmap.close();
            return;
          }
          viewerDebug("outline:ready", {
            source: request.sourceKey,
            kind: request.kind,
            ms: Math.round(performance.now() - started),
          });
          setOutlines((current) =>
            new Map(current).set(request.key, { status: "ready", value: bitmap }),
          );
        },
        (reason: unknown) => {
          inflight.current.delete(request.key);
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
