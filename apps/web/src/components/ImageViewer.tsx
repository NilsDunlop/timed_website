import {
  DETAIL_VIEW_ID,
  DetailView,
  getChannelStats,
  getDefaultInitialViewState,
  loadOmeZarr,
  loadOmeZarrFromStore,
  PictureInPictureViewer,
  VivViewer,
} from "@hms-dbmi/viv";
import { SCALEBAR_VIEW_ID, ScaleBarView } from "@vivjs/views";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { components } from "../lib/api/dataserver-schema";
import { apiErrorMessage, dataServerClient } from "../lib/api/client";
import {
  cacheTileLoader,
  type CacheableTileSource,
  prefetchTimepoint,
  viewerTileCache,
} from "../lib/viewerTileCache";
import PlateMetadataPanel, {
  type PlateMetadata,
  wellIdFromPath,
} from "./PlateMetadataPanel";

type ImageSummary = components["schemas"]["ImageSummary"];
type Summary =
  | ImageSummary
  | components["schemas"]["PlateSummary"];
type Loader = Awaited<ReturnType<typeof loadOmeZarr>>["data"];
type Source = Loader[number];
type Dimensions = { width: number; height: number };
type CameraViewState = {
  id: string;
  target: number[];
  zoom: number;
  [key: string]: unknown;
};
type VivViewStateChangeEvent = {
  viewId: string;
  viewState: object;
  oldViewState: object;
};
type VivViewStateChange = (event: VivViewStateChangeEvent) => object;

type ChannelState = {
  name: string;
  color: [number, number, number];
  visible: boolean;
  domain: [number, number];
  contrast: [number, number];
};

const fallbackColors: [number, number, number][] = [
  [0, 0, 255],
  [0, 255, 0],
  [255, 0, 0],
  [255, 255, 255],
  [255, 0, 255],
  [0, 255, 255],
];

type ByteRange =
  | { offset: number; length: number }
  | { suffixLength: number };

class V3FetchStore {
  private root: URL;

  constructor(root: string) {
    this.root = new URL(root);
    if (!this.root.pathname.endsWith("/")) this.root.pathname += "/";
  }

