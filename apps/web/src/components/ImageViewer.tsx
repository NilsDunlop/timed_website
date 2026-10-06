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
  cacheImageLoader,
  type CacheableImageSource,
  prefetchTimepoint,
  viewerPixelCache,
} from "../lib/viewerTileCache";
import {
  moveTimeIndex,
  prefetchTimeIndex,
  timeIntervalHours,
  timeJumpOptions,
} from "../lib/timeNavigation";
import { FrameReadinessTracker } from "../lib/frameReadiness";
import { V3FetchStore } from "../lib/v3FetchStore";
import { viewerDebug } from "../lib/viewerDebug";
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
  const [datasetId, setDatasetId] = useState("");
  const [datasetName, setDatasetName] = useState("");
  const [compareDatasetId, setCompareDatasetId] = useState("");
  const [compareDatasetName, setCompareDatasetName] = useState("");
  const [imagePath, setImagePath] = useState("");
  const [comparePath, setComparePath] = useState("");
  const [image, setImage] = useState<ImageSummary | null>(null);
  const [plateMetadata, setPlateMetadata] = useState<PlateMetadata | null>(null);
  const [comparePlateMetadata, setComparePlateMetadata] =
    useState<PlateMetadata | null>(null);
  const [loader, setLoader] = useState<Loader | null>(null);
  const [compareLoader, setCompareLoader] = useState<Loader | null>(null);
  const [channels, setChannels] = useState<ChannelState[]>([]);
  const [requestedTime, setRequestedTime] = useState(0);
  const [renderTime, setRenderTime] = useState(0);
  const [displayedTime, setDisplayedTime] = useState<number | null>(null);
  const [jumpFrames, setJumpFrames] = useState(1);
  const [z, setZ] = useState(0);
  const [viewState, setViewState] = useState<CameraViewState | null>(null);
  const [controlsOpen, setControlsOpen] = useState(true);
  const [status, setStatus] = useState("Loading metadata…");
  const [error, setError] = useState("");
  const frameReadiness = useRef(new FrameReadinessTracker());
  const settledFrame = useRef("");
  const timeDirection = useRef<1 | -1>(1);

  useEffect(() => {
    let active = true;
    viewerPixelCache.clear();
    async function load() {
      try {
        const query = new URLSearchParams(window.location.search);
        const primaryDatasetId = query.get("dataset");
        const selectedPath = query.get("path");
        const requestedComparison = query.get("compare");
        const requestedComparisonDataset =
          query.get("compareDataset") ?? primaryDatasetId;
        const comparisonPath =
          requestedComparison &&
          (requestedComparisonDataset !== primaryDatasetId ||
            requestedComparison !== selectedPath)
            ? requestedComparison
            : "";
        const comparisonDatasetId = comparisonPath
          ? requestedComparisonDataset
          : null;
        viewerDebug("viewer:load-start", {
          primary: { datasetId: primaryDatasetId, path: selectedPath },
          comparison: comparisonDatasetId
            ? { datasetId: comparisonDatasetId, path: comparisonPath }
            : null,
        });
        if (!primaryDatasetId || selectedPath == null) {
          throw new Error("Choose an image field from the Data page first.");
        }

        async function loadDatasetDetails(sourceDatasetId: string) {
          const [summaryResult, metadataResult] = await Promise.all([
            dataServerClient.GET("/api/datasets/{dataset_id}/summary", {
              params: { path: { dataset_id: sourceDatasetId } },
            }),
            dataServerClient.GET("/api/datasets/{dataset_id}/metadata", {
              params: { path: { dataset_id: sourceDatasetId } },
            }),
          ]);
          if (!summaryResult.data) {
            throw new Error(
              apiErrorMessage(summaryResult.error, "Could not load dataset summary"),
            );
          }
          if (!metadataResult.data && metadataResult.response.status !== 404) {
            throw new Error(
              apiErrorMessage(metadataResult.error, "Could not load plate metadata"),
            );
          }
          return {
            summary: summaryResult.data,
            metadata: metadataResult.data ?? null,
          };
        }

        const [datasetsResult, primaryDetails, comparisonDetails] = await Promise.all([
          dataServerClient.GET("/api/datasets"),
          loadDatasetDetails(primaryDatasetId),
          comparisonDatasetId
            ? loadDatasetDetails(comparisonDatasetId)
            : Promise.resolve(null),
        ]);
        if (!datasetsResult.data) {
          throw new Error(
            apiErrorMessage(datasetsResult.error, "Could not load linked datasets"),
          );
        }
        const record = datasetsResult.data.datasets.find(
          (dataset) => dataset.id === primaryDatasetId,
        );
        const comparisonRecord = comparisonDatasetId
          ? datasetsResult.data.datasets.find(
              (dataset) => dataset.id === comparisonDatasetId,
            )
          : null;
        const selectedImage = imageFromSummary(primaryDetails.summary);
        const comparisonImage = comparisonDetails
          ? imageFromSummary(comparisonDetails.summary)
          : null;
        if (!record || !selectedImage) {
          throw new Error("The linked dataset has no image metadata.");
        }
        if (comparisonDatasetId && (!comparisonRecord || !comparisonImage)) {
          throw new Error("The comparison dataset has no image metadata.");
        }

        async function loadSource(source: {
          datasetId: string;
          path: string;
          image: ImageSummary;
        }) {
          const zarrRoot = encodedZarrPath(source.datasetId, source.path);
          return source.image.zarr_format === 3
            ? loadOmeZarrFromStore(
                new V3FetchStore(zarrRoot) as Parameters<
                  typeof loadOmeZarrFromStore
                >[0],
              )
            : loadOmeZarr(zarrRoot, { type: "multiscales" });
        }

        const sources = [
          {
            datasetId: primaryDatasetId,
            path: selectedPath,
            image: selectedImage,
          },
        ];
        if (comparisonDatasetId && comparisonImage) {
          sources.push({
            datasetId: comparisonDatasetId,
            path: comparisonPath,
            image: comparisonImage,
          });
        }
        viewerDebug("viewer:image-metadata-ready", {
          sources: sources.map((source) => ({
            datasetId: source.datasetId,
            path: source.path,
            zarrFormat: source.image.zarr_format,
          })),
          axes: selectedImage.axes,
          levels: selectedImage.levels.length,
        });
        const loadedImages = await Promise.all(sources.map(loadSource));
        const loaded = loadedImages[0];
        const comparison = loadedImages[1];
        for (const [index, loadedImage] of loadedImages.entries()) {
          const sourceSpec = sources[index];
          cacheImageLoader(
            loadedImage.data as unknown as CacheableImageSource[],
            sourceSpec.datasetId + "/" + sourceSpec.path,
            viewerPixelCache,
          );
          for (const source of loadedImage.data) {
            source.onTileError = (reason: Error) => {
              viewerDebug("viv:tile-error", {
                datasetId: sourceSpec.datasetId,
                path: sourceSpec.path,
                reason: reason.message,
              });
              if (active) {
                setError(
                  "Could not load a chunk for " +
                    sourceSpec.path +
                    ": " +
                    reason.message,
                );
              }
            };
          }
        }
        let initialChannels = await channelStates(loaded.data, selectedImage);
        if (comparison) {
          const comparisonSource = comparison.data.at(-1);
          const commonChannelCount = comparisonSource
            ? Math.min(initialChannels.length, axisSize(comparisonSource, "c"))
            : 0;
          if (commonChannelCount === 0) {
            throw new Error("The selected images have no comparable channels.");
          }
          initialChannels = initialChannels.slice(0, commonChannelCount);
        }
        if (!active) return;
        viewerDebug("viewer:loader-ready", {
          sources: sources.map((source) => ({
            datasetId: source.datasetId,
            path: source.path,
          })),
          channels: initialChannels.length,
          resolutions: loaded.data.length,
          comparison: Boolean(comparison),
        });
        frameReadiness.current.clear();
        settledFrame.current = "";
        setRequestedTime(0);
        setRenderTime(0);
        setDisplayedTime(null);
        setJumpFrames(1);
        setDatasetId(primaryDatasetId);
        setDatasetName(record.name);
        setCompareDatasetId(comparisonDatasetId ?? "");
        setCompareDatasetName(comparisonRecord?.name ?? "");
        setImagePath(selectedPath || selectedImage.path);
        setComparePath(comparisonPath);
        setImage(selectedImage);
        setPlateMetadata(primaryDetails.metadata);
        setComparePlateMetadata(comparisonDetails?.metadata ?? null);
        setLoader(loaded.data);
        setCompareLoader(comparison?.data ?? null);
        setChannels(initialChannels);
        setViewState(null);
        setStatus(comparison ? "Rendering images…" : "Rendering image…");
      } catch (reason) {
        viewerDebug("viewer:load-error", {
          reason: reason instanceof Error ? reason.message : String(reason),
        });
        if (active) {
          setError(reason instanceof Error ? reason.message : String(reason));
          setStatus("Viewer failed");
        }
      }
    }
    void load();
    return () => {
      active = false;
      viewerDebug("viewer:unmount");
      viewerPixelCache.clear();
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
  const imageReadyKey = datasetId + "\u0000" + imagePath;
  const compareReadyKey = compareDatasetId + "\u0000" + comparePath;
  const intervalHours = timeIntervalHours(
    image,
    plateMetadata?.plate?.acquisition?.interval_minutes,
  );
  const jumpOptions = useMemo(
    () => timeJumpOptions(timeCount, intervalHours),
    [intervalHours, timeCount],
  );
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

  const completeFrame = useCallback(
    (
      readyFrame: string,
      readyTime: number,
      readyPaths: string[],
      source: "active-callback" | "recorded-before-activation",
    ) => {
      const expected = comparing ? 2 : 1;
      if (
        frameReadiness.current.activeFrame !== readyFrame ||
        readyPaths.length < expected
      ) {
        return false;
      }
      if (settledFrame.current === readyFrame) return true;

      settledFrame.current = readyFrame;
      viewerDebug("frame:ready", {
        frameKey: readyFrame,
        timepoint: readyTime + 1,
        paths: readyPaths,
        source,
        cachedEntries: viewerPixelCache.size,
        cachedBytes: viewerPixelCache.byteLength,
      });
      setDisplayedTime(readyTime);
      setStatus(comparing ? "Images ready" : "Image ready");
      return true;
    },
    [comparing],
  );

  const commitTime = useCallback(
    (nextTime: number) => {
      const nextFrame = nextTime + ":" + z;
      if (nextFrame !== frameReadiness.current.activeFrame) {
        frameReadiness.current.prepare(nextFrame);
      }
      setRenderTime((currentTime) => {
        if (nextTime !== currentTime) {
          timeDirection.current = nextTime > currentTime ? 1 : -1;
          viewerDebug("time:commit", {
            from: currentTime + 1,
            to: nextTime + 1,
            direction: timeDirection.current,
          });
        }
        return nextTime;
      });
    },
    [z],
  );

  useEffect(() => {
    if (!jumpOptions.some((option) => option.frames === jumpFrames)) {
      setJumpFrames(jumpOptions[0]?.frames ?? 1);
    }
  }, [jumpFrames, jumpOptions]);

  useEffect(() => {
    if (requestedTime === renderTime) return;
    viewerDebug("time:debounce-scheduled", {
      requested: requestedTime + 1,
      rendered: renderTime + 1,
      delayMs: 150,
    });
    const timer = window.setTimeout(() => commitTime(requestedTime), 150);
    return () => window.clearTimeout(timer);
  }, [commitTime, renderTime, requestedTime]);

  useEffect(() => {
    if (!loader) return;
    const readiness = frameReadiness.current.activate(frameKey);
    viewerDebug("frame:render-start", {
      frameKey,
      timepoint: renderTime + 1,
      z: z + 1,
      comparison: comparing,
      requested: requestedTime + 1,
      recordedPaths: readiness.paths,
    });
    if (
      !completeFrame(
        frameKey,
        renderTime,
        readiness.paths,
        "recorded-before-activation",
      )
    ) {
      setStatus(
        comparing
          ? "Loading time " + String(renderTime + 1) + " in both images…"
          : "Loading time " + String(renderTime + 1) + "…",
      );
    }
  }, [
    compareLoader,
    comparing,
    completeFrame,
    frameKey,
    loader,
    renderTime,
  ]);

  useEffect(() => {
    if (loader && requestedTime !== renderTime) {
      viewerDebug("time:requested", {
        requested: requestedTime + 1,
        rendered: renderTime + 1,
        displayed: displayedTime == null ? null : displayedTime + 1,
      });
      setStatus("Preparing time " + String(requestedTime + 1) + "…");
    }
  }, [displayedTime, loader, renderTime, requestedTime]);

  useEffect(() => {
    if (!loader || displayedTime === renderTime) return;
    const waitingFor = frameKey;
    const timer = window.setTimeout(() => {
      if (frameReadiness.current.activeFrame !== waitingFor) return;
      viewerDebug("frame:still-waiting", {
        frameKey: waitingFor,
        requested: requestedTime + 1,
        rendered: renderTime + 1,
        displayed: displayedTime == null ? null : displayedTime + 1,
        readyPaths: frameReadiness.current.pathsFor(waitingFor),
        expectedPaths: comparing
          ? [imageReadyKey, compareReadyKey]
          : [imageReadyKey],
        cachedEntries: viewerPixelCache.size,
        cachedBytes: viewerPixelCache.byteLength,
        waitedMs: 10_000,
      });
    }, 10_000);
    return () => window.clearTimeout(timer);
  }, [
    comparePath,
    comparing,
    displayedTime,
    frameKey,
    imagePath,
    imageReadyKey,
    loader,
    compareReadyKey,
    renderTime,
    requestedTime,
  ]);

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

    const targetTime = prefetchTimeIndex(
      renderTime,
      timeDirection.current,
      jumpFrames,
      timeCount,
    );
    if (targetTime == null) return;

    const controller = new AbortController();
    let started = false;
    let completed = false;
    viewerDebug("prefetch:scheduled", {
      from: renderTime + 1,
      to: targetTime + 1,
      jumpFrames,
      direction: timeDirection.current,
      delayMs: 250,
    });
    const timer = window.setTimeout(() => {
      started = true;
      viewerDebug("prefetch:start", {
        from: renderTime + 1,
        to: targetTime + 1,
        comparison: comparing,
        strategy: "single-resolution-raster",
        rasters: channelCount * (comparing ? 2 : 1),
      });
      const nextSelections = Array.from(
        { length: channelCount },
        (_unused, index) => selectionFor(loader[0], index, targetTime, z),
      );
      const primary = prefetchTimepoint(
        loader as unknown as CacheableImageSource[],
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
            compareLoader as unknown as CacheableImageSource[],
            nextComparisonSelections,
            controller.signal,
            3,
          ),
        ]).then(() => undefined);
      }
      void work.then(
        () => {
          completed = true;
          viewerDebug("prefetch:complete", { timepoint: targetTime + 1 });
        },
        (reason: unknown) => {
          viewerDebug(
            controller.signal.aborted ? "prefetch:aborted" : "prefetch:error",
            {
              timepoint: targetTime + 1,
              reason: reason instanceof Error ? reason.message : String(reason),
            },
          );
        },
      );
    }, 250);

    return () => {
      window.clearTimeout(timer);
      if (!completed) {
        viewerDebug(
          started ? "prefetch:cancel-active" : "prefetch:cancel-scheduled",
          { timepoint: targetTime + 1 },
        );
      }
      controller.abort();
    };
  }, [
    channelCount,
    compareLoader,
    comparing,
    displayedTime,
    jumpFrames,
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

  const markReady = useCallback(
    (path: string, readyFrame: string, readyTime: number) => {
      const readiness = frameReadiness.current.record(readyFrame, path);
      viewerDebug("viv:viewport-load", {
        path,
        readyFrame,
        activeFrame: frameReadiness.current.activeFrame,
        timepoint: readyTime + 1,
        relation: readiness.active ? "active" : "inactive-recorded",
        readyPaths: readiness.paths,
      });
      if (readiness.active) {
        completeFrame(
          readyFrame,
          readyTime,
          readiness.paths,
          "active-callback",
        );
      }
    },
    [completeFrame],
  );

  const markPrimaryReady = useCallback(
    () => markReady(imageReadyKey, frameKey, renderTime),
    [frameKey, imageReadyKey, markReady, renderTime],
  );
  const markComparisonReady = useCallback(
    () => markReady(compareReadyKey, frameKey, renderTime),
    [compareReadyKey, frameKey, markReady, renderTime],
  );

  const firstLevel = image?.levels[0];
  const jumpIndex = Math.max(
    0,
    jumpOptions.findIndex((option) => option.frames === jumpFrames),
  );
  const jumpOption = jumpOptions[jumpIndex];
  const nextJumpOption = jumpOptions[(jumpIndex + 1) % jumpOptions.length];
  const cycleTimeJump = () => {
    const nextFrames = nextJumpOption?.frames ?? 1;
    viewerDebug("time:jump-change", {
      fromFrames: jumpFrames,
      toFrames: nextFrames,
      fromLabel: jumpOption?.label,
      toLabel: nextJumpOption?.label,
    });
    setJumpFrames(nextFrames);
  };
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
          <div className="viewer-time-control">
            <div className="viewer-time-heading">
              <label htmlFor="time-slider">
                Time: {requestedTime + 1} / {timeCount}
              </label>
              <button
                type="button"
                className="viewer-time-jump"
                data-testid="time-jump"
                aria-label={
                  "Time jump " +
                  (jumpOption?.label ?? "1 frame") +
                  "; activate for " +
                  (nextJumpOption?.label ?? "1 frame")
                }
                title={
                  "Change time jump to " + (nextJumpOption?.label ?? "1 frame")
                }
                onClick={cycleTimeJump}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M3 5v14l8-7-8-7Zm9 0v14l8-7-8-7Z" />
                </svg>
                <span>{jumpOption?.label ?? "1 frame"}</span>
              </button>
            </div>
            <input
              id="time-slider"
              data-testid="time-slider"
              type="range"
              min={0}
              max={timeCount - 1}
              step={1}
              value={requestedTime}
              onChange={(event) => setRequestedTime(Number(event.target.value))}
              onKeyDown={(event) => {
                let direction: 1 | -1 | null = null;
                if (event.key === "ArrowRight" || event.key === "ArrowUp") {
                  direction = 1;
                } else if (
                  event.key === "ArrowLeft" ||
                  event.key === "ArrowDown"
                ) {
                  direction = -1;
                }
                if (direction == null) return;
                event.preventDefault();
                setRequestedTime((current) =>
                  moveTimeIndex(current, direction, jumpFrames, timeCount),
                );
              }}
              onPointerUp={() => commitTime(requestedTime)}
            />
            <small className="viewer-time-help">
              Fast-forward changes the jump; arrow keys navigate. Dragging still
              selects any frame.
            </small>
            {displayedTime != null && displayedTime !== requestedTime && (
              <small className="viewer-time-state" data-testid="displayed-time-hint">
                Showing time {displayedTime + 1} while time {requestedTime + 1} loads
              </small>
            )}
          </div>
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
              onChange={(event) => {
                const nextZ = Number(event.target.value);
                if (nextZ !== z) {
                  frameReadiness.current.prepare(renderTime + ":" + nextZ);
                  setZ(nextZ);
                }
              }}
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
                      <small>{datasetName + " · " + imagePath}</small>
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
                      <small>{compareDatasetName + " · " + comparePath}</small>
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
          <strong>
            {comparing
              ? (datasetName || "Dataset A") +
                " vs " +
                (compareDatasetName || "Dataset B")
              : datasetName || "Dataset"}
          </strong>
          <span>{imagePath}</span>
          {comparing && (
            <span>
              vs {comparePath}
            </span>
          )}
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
              <h2>Well A · {datasetName}</h2>
              <PlateMetadataPanel
                metadata={plateMetadata}
                selectedWell={wellIdFromPath(imagePath)}
              />
            </div>
            <div className="stack">
              <h2>Well B · {compareDatasetName}</h2>
              <PlateMetadataPanel
                metadata={comparePlateMetadata}
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