  private url(key: string): URL {
    return new URL(key.replace(/^\//, ""), this.root);
  }

  private async read(response: Response): Promise<Uint8Array | undefined> {
    if (response.status === 404) return undefined;
    if (response.status !== 200 && response.status !== 206) {
      throw new Error(
        "Unexpected Zarr response " +
          String(response.status) +
          " " +
          response.statusText,
      );
    }
    return new Uint8Array(await response.arrayBuffer());
  }

  async get(
    key: string,
    options: RequestInit = {},
  ): Promise<Uint8Array | undefined> {
    if (key.endsWith("/.zattrs") || key.endsWith("/.zgroup")) {
      return undefined;
    }
    return this.read(await fetch(this.url(key), options));
  }

  async getRange(
    key: string,
    range: ByteRange,
    options: RequestInit = {},
  ): Promise<Uint8Array | undefined> {
    const headers = new Headers(options.headers);
    if ("suffixLength" in range) {
      headers.set("Range", "bytes=-" + String(range.suffixLength));
    } else {
      headers.set(
        "Range",
        "bytes=" +
          String(range.offset) +
          "-" +
          String(range.offset + range.length - 1),
      );
    }
    return this.read(await fetch(this.url(key), { ...options, headers }));
  }
}

function parseColor(value: string | null | undefined, index: number) {
  if (value && /^[0-9a-f]{6}$/i.test(value)) {
    return [
      Number.parseInt(value.slice(0, 2), 16),
      Number.parseInt(value.slice(2, 4), 16),
      Number.parseInt(value.slice(4, 6), 16),
    ] as [number, number, number];
  }
  return fallbackColors[index % fallbackColors.length];
}

function imageFromSummary(summary: Summary): ImageSummary | null {
  return summary.kind === "image" ? summary : (summary.first_image ?? null);
}

function axisSize(source: Source, axis: string): number {
  const index = Array.from(source.labels).indexOf(axis);
  return index >= 0 ? source.shape[index] ?? 1 : 1;
}

function selectionFor(
  source: Source,
  channel: number,
  time: number,
  z: number,
): Record<string, number> {
  const values: Record<string, number> = {};
  for (const label of source.labels) {
    if (label === "x" || label === "y" || label === "_c") continue;
    if (label === "c") values[label] = channel;
    else if (label === "t") values[label] = time;
    else if (label === "z") values[label] = z;
    else values[label] = 0;
  }
  return values;
}

function encodedZarrPath(datasetId: string, imagePath: string): string {
  const encodedPath = imagePath
    .split("/")
    .filter(Boolean)
    .map(encodeURIComponent)
    .join("/");
  const path = (
    "/backend/zarr/" +
    encodeURIComponent(datasetId) +
    "/" +
    encodedPath
  ).replace(/\/$/, "");
  return new URL(path, window.location.origin).href;
}

async function channelStates(
  loader: Loader,
  image: ImageSummary,
): Promise<ChannelState[]> {
  const source = loader.at(-1);
  if (!source) return [];
  const channelCount = Math.max(axisSize(source, "c"), image.channels.length, 1);
  const states: ChannelState[] = [];

  for (let index = 0; index < channelCount; index += 1) {
    const metadata = image.channels[index];
    const minimum = metadata?.window?.min ?? 0;
    const maximum = metadata?.window?.max ?? 65535;
    let start = metadata?.window?.start;
    let end = metadata?.window?.end;
    let domain: [number, number] = [minimum, maximum];

    if (start == null || end == null) {
      try {
        const raster = await source.getRaster({
          selection: selectionFor(source, index, 0, 0),
        });
        const statistics = getChannelStats(raster.data);
        domain = [statistics.domain[0], statistics.domain[1]];
        [start, end] = statistics.contrastLimits;
      } catch {
        start = minimum;
        end = maximum;
      }
    }

    states.push({
      name: metadata?.name ?? "Channel " + String(index + 1),
      color: parseColor(metadata?.color, index),
      visible: true,
      domain,
      contrast: [start ?? domain[0], domain[1]],
    });
  }
  return states;
}

type VivCanvasProps = {
  loader: Loader;
  channels: ChannelState[];
  selections: Record<string, number>[];
  viewState: CameraViewState | null;
  onViewStateChange: VivViewStateChange;
  onReady: () => void;
  onResize?: (dimensions: Dimensions) => void;
  maxRequests: number;
};

const VivCanvas = memo(function VivCanvas({
  loader,
  channels,
  selections,
  viewState,
  onViewStateChange,
  onReady,
  onResize,
  maxRequests,
}: VivCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState<Dimensions>({
    width: 900,
    height: 600,
  });

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const update = () => {
      const next = {
        width: Math.max(320, Math.floor(container.clientWidth)),
        height: Math.max(320, Math.floor(container.clientHeight)),
      };
      setDimensions(next);
      onResize?.(next);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(container);
    return () => observer.disconnect();
  }, [onResize]);

  const viewer = loader.length > 1 ? (
    <PictureInPictureViewer
      loader={loader}
      contrastLimits={channels.map((channel) => channel.contrast)}
      colors={channels.map((channel) => channel.color)}
      channelsVisible={channels.map((channel) => channel.visible)}
      selections={selections}
      overview={{}}
      overviewOn
      height={dimensions.height}
      width={dimensions.width}
      viewStates={viewState ? [viewState] : undefined}
      onViewStateChange={onViewStateChange}
      onViewportLoad={onReady}
    />
  ) : (
    <VivViewer
      views={[
        new DetailView({
          id: DETAIL_VIEW_ID,
          height: dimensions.height,
          width: dimensions.width,
        }),
        new ScaleBarView({
          id: SCALEBAR_VIEW_ID,
          height: dimensions.height,
          width: dimensions.width,
          loader,
          imageViewId: DETAIL_VIEW_ID,
        }),
      ]}
      layerProps={[
        {
          loader,
          contrastLimits: channels.map((channel) => channel.contrast),
          colors: channels.map((channel) => channel.color),
          channelsVisible: channels.map((channel) => channel.visible),
          selections,
          excludeBackground: true,
          maxRequests,
          onViewportLoad: onReady,
        },
        {
          loader,
          contrastLimits: channels.map((channel) => channel.contrast),
          colors: channels.map((channel) => channel.color),
          channelsVisible: channels.map((channel) => channel.visible),
          selections,
          excludeBackground: true,
          maxRequests,
          onViewportLoad: onReady,
        },
      ]}
      viewStates={[
        viewState ?? {
          ...getDefaultInitialViewState(loader, dimensions, 0.5),
          id: DETAIL_VIEW_ID,
        },
        {
          id: SCALEBAR_VIEW_ID,
          zoom: 0,
          target: [dimensions.width / 2, dimensions.height / 2, 0],
        },
      ]}
      onViewStateChange={onViewStateChange}
    />
  );

  return (
    <div className="canvas-frame" ref={containerRef}>
      {viewer}
    </div>
  );
});

export default function ImageViewer() {
  const [primaryDimensions, setPrimaryDimensions] = useState<Dimensions>({
    width: 900,
    height: 600,
  });
  const [datasetName, setDatasetName] = useState("");
  const [imagePath, setImagePath] = useState("");
  const [comparePath, setComparePath] = useState("");
  const [image, setImage] = useState<ImageSummary | null>(null);
  const [plateMetadata, setPlateMetadata] = useState<PlateMetadata | null>(null);
  const [loader, setLoader] = useState<Loader | null>(null);
  const [compareLoader, setCompareLoader] = useState<Loader | null>(null);
  const [channels, setChannels] = useState<ChannelState[]>([]);
  const [requestedTime, setRequestedTime] = useState(0);
  const [renderTime, setRenderTime] = useState(0);
  const [displayedTime, setDisplayedTime] = useState<number | null>(null);
  const [z, setZ] = useState(0);
  const [viewState, setViewState] = useState<CameraViewState | null>(null);
  const [controlsOpen, setControlsOpen] = useState(true);
  const [status, setStatus] = useState("Loading metadata…");
  const [error, setError] = useState("");
  const readyImages = useRef(new Set<string>());
  const activeFrame = useRef("");
  const timeDirection = useRef<1 | -1>(1);

  useEffect(() => {
    let active = true;
    viewerTileCache.clear();
    async function load() {
      try {
        const query = new URLSearchParams(window.location.search);
        const datasetId = query.get("dataset");
        const selectedPath = query.get("path");
        const requestedComparison = query.get("compare");
        const comparisonPath =
          requestedComparison && requestedComparison !== selectedPath
            ? requestedComparison
            : "";
        if (!datasetId || selectedPath == null) {
          throw new Error("Choose an image field from the Data page first.");
        }

        const [datasetsResult, summaryResult, metadataResult] = await Promise.all([
          dataServerClient.GET("/api/datasets"),
          dataServerClient.GET("/api/datasets/{dataset_id}/summary", {
            params: { path: { dataset_id: datasetId } },
          }),
          dataServerClient.GET("/api/datasets/{dataset_id}/metadata", {
            params: { path: { dataset_id: datasetId } },
          }),
        ]);
        if (!datasetsResult.data || !summaryResult.data) {
          throw new Error(
            apiErrorMessage(
              datasetsResult.error ?? summaryResult.error,
              "Could not load the linked dataset",
            ),
          );
        }
        const record = datasetsResult.data.datasets.find(
          (dataset) => dataset.id === datasetId,
        );
        const selectedImage = imageFromSummary(summaryResult.data);
        if (!record || !selectedImage) {
          throw new Error("The linked dataset has no image metadata.");
        }
        const linkedDatasetId = datasetId;
        const zarrFormat = selectedImage.zarr_format;

        async function loadPath(path: string) {
          const zarrRoot = encodedZarrPath(linkedDatasetId, path);
          return zarrFormat === 3
            ? loadOmeZarrFromStore(
                new V3FetchStore(zarrRoot) as Parameters<
                  typeof loadOmeZarrFromStore
                >[0],
              )
            : loadOmeZarr(zarrRoot, { type: "multiscales" });
        }

        const paths = comparisonPath
          ? [selectedPath, comparisonPath]
          : [selectedPath];
        const loadedImages = await Promise.all(paths.map(loadPath));
        const loaded = loadedImages[0];
        const comparison = loadedImages[1];
        for (const [index, loadedImage] of loadedImages.entries()) {
          cacheTileLoader(
            loadedImage.data as unknown as CacheableTileSource[],
            linkedDatasetId + "/" + paths[index],
            viewerTileCache,
          );
          for (const source of loadedImage.data) {
            source.onTileError = (reason: Error) => {
              if (active) {
                setError(
                  "Could not load a chunk for " +
                    paths[index] +
                    ": " +
                    reason.message,
                );
              }
            };
          }
        }
        const initialChannels = await channelStates(loaded.data, selectedImage);
        if (!active) return;
        readyImages.current.clear();
        setRequestedTime(0);
        setRenderTime(0);
        setDisplayedTime(null);
        setDatasetName(record.name);
        setImagePath(selectedPath || selectedImage.path);
        setComparePath(comparisonPath);
        setImage(selectedImage);
        setPlateMetadata(metadataResult.data ?? null);
        setLoader(loaded.data);
        setCompareLoader(comparison?.data ?? null);
        setChannels(initialChannels);
        setViewState(null);
        setStatus(comparison ? "Rendering images…" : "Rendering image…");
      } catch (reason) {
        if (active) {
          setError(reason instanceof Error ? reason.message : String(reason));
          setStatus("Viewer failed");
        }
      }
    }
    void load();
    return () => {
      active = false;
      viewerTileCache.clear();
    };
  }, []);

  const source = loader?.[0];
  const comparisonSource = compareLoader?.[0];
  const timeCount = source
    ? Math.min(
        axisSize(source, "t"),
        comparisonSource ? axisSize(comparisonSource, "t") : Number.POSITIVE_INFINITY,
      )
    : 1;
  const zCount = source
    ? Math.min(
        axisSize(source, "z"),
        comparisonSource ? axisSize(comparisonSource, "z") : Number.POSITIVE_INFINITY,
      )
    : 1;
  const channelCount = channels.length;
  const comparing = Boolean(compareLoader && comparePath);
  const selections = useMemo(
    () =>
      source
        ? Array.from({ length: channelCount }, (_unused, index) =>
            selectionFor(source, index, renderTime, z),
          )
        : [],
    [channelCount, source, renderTime, z],
  );
  const comparisonSelections = useMemo(
    () =>
      comparisonSource
        ? Array.from({ length: channelCount }, (_unused, index) =>
            selectionFor(comparisonSource, index, renderTime, z),
          )
        : [],
    [channelCount, comparisonSource, renderTime, z],
  );
  const frameKey = renderTime + ":" + z;
  const requestBudget = Math.max(
    1,
    Math.floor(6 / Math.max(1, channelCount * (comparing ? 2 : 1))),
  );

  const commitTime = useCallback((nextTime: number) => {
    setRenderTime((currentTime) => {
      if (nextTime !== currentTime) {
        timeDirection.current = nextTime > currentTime ? 1 : -1;
      }
      return nextTime;
    });
  }, []);

  useEffect(() => {
    if (requestedTime === renderTime) return;
    const timer = window.setTimeout(() => commitTime(requestedTime), 150);
    return () => window.clearTimeout(timer);
  }, [commitTime, renderTime, requestedTime]);

  useEffect(() => {
    if (!loader) return;
    activeFrame.current = frameKey;
    readyImages.current.clear();
    setStatus(
      comparing
        ? "Loading time " + String(renderTime + 1) + " in both images…"
        : "Loading time " + String(renderTime + 1) + "…",
    );
  }, [compareLoader, comparing, frameKey, loader, renderTime]);

  useEffect(() => {
    if (loader && requestedTime !== renderTime) {
      setStatus("Preparing time " + String(requestedTime + 1) + "…");
    }
  }, [loader, renderTime, requestedTime]);

  useEffect(() => {
    if (
      !loader ||
      loader.length !== 1 ||
      requestedTime !== renderTime ||
      displayedTime !== renderTime ||
      timeCount <= 1 ||
      channelCount === 0
    ) {
      return;
    }

    let targetTime = renderTime + timeDirection.current;
    if (targetTime < 0 || targetTime >= timeCount) {
      targetTime = renderTime - timeDirection.current;
    }
    if (targetTime < 0 || targetTime >= timeCount) return;

    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      const nextSelections = Array.from(
        { length: channelCount },
        (_unused, index) => selectionFor(loader[0], index, targetTime, z),
      );
      const primary = prefetchTimepoint(
        loader as unknown as CacheableTileSource[],
        nextSelections,
        controller.signal,
        comparing ? 3 : 6,
      );

      let work = primary;
      if (comparing && compareLoader?.length === 1) {
        const nextComparisonSelections = Array.from(
          { length: channelCount },
          (_unused, index) =>
            selectionFor(compareLoader[0], index, targetTime, z),
        );
        work = Promise.all([
          primary,
          prefetchTimepoint(
            compareLoader as unknown as CacheableTileSource[],
            nextComparisonSelections,
            controller.signal,
            3,
          ),
        ]).then(() => undefined);
      }
      void work.catch(() => {
        // Prefetch is opportunistic; foreground rendering reports real failures.
      });
    }, 250);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [
    channelCount,
    compareLoader,
    comparing,
    displayedTime,
    loader,
    renderTime,
    requestedTime,
    timeCount,
    z,
  ]);

  function updateChannel(index: number, next: Partial<ChannelState>) {
    setChannels((current) =>
      current.map((channel, channelIndex) =>
        channelIndex === index ? { ...channel, ...next } : channel,
      ),
    );
  }

  function defaultViewState(): CameraViewState | null {
    if (!loader) return null;
    const initial = getDefaultInitialViewState(
      loader,
      primaryDimensions,
      0.5,
    ) as Pick<CameraViewState, "target" | "zoom">;
    return { ...initial, id: DETAIL_VIEW_ID };
  }

  function zoomBy(delta: number) {
    const current = viewState ?? defaultViewState();
    if (!current) return;
    setViewState({ ...current, id: DETAIL_VIEW_ID, zoom: current.zoom + delta });
  }

  function resetView() {
    setViewState(defaultViewState());
  }

  function handleViewStateChange(event: VivViewStateChangeEvent): object {
    const camera = event.viewState as Partial<CameraViewState>;
    if (
      event.viewId === DETAIL_VIEW_ID &&
      Array.isArray(camera.target) &&
      typeof camera.zoom === "number"
    ) {
      setViewState({ ...camera, id: DETAIL_VIEW_ID } as CameraViewState);
    }
    return event.viewState;
  }

  const markReady = useCallback((path: string, readyFrame: string, readyTime: number) => {
    if (readyFrame !== activeFrame.current) return;
    readyImages.current.add(path);
    const expected = comparing ? 2 : 1;
    if (readyImages.current.size >= expected) {
      setDisplayedTime(readyTime);
      setStatus(comparing ? "Images ready" : "Image ready");
    }
  }, [comparing]);

  const markPrimaryReady = useCallback(
    () => markReady(imagePath, frameKey, renderTime),
    [frameKey, imagePath, markReady, renderTime],
  );
  const markComparisonReady = useCallback(
    () => markReady(comparePath, frameKey, renderTime),
    [comparePath, frameKey, markReady, renderTime],
  );

  const firstLevel = image?.levels[0];
  return (
    <div
      className={
        "viewer-layout" + (controlsOpen ? "" : " viewer-layout-controls-hidden")
      }
    >
      {controlsOpen && (
        <aside id="image-controls" className="panel viewer-controls stack">
        <div>
          <p className="eyebrow">Image controls</p>
          <h2>Time and channels</h2>
        </div>

        {timeCount > 1 && (
          <label className="viewer-time-control">
            <span>Time: {requestedTime + 1} / {timeCount}</span>
            <input
              data-testid="time-slider"
              type="range"
              min={0}
              max={timeCount - 1}
              value={requestedTime}
              onChange={(event) => setRequestedTime(Number(event.target.value))}
              onPointerUp={() => commitTime(requestedTime)}
            />
            {displayedTime != null && displayedTime !== requestedTime && (
              <small className="viewer-time-state" data-testid="displayed-time-hint">
                Showing time {displayedTime + 1} while time {requestedTime + 1} loads
              </small>
            )}
          </label>
        )}
        {zCount > 1 && (
          <label>
            <span>Z: {z + 1} / {zCount}</span>
            <input
              data-testid="z-slider"
              type="range"
              min={0}
              max={zCount - 1}
              value={z}
              onChange={(event) => setZ(Number(event.target.value))}
            />
          </label>
        )}

        <h3 className="viewer-channel-heading">Channels</h3>

        {channels.map((channel, index) => {
          const step = Math.max(
            1,
            (channel.domain[1] - channel.domain[0]) / 1000,
          );
          return (
            <fieldset key={channel.name + String(index)} className="channel-control">
              <legend>
                <label>
                  <input
                    type="checkbox"
                    checked={channel.visible}
                    onChange={(event) =>
                      updateChannel(index, { visible: event.target.checked })
                    }
                  />
                  <span
                    className="channel-swatch"
                    style={{
                      backgroundColor:
                        "rgb(" + channel.color.join(",") + ")",
                    }}
                  />
                  {channel.name}
                </label>
              </legend>
              <label>
                <span>Contrast minimum: {Math.round(channel.contrast[0])}</span>
                <input
                  aria-label={channel.name + " contrast minimum"}
                  type="range"
                  min={channel.domain[0]}
                  max={channel.domain[1]}
                  step={step}
                  value={channel.contrast[0]}
                  onChange={(event) => {
                    const value = Number(event.target.value);
                    updateChannel(index, {
                      contrast: [Math.min(value, channel.contrast[1]), channel.contrast[1]],
                    });
                  }}
                />
              </label>
              <label>
                <span>Contrast maximum: {Math.round(channel.contrast[1])}</span>
                <input
                  aria-label={channel.name + " contrast maximum"}
                  type="range"
                  min={channel.domain[0]}
                  max={channel.domain[1]}
                  step={step}
                  value={channel.contrast[1]}
                  onChange={(event) => {
                    const value = Number(event.target.value);
                    updateChannel(index, {
                      contrast: [channel.contrast[0], Math.max(value, channel.contrast[0])],
                    });
                  }}
                />
              </label>
            </fieldset>
          );
        })}
        <a href="/data">← Back to data</a>
        </aside>
      )}

      <section className="viewer-stage stack" aria-labelledby="viewer-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">
              {comparing ? "Synchronized viewer" : "Viewer"}
            </p>
            <h1 id="viewer-heading">
              {comparing ? "Compare trajectories" : imagePath || "Loading image"}
            </h1>
          </div>
          <div className="viewer-heading-actions">
            <span
              className={
                status === "Image ready" || status === "Images ready"
                  ? "status-ready"
                  : "muted"
              }
              data-testid="viewer-status"
            >
              {status}
            </span>
            <button
              type="button"
              className="secondary compact-button"
              aria-expanded={controlsOpen}
              aria-controls="image-controls"
              onClick={() => setControlsOpen((open) => !open)}
            >
              {controlsOpen ? "Hide controls" : "Show controls"}
            </button>
          </div>
        </div>

        {error && (
          <div className="notice error" role="alert">
            {error}
          </div>
        )}

        <div className="viewer-canvas-area">
          <div className="viewer-navigation" role="group" aria-label="Image navigation">
            <button
              type="button"
              className="viewer-icon-button"
              aria-label="Zoom out"
              title="Zoom out"
              disabled={!loader}
              onClick={() => zoomBy(-0.5)}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <circle cx="10.5" cy="10.5" r="6.5" />
                <path d="M15.5 15.5 21 21M7.5 10.5h6" />
              </svg>
            </button>
            <button
              type="button"
              className="viewer-icon-button"
              aria-label="Reset view to center"
              title="Reset view to center"
              disabled={!loader}
              onClick={resetView}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5" />
                <circle cx="12" cy="12" r="2.5" />
              </svg>
            </button>
            <button
              type="button"
              className="viewer-icon-button"
              aria-label="Zoom in"
              title="Zoom in"
              disabled={!loader}
              onClick={() => zoomBy(0.5)}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <circle cx="10.5" cy="10.5" r="6.5" />
                <path d="M15.5 15.5 21 21M7.5 10.5h6M10.5 7.5v6" />
              </svg>
            </button>
          </div>

          {loader && channels.length > 0 &&
            (comparing && compareLoader ? (
              <div className="comparison-view-grid" data-testid="comparison-view">
                <article className="comparison-view">
                  <header>
                    <span className="comparison-index">A</span>
                    <div>
                      <strong>{wellIdFromPath(imagePath) || "First well"}</strong>
                      <small>{imagePath}</small>
                    </div>
                  </header>
                  <VivCanvas
                    loader={loader}
                    channels={channels}
                    selections={selections}
                    viewState={viewState}
                    onViewStateChange={handleViewStateChange}
                    onReady={markPrimaryReady}
                    onResize={setPrimaryDimensions}
                    maxRequests={requestBudget}
                  />
                </article>
                <article className="comparison-view">
                  <header>
                    <span className="comparison-index">B</span>
                    <div>
                      <strong>{wellIdFromPath(comparePath) || "Second well"}</strong>
                      <small>{comparePath}</small>
                    </div>
                  </header>
                  <VivCanvas
                    loader={compareLoader}
                    channels={channels}
                    selections={comparisonSelections}
                    viewState={viewState}
                    onViewStateChange={handleViewStateChange}
                    onReady={markComparisonReady}
                    maxRequests={requestBudget}
                  />
                </article>
              </div>
            ) : (
              <VivCanvas
                loader={loader}
                channels={channels}
                selections={selections}
                viewState={viewState}
                onViewStateChange={handleViewStateChange}
                onReady={markPrimaryReady}
                onResize={setPrimaryDimensions}
                maxRequests={requestBudget}
              />
            ))}
        </div>

        <p className="viewer-status-line">
          <strong>{datasetName || "Dataset"}</strong>
          <span>{imagePath}</span>
          {comparing && <span>vs {comparePath}</span>}
          <span>
            shape {firstLevel?.shape.join(" × ") ?? source?.shape.join(" × ") ?? "—"}
          </span>
          <span>dtype {firstLevel?.dtype ?? source?.dtype ?? "—"}</span>
          <span data-testid="displayed-time">
            t {displayedTime == null ? "—" : displayedTime + 1} / {timeCount}
          </span>
        </p>

        {comparing ? (
          <div className="comparison-metadata-grid">
            <div className="stack">
              <h2>Well A</h2>
              <PlateMetadataPanel
                metadata={plateMetadata}
                selectedWell={wellIdFromPath(imagePath)}
              />
            </div>
            <div className="stack">
              <h2>Well B</h2>
              <PlateMetadataPanel
                metadata={plateMetadata}
                selectedWell={wellIdFromPath(comparePath)}
              />
            </div>
          </div>
        ) : (
          <PlateMetadataPanel
            metadata={plateMetadata}
            selectedWell={wellIdFromPath(imagePath)}
          />
        )}
      </section>
    </div>
  );
}
