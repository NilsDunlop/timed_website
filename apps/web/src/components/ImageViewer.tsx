import {
  DETAIL_VIEW_ID,
  getDefaultInitialViewState,
  loadOmeZarr,
  loadOmeZarrFromStore,
} from "@hms-dbmi/viv";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { components as ConnectorComponents } from "../lib/api/connector-schema";
import type { components } from "../lib/api/dataserver-schema";
import {
  apiErrorMessage,
  connectorClient,
  dataServerClient,
  webClientHeaders,
} from "../lib/api/client";
import { fieldPathFor, timedWellPathFromField, wellIdFromPath } from "../lib/fieldPath";
import { qcRequestFromQuery, type QcViewerRequest } from "../lib/qc/qcViewerSession";
import { writeQcViewerLink } from "../lib/qc/qcViewerLink";
import {
  axisSize,
  channelStates,
  selectionFor,
  type ChannelState,
} from "../lib/viewerChannels";
import { parseViewerSearch, viewerQueryHref, viewerReturnHref } from "../lib/viewerLink";
import {
  sitePositionFixNote,
  viewerSourceIdentity,
  type ViewerSourceIdentity,
} from "../lib/viewerSourceIdentity";
import {
  encodedZarrPath,
  imageFromSummary,
  type FieldSummary,
  type ImageSummary,
  type Source,
  type ViewerSourceState,
} from "../lib/viewerSources";
import {
  cacheImageLoader,
  type CacheableImageSource,
  prefetchTimepoints,
  viewerPixelCache,
} from "../lib/viewerTileCache";
import {
  moveTimeIndex,
  prefetchTimeIndex,
  timeIntervalHours,
  timeJumpOptions,
} from "../lib/timeNavigation";
import { FrameReadinessTracker } from "../lib/frameReadiness";
import {
  channelLabelsMatch,
  imageChannelLabels,
} from "../lib/imageSummary";
import { V3FetchStore } from "../lib/v3FetchStore";
import {
  defaultViewerDisplayPreference,
  preferredGrayscaleChannelIndex,
  readViewerDisplayPreference,
  writeViewerDisplayPreference,
  type ViewerDisplayMode,
  type ViewerDisplayPreference,
} from "../lib/viewerDisplay";
import { viewerDebug } from "../lib/viewerDebug";
import {
  countAnnotatedQueueEntries,
  parseQcAnnotation,
  parseQcAnnotationLabels,
  parseQcAnnotations,
  type QcAnnotationLabelDefinition,
  type QcCurrentAnnotation,
} from "../lib/qcAnnotations";
import {
  loadQcQueue,
  qcQueueEntryMatches,
  qcQueueIndexFromSearch,
  type QcReviewQueue,
} from "../lib/qcQueue";
import {
  applyQcDisplayRanges,
  buildQcSiteStepper,
  copyQcContrastChannels,
  qcSiteHref,
  qcSiteOptions,
  selectQcDisplayRanges,
  type QcSiteStepper,
} from "../lib/qcViewer";
import PlateMetadataPanel from "./PlateMetadataPanel";
import QcFieldPanel from "./QcFieldPanel";
import type { QcAnnotationFormValue } from "./QcAnnotationPanel";
import QcReviewBar from "./QcReviewBar";
import VivCanvas, {
  type CameraViewState,
  type Dimensions,
  type VivViewStateChangeEvent,
} from "./viewer/VivCanvas";

type ConnectorProfile = ConnectorComponents["schemas"]["Profile"];
type QcFieldResponse = components["schemas"]["QcFieldResponse"];
type QcRowsResponse = components["schemas"]["QcRowsResponse"];
type QcAnnotationsResponse = components["schemas"]["QcAnnotationsResponse"];
type PendingQcViewerContext = {
  payload: QcFieldResponse;
  rangeRows: QcRowsResponse["rows"];
  annotationRows: QcAnnotationsResponse["annotations"];
  annotationLabelRows: QcAnnotationsResponse["labels"];
  annotationsWritable: boolean;
  collectionPath: string;
  siteStepper: QcSiteStepper | null;
};

type QcViewerContext = {
  payload: QcFieldResponse;
  annotations: readonly QcCurrentAnnotation[];
  annotationLabels: readonly QcAnnotationLabelDefinition[];
  annotationsWritable: boolean;
  collectionPath: string;
  scope: "field" | "well";
  siteStepper: QcSiteStepper | null;
  flaggedSites: readonly number[];
};

type QcQueueContext = {
  queue: QcReviewQueue | null;
  index: number;
  backHref: string;
  warning: string;
};

function qcCollectionPath(
  profiles: readonly ConnectorProfile[],
  profileId: string | null | undefined,
  collectionId: string,
): string | null {
  const profile = profiles.find((candidate) => candidate.id === profileId);
  const collection = profile?.collections?.find(
    (candidate) => candidate.id === collectionId && candidate.qc_available,
  );
  return collection?.path ?? null;
}

async function loadQcViewerContext(
  request: QcViewerRequest,
  datasetId: string,
): Promise<PendingQcViewerContext> {
  const [profilesResult, sessionResult] = await Promise.all([
    connectorClient.GET("/connector/profiles"),
    connectorClient.GET("/connector/session"),
  ]);
  if (!profilesResult.data || !sessionResult.data) {
    throw new Error("The QC collection is unavailable");
  }
  const path = qcCollectionPath(
    profilesResult.data,
    sessionResult.data.profile_id,
    request.collectionId,
  );
  if (!path) throw new Error("The QC collection is unavailable");

  const wellRequest =
    request.scope === "well"
      ? dataServerClient.GET("/api/datasets/{dataset_id}/wells/{well_path}", {
          params: {
            path: { dataset_id: datasetId, well_path: request.well },
          },
        })
      : Promise.resolve(null);
  const [fieldResult, rangesResult, annotationsResult, wellResult] = await Promise.all([
    dataServerClient.GET("/api/qc/field", {
      params: {
        query: {
          path,
          plate: request.plate,
          well: request.well,
          site: request.site,
        },
      },
    }),
    dataServerClient.GET("/api/qc/display-ranges", {
      params: { query: { path } },
    }),
    dataServerClient.GET("/api/qc/annotations", {
      params: { query: { path } },
    }),
    wellRequest,
  ]);
  if (!fieldResult.data || !rangesResult.data || !annotationsResult.data) {
    throw new Error(
      apiErrorMessage(
        fieldResult.error ?? rangesResult.error ?? annotationsResult.error,
        "The QC review data is unavailable",
      ),
    );
  }
  if (
    fieldResult.data.blinded ||
    rangesResult.data.blinded ||
    annotationsResult.data.blinded ||
    fieldResult.data.export_id !== rangesResult.data.export_id ||
    fieldResult.data.export_id !== annotationsResult.data.export_id ||
    fieldResult.data.registry_hash !== rangesResult.data.registry_hash ||
    fieldResult.data.registry_hash !== annotationsResult.data.registry_hash ||
    fieldResult.data.export_id !== request.expectedExport ||
    Number(fieldResult.data.field.plate) !== request.plate ||
    String(fieldResult.data.field.well ?? "").toUpperCase() !== request.well ||
    Number(fieldResult.data.field.site) !== request.site
  ) {
    throw new Error("The QC export changed while the viewer was opening");
  }

  let siteStepper: QcSiteStepper | null = null;
  if (request.scope === "well") {
    if (!wellResult?.data) {
      throw new Error("The QC well fields are unavailable");
    }
    siteStepper = buildQcSiteStepper(
      qcSiteOptions(wellResult.data.fields),
      request.site,
    );
    if (!siteStepper) throw new Error("The selected QC site is unavailable");
  }
  return {
    payload: fieldResult.data,
    rangeRows: rangesResult.data.rows,
    annotationRows: annotationsResult.data.annotations,
    annotationLabelRows: annotationsResult.data.labels,
    annotationsWritable: annotationsResult.data.writable,
    collectionPath: path,
    siteStepper,
  };
}

function SitePositionFixNote({ field }: { field: FieldSummary | null }) {
  const note = sitePositionFixNote(field?.site_position_fix);
  if (!note) return null;
  return (
    <p className="site-position-fix-note" role="note" data-testid="site-position-fix-note">
      {note}
    </p>
  );
}

function ViewerSourceFacts({
  identity,
  compact = false,
}: {
  identity: ViewerSourceIdentity;
  compact?: boolean;
}) {
  return (
    <dl
      className={
        "viewer-source-facts" +
        (compact ? " viewer-source-facts-compact" : "")
      }
      data-testid="viewer-source-facts"
    >
      <div className="viewer-source-compound">
        <dt>{identity.compoundLabel}</dt>
        <dd>{identity.compound}</dd>
      </div>
      {identity.concentration != null && (
        <div>
          <dt>Concentration</dt>
          <dd>{identity.concentration}</dd>
        </div>
      )}
      <div>
        <dt>Well</dt>
        <dd>{identity.well}</dd>
      </div>
      <div>
        <dt>Site</dt>
        <dd>{identity.site}</dd>
      </div>
      <div>
        <dt>Plate</dt>
        <dd>{identity.plate}</dd>
      </div>
    </dl>
  );
}

export default function ImageViewer() {
  const [primaryDimensions, setPrimaryDimensions] = useState<Dimensions>({
    width: 900,
    height: 600,
  });
  const [viewerSources, setViewerSources] = useState<ViewerSourceState[]>([]);
  const [channels, setChannels] = useState<ChannelState[]>([]);
  const [qcContext, setQcContext] = useState<QcViewerContext | null>(null);
  const [qcQueue, setQcQueue] = useState<QcQueueContext | null>(null);
  const [qcError, setQcError] = useState("");
  const [qcQueueBusy, setQcQueueBusy] = useState(false);
  const [qcAnnotationBusy, setQcAnnotationBusy] = useState(false);
  const [qcAnnotationError, setQcAnnotationError] = useState("");
  const [qcAnnotationStatus, setQcAnnotationStatus] = useState("");
  const [displayPreference, setDisplayPreference] =
    useState<ViewerDisplayPreference>(() => {
      if (typeof window === "undefined") return defaultViewerDisplayPreference;
      return readViewerDisplayPreference(window.sessionStorage);
    });
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
        const viewerSearch = parseViewerSearch(window.location.search);
        const { query, queueId: requestedQueueId } = viewerSearch;
        let loadedQcQueue: QcQueueContext | null = requestedQueueId
          ? {
              queue: null,
              index: 0,
              backHref: viewerReturnHref(window.location.search),
              warning: "This review queue is unavailable. Return to QC and start it again.",
            }
          : null;
        const primaryDatasetId = viewerSearch.primary.datasetId;
        const selectedPath = viewerSearch.primary.path;
        const sourceSpecs = viewerSearch.sources;
        let qcLinkError = false;
        let qcRequest: QcViewerRequest | null = null;
        try {
          qcRequest = qcRequestFromQuery(query, selectedPath, sourceSpecs.length);
        } catch {
          qcLinkError = true;
        }
        const qcOutcomePromise = qcRequest
          ? loadQcViewerContext(qcRequest, primaryDatasetId)
              .then((value) => ({ value, failed: false }))
              .catch(() => ({ value: null, failed: true }))
          : Promise.resolve({ value: null, failed: qcLinkError });
        viewerDebug("viewer:load-start", { sources: sourceSpecs });

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

        const detailRequests = new Map<
          string,
          ReturnType<typeof loadDatasetDetails>
        >();
        const detailsFor = (sourceDatasetId: string) => {
          let request = detailRequests.get(sourceDatasetId);
          if (!request) {
            request = loadDatasetDetails(sourceDatasetId);
            detailRequests.set(sourceDatasetId, request);
          }
          return request;
        };
        async function selectedFieldFor(source: {
          datasetId: string;
          path: string;
        }): Promise<FieldSummary | null> {
          const wellPath = timedWellPathFromField(source.path);
          if (!wellPath) return null;
          const result = await dataServerClient.GET(
            "/api/datasets/{dataset_id}/wells/{well_path}",
            {
              params: {
                path: {
                  dataset_id: source.datasetId,
                  well_path: wellPath,
                },
              },
            },
          );
          if (!result.data) {
            throw new Error(
              apiErrorMessage(result.error, "Could not load field information"),
            );
          }
          return result.data.fields.find((field) => field.path === source.path) ?? null;
        }
        const sourceDetails = await Promise.all(
          sourceSpecs.map(async (source) => {
            const [details, field] = await Promise.all([
              detailsFor(source.datasetId),
              selectedFieldFor(source),
            ]);
            return { ...details, field };
          }),
        );
        const configuredSources = sourceSpecs.map((source, index) => {
          const details = sourceDetails[index];
          const selectedImage = details
            ? imageFromSummary(details.summary)
            : null;
          if (!selectedImage || !details) {
            throw new Error(
              "Linked dataset " + source.datasetId + " has no image metadata.",
            );
          }
          return {
            ...source,
            image: selectedImage,
            field: details.field,
            metadata: details.metadata,
          };
        });
        const expectedChannelLabels = configuredSources[0]
          ? imageChannelLabels(configuredSources[0].image)
          : undefined;
        if (
          expectedChannelLabels &&
          configuredSources
            .slice(1)
            .some((source) =>
              !channelLabelsMatch(
                expectedChannelLabels,
                imageChannelLabels(source.image),
              ),
            )
        ) {
          throw new Error(
            "The selected fields have different channels and cannot be compared.",
          );
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

        viewerDebug("viewer:image-metadata-ready", {
          sources: configuredSources.map((source) => ({
            datasetId: source.datasetId,
            path: source.path,
            zarrFormat: source.image.zarr_format,
          })),
          axes: configuredSources[0].image.axes,
          levels: configuredSources[0].image.levels.length,
        });
        const loadedImages = await Promise.all(configuredSources.map(loadSource));
        for (const [index, loadedImage] of loadedImages.entries()) {
          const sourceSpec = configuredSources[index];
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
        const primaryLoaded = loadedImages[0];
        const primarySpec = configuredSources[0];
        if (!primaryLoaded || !primarySpec) {
          throw new Error("The viewer has no image source.");
        }
        let initialChannels = await channelStates(
          primaryLoaded.data,
          primarySpec.image,
        );
        const commonChannelCount = loadedImages.reduce(
          (count, loadedImage) => {
            const loadedSource = loadedImage.data.at(-1);
            return loadedSource
              ? Math.min(count, axisSize(loadedSource, "c"))
              : 0;
          },
          initialChannels.length,
        );
        if (commonChannelCount === 0) {
          throw new Error("The selected images have no comparable channels.");
        }
        initialChannels = initialChannels.slice(0, commonChannelCount);
        let displayedChannels = copyQcContrastChannels(initialChannels);
        let loadedQcContext: QcViewerContext | null = null;
        const qcOutcome = await qcOutcomePromise;
        let qcFailed = qcOutcome.failed;
        if (qcOutcome.value) {
          try {
            const ranges = selectQcDisplayRanges(
              qcOutcome.value.rangeRows,
              qcRequest?.plate ?? 0,
              initialChannels.map((channel) => channel.name),
            );
            const fieldBarcode = String(
              qcOutcome.value.payload.field.plate_barcode ?? "",
            ).trim();
            const displayedPlate = primarySpec.metadata?.plate?.plate_id?.trim() ?? "";
            if (
              !fieldBarcode ||
              !displayedPlate ||
              displayedPlate !== fieldBarcode ||
              ranges.some(
                (range) => !range.plateBarcode || range.plateBarcode !== displayedPlate,
              )
            ) {
              throw new Error("The QC display ranges do not match this plate");
            }
            displayedChannels = applyQcDisplayRanges(initialChannels, ranges);
            const parsedAnnotations = parseQcAnnotations(
              qcOutcome.value.annotationRows,
              initialChannels.map((channel) => channel.name),
            );
            if (parsedAnnotations.invalidRecords > 0) {
              throw new Error("The QC annotation response is invalid");
            }
            const parsedLabels = parseQcAnnotationLabels(
              qcOutcome.value.annotationLabelRows,
            );
            if (parsedLabels.invalidRecords > 0) {
              throw new Error("The QC annotation label registry is invalid");
            }
            loadedQcContext = {
              payload: qcOutcome.value.payload,
              annotations: parsedAnnotations.annotations,
              annotationLabels: parsedLabels.labels,
              annotationsWritable: qcOutcome.value.annotationsWritable,
              collectionPath: qcOutcome.value.collectionPath,
              scope: qcRequest?.scope ?? "field",
              siteStepper: qcOutcome.value.siteStepper,
              flaggedSites: qcRequest?.flaggedSites ?? [],
            };
            if (requestedQueueId && qcRequest) {
              const queueResult = loadQcQueue(window.sessionStorage, {
                id: requestedQueueId,
                exportId: qcOutcome.value.payload.export_id,
                registryHash: qcOutcome.value.payload.registry_hash,
                collectionId: qcRequest.collectionId,
                channels: initialChannels.map((channel) => channel.name),
              });
              const queueIndex = queueResult.ok
                ? qcQueueIndexFromSearch(query, queueResult.queue)
                : null;
              const entry =
                queueResult.ok && queueIndex !== null
                  ? queueResult.queue.entries[queueIndex]
                  : null;
              if (
                queueResult.ok &&
                queueIndex !== null &&
                entry &&
                qcQueueEntryMatches(entry, {
                  scope: qcRequest.scope,
                  plate: qcRequest.plate,
                  barcode: fieldBarcode,
                  well: qcRequest.well,
                  site: qcRequest.site,
                })
              ) {
                loadedQcQueue = {
                  queue: queueResult.queue,
                  index: queueIndex,
                  backHref: queueResult.queue.returnHref,
                  warning: "",
                };
              }
            }
          } catch {
            qcFailed = true;
          }
        }
        if (!active) return;
        const loadedSources = configuredSources.map((source, index) => ({
          ...source,
          loader: loadedImages[index].data,
          readyKey: source.datasetId + "\u0000" + source.path,
        }));
        viewerDebug("viewer:loader-ready", {
          sources: loadedSources.map((source) => ({
            datasetId: source.datasetId,
            path: source.path,
          })),
          channels: initialChannels.length,
          resolutions: primaryLoaded.data.length,
          sourceCount: loadedSources.length,
        });
        frameReadiness.current.clear();
        settledFrame.current = "";
        setRequestedTime(0);
        setRenderTime(0);
        setDisplayedTime(null);
        setJumpFrames(1);
        setViewerSources(loadedSources);
        setQcContext(loadedQcContext);
        setQcQueue(loadedQcQueue);
        setQcQueueBusy(false);
        setQcAnnotationBusy(false);
        setQcAnnotationError("");
        setQcAnnotationStatus("");
        setQcError(
          qcFailed
            ? "QC review data could not be loaded. Return to QC and reload the export."
            : "",
        );
        setChannels(displayedChannels);
        setViewState(null);
        setStatus(
          loadedSources.length > 1 ? "Rendering images…" : "Rendering image…",
        );
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

  const primaryViewerSource = viewerSources[0] ?? null;
  const loader = primaryViewerSource?.loader ?? null;
  const source = loader?.[0];
  const image = primaryViewerSource?.image ?? null;
  const imagePath = primaryViewerSource?.path ?? "";
  const plateMetadata = primaryViewerSource?.metadata ?? null;
  const primaryIdentity = primaryViewerSource
    ? viewerSourceIdentity(primaryViewerSource)
    : null;
  const sourceCount = viewerSources.length;
  const comparing = sourceCount > 1;
  const imageSources = useMemo(
    () =>
      viewerSources
        .map((viewerSource) => viewerSource.loader[0])
        .filter((viewerSource): viewerSource is Source => Boolean(viewerSource)),
    [viewerSources],
  );
  const timeCount =
    imageSources.length > 0
      ? Math.min(...imageSources.map((imageSource) => axisSize(imageSource, "t")))
      : 1;
  const zCount =
    imageSources.length > 0
      ? Math.min(...imageSources.map((imageSource) => axisSize(imageSource, "z")))
      : 1;
  const channelCount = channels.length;
  const grayscaleAvailable =
    viewerSources.length > 0 &&
    viewerSources.every(
      (viewerSource) => viewerSource.image.grayscale_available === true,
    );
  const displayMode = grayscaleAvailable
    ? displayPreference.mode
    : "fluorescence";
  const grayscaleChannelIndex = preferredGrayscaleChannelIndex(
    channels.map((channel) => channel.name),
    displayPreference.grayscaleChannelName,
  );
  const renderedChannels = useMemo(
    () =>
      displayMode === "fluorescence" || grayscaleChannelIndex == null
        ? channels
        : channels.map((channel, index) => ({
            ...channel,
            color: [255, 255, 255] as [number, number, number],
            visible: index === grayscaleChannelIndex,
          })),
    [channels, displayMode, grayscaleChannelIndex],
  );
  const readyKeys = useMemo(
    () => viewerSources.map((viewerSource) => viewerSource.readyKey),
    [viewerSources],
  );
  const intervalHours = timeIntervalHours(
    image,
    plateMetadata?.plate?.acquisition?.interval_minutes,
  );
  const jumpOptions = useMemo(
    () => timeJumpOptions(timeCount, intervalHours),
    [intervalHours, timeCount],
  );
  const selectionsBySource = useMemo(
    () =>
      imageSources.map((imageSource) =>
        Array.from({ length: channelCount }, (_unused, index) =>
          selectionFor(imageSource, index, renderTime, z),
        ),
      ),
    [channelCount, imageSources, renderTime, z],
  );
  const selections = selectionsBySource[0] ?? [];
  const frameKey = renderTime + ":" + z;

  const completeFrame = useCallback(
    (
      readyFrame: string,
      readyTime: number,
      readyPaths: string[],
      source: "active-callback" | "recorded-before-activation",
    ) => {
      const expected = sourceCount;
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
      setStatus(sourceCount > 1 ? "Images ready" : "Image ready");
      return true;
    },
    [sourceCount],
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

  const requestTimeMove = useCallback(
    (direction: 1 | -1) => {
      setRequestedTime((current) =>
        moveTimeIndex(current, direction, jumpFrames, timeCount),
      );
    },
    [jumpFrames, timeCount],
  );

  useEffect(() => {
    if (!jumpOptions.some((option) => option.frames === jumpFrames)) {
      setJumpFrames(jumpOptions[0]?.frames ?? 1);
    }
  }, [jumpFrames, jumpOptions]);

  useEffect(() => {
    if (controlsOpen || !loader || timeCount <= 1) return;

    const handleHiddenControlsKeyDown = (event: KeyboardEvent) => {
      if (
        event.altKey ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        event.target instanceof HTMLInputElement ||
        event.target instanceof HTMLTextAreaElement ||
        event.target instanceof HTMLSelectElement ||
        (event.target instanceof HTMLElement && event.target.isContentEditable)
      ) {
        return;
      }

      const direction =
        event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : null;
      if (direction == null) return;

      event.preventDefault();
      viewerDebug("time:keyboard-jump", {
        direction,
        jumpFrames,
        controlsOpen: false,
      });
      requestTimeMove(direction);
    };

    window.addEventListener("keydown", handleHiddenControlsKeyDown, true);
    return () =>
      window.removeEventListener("keydown", handleHiddenControlsKeyDown, true);
  }, [
    controlsOpen,
    jumpFrames,
    loader,
    requestTimeMove,
    timeCount,
  ]);

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
      sourceCount,
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
        sourceCount > 1
          ? "Loading time " +
              String(renderTime + 1) +
              " in " +
              String(sourceCount) +
              " images…"
          : "Loading time " + String(renderTime + 1) + "…",
      );
    }
  }, [
    completeFrame,
    frameKey,
    loader,
    renderTime,
    sourceCount,
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
        expectedPaths: readyKeys,
        cachedEntries: viewerPixelCache.size,
        cachedBytes: viewerPixelCache.byteLength,
        waitedMs: 10_000,
      });
    }, 10_000);
    return () => window.clearTimeout(timer);
  }, [
    displayedTime,
    frameKey,
    loader,
    readyKeys,
    renderTime,
    requestedTime,
  ]);

  useEffect(() => {
    if (
      viewerSources.length === 0 ||
      viewerSources.some((viewerSource) => viewerSource.loader.length !== 1) ||
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
        sourceCount,
        strategy: "single-resolution-raster",
        rasters: channelCount * sourceCount,
      });
      const work = prefetchTimepoints(
        viewerSources.map((viewerSource) => ({
          loader: viewerSource.loader as unknown as CacheableImageSource[],
          selections: Array.from(
            { length: channelCount },
            (_unused, index) =>
              selectionFor(
                viewerSource.loader[0],
                index,
                targetTime,
                z,
              ),
          ),
        })),
        controller.signal,
        3,
      );
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
    displayedTime,
    jumpFrames,
    renderTime,
    requestedTime,
    sourceCount,
    timeCount,
    viewerSources,
    z,
  ]);

  function updateChannel(index: number, next: Partial<ChannelState>) {
    setChannels((current) =>
      current.map((channel, channelIndex) =>
        channelIndex === index ? { ...channel, ...next } : channel,
      ),
    );
  }

  async function refreshQcAnnotations(context: QcViewerContext): Promise<void> {
    const result = await dataServerClient.GET("/api/qc/annotations", {
      params: { query: { path: context.collectionPath } },
    });
    if (
      !result.data ||
      result.data.blinded ||
      result.data.export_id !== context.payload.export_id ||
      result.data.registry_hash !== context.payload.registry_hash
    ) {
      throw new Error(
        apiErrorMessage(result.error, "The current annotations could not be loaded"),
      );
    }
    const parsed = parseQcAnnotations(
      result.data.annotations,
      channels.map((channel) => channel.name),
    );
    if (parsed.invalidRecords > 0) {
      throw new Error("The QC annotation response is invalid");
    }
    const parsedLabels = parseQcAnnotationLabels(result.data.labels);
    if (parsedLabels.invalidRecords > 0) {
      throw new Error("The QC annotation label registry is invalid");
    }
    setQcContext((current) =>
      current && current.payload.export_id === context.payload.export_id
        ? {
            ...current,
            annotations: parsed.annotations,
            annotationLabels: parsedLabels.labels,
            annotationsWritable: result.data.writable,
          }
        : current,
    );
  }

  async function saveQcAnnotation(value: QcAnnotationFormValue): Promise<void> {
    if (!qcContext) return;
    const plate = Number(qcContext.payload.field.plate);
    const well = String(qcContext.payload.field.well ?? "").toUpperCase();
    const site = Number(qcContext.payload.field.site);
    setQcAnnotationBusy(true);
    setQcAnnotationError("");
    setQcAnnotationStatus("");
    try {
      const result = await dataServerClient.POST("/api/qc/annotations", {
        headers: webClientHeaders,
        params: { query: { path: qcContext.collectionPath } },
        body: {
          record: "annotation",
          export_id: qcContext.payload.export_id,
          scope: value.scope,
          plate,
          well,
          site: value.scope === "field" ? site : null,
          channel: value.channel,
          label: value.label,
          confidence: value.confidence,
          note: value.note,
        },
      });
      if (!result.data) {
        throw new Error(apiErrorMessage(result.error, "The annotation could not be saved"));
      }
      const saved =
        result.data.record.record === "annotation"
          ? parseQcAnnotation(
              result.data.record,
              channels.map((channel) => channel.name),
            )
          : null;
      if (saved) {
        setQcContext((current) =>
          current && current.payload.export_id === qcContext.payload.export_id
            ? {
                ...current,
                annotations: [
                  ...current.annotations.filter(
                    (annotation) =>
                      !(
                        annotation.reviewer === saved.reviewer &&
                        annotation.scope === saved.scope &&
                        annotation.plate === saved.plate &&
                        annotation.barcode === saved.barcode &&
                        annotation.well === saved.well &&
                        annotation.site === saved.site &&
                        annotation.channel === saved.channel
                      ),
                  ),
                  saved,
                ],
              }
            : current,
        );
      }
      try {
        await refreshQcAnnotations(qcContext);
        setQcAnnotationStatus("Annotation saved.");
      } catch {
        setQcAnnotationStatus(
          "Annotation saved, but the latest annotation list could not be refreshed.",
        );
      }
    } catch (cause) {
      setQcAnnotationError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setQcAnnotationBusy(false);
    }
  }

  async function retractQcAnnotation(id: string): Promise<void> {
    if (!qcContext) return;
    setQcAnnotationBusy(true);
    setQcAnnotationError("");
    setQcAnnotationStatus("");
    try {
      const result = await dataServerClient.POST("/api/qc/annotations", {
        headers: webClientHeaders,
        params: { query: { path: qcContext.collectionPath } },
        body: {
          record: "retraction",
          export_id: qcContext.payload.export_id,
          id,
        },
      });
      if (!result.data) {
        throw new Error(
          apiErrorMessage(result.error, "The annotation could not be retracted"),
        );
      }
      setQcContext((current) =>
        current && current.payload.export_id === qcContext.payload.export_id
          ? {
              ...current,
              annotations: current.annotations.filter(
                (annotation) => annotation.id !== id,
              ),
            }
          : current,
      );
      try {
        await refreshQcAnnotations(qcContext);
        setQcAnnotationStatus("Annotation retracted.");
      } catch {
        setQcAnnotationStatus(
          "Annotation retracted, but the latest annotation list could not be refreshed.",
        );
      }
    } catch (cause) {
      setQcAnnotationError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setQcAnnotationBusy(false);
    }
  }

  async function moveQcQueue(nextIndex: number): Promise<void> {
    const queue = qcQueue?.queue;
    const context = qcContext;
    const entry = queue?.entries[nextIndex];
    if (!queue || !context || !entry || qcQueueBusy) return;
    setQcQueueBusy(true);
    setQcQueue((current) => (current ? { ...current, warning: "" } : current));
    try {
      const root = context.collectionPath.replace(/[\\/]+$/, "");
      const linked = await dataServerClient.POST("/api/datasets", {
        headers: webClientHeaders,
        body: { path: `${root}/${entry.plateGroup}/${entry.plate}` },
      });
      if (!linked.data) {
        throw new Error(apiErrorMessage(linked.error, "The next review image could not be opened"));
      }
      // Edits the current query, so parameters such as debug survive the move.
      const query = writeQcViewerLink(
        new URLSearchParams(window.location.search),
        {
          datasetId: linked.data.id,
          path: fieldPathFor(entry.well, entry.site),
          collectionId: queue.collectionId,
          scope: entry.scope,
          plate: entry.plate,
          well: entry.well,
          site: entry.site,
          exportId: queue.exportId,
          returnHref: queue.returnHref,
          flaggedSites: entry.flaggedSites,
        },
        { queue, index: nextIndex },
      );
      if (!query) {
        throw new Error("The next review image could not be opened");
      }
      window.location.assign(viewerQueryHref(query));
    } catch (cause) {
      setQcQueueBusy(false);
      setQcQueue((current) =>
        current
          ? {
              ...current,
              warning: cause instanceof Error ? cause.message : String(cause),
            }
          : current,
      );
    }
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

  const markSourceReady = useCallback(
    (readyKey: string) => markReady(readyKey, frameKey, renderTime),
    [frameKey, markReady, renderTime],
  );
  const sourceReadyCallbacks = useMemo(
    () =>
      viewerSources.map((viewerSource) => () =>
        markSourceReady(viewerSource.readyKey),
      ),
    [markSourceReady, viewerSources],
  );

  const firstLevel = image?.levels[0];
  const jumpIndex = Math.max(
    0,
    jumpOptions.findIndex((option) => option.frames === jumpFrames),
  );
  const jumpOption = jumpOptions[jumpIndex];
  const nextJumpOption = jumpOptions[(jumpIndex + 1) % jumpOptions.length];
  const channelControlIndexes =
    displayMode === "grayscale" && grayscaleChannelIndex != null
      ? [grayscaleChannelIndex]
      : channels.map((_channel, index) => index);
  const backHref = useMemo(() => {
    return viewerReturnHref(window.location.search);
  }, []);
  const qcMode = useMemo(
    () => new URLSearchParams(window.location.search).get("qc") === "1",
    [],
  );
  const currentQcAnnotations = useMemo(() => {
    if (!qcContext) return [];
    const plate = Number(qcContext.payload.field.plate);
    const barcode = String(qcContext.payload.field.plate_barcode ?? "");
    const well = String(qcContext.payload.field.well ?? "").toUpperCase();
    const site = Number(qcContext.payload.field.site);
    return qcContext.annotations.filter(
      (annotation) =>
        annotation.plate === plate &&
        annotation.barcode === barcode &&
        annotation.well === well &&
        (annotation.scope === "well" ||
          (annotation.scope === "field" && annotation.site === site)),
    );
  }, [qcContext]);
  const activeQueueEntry =
    qcQueue?.queue?.entries[qcQueue.index] ?? null;
  const annotatedQueueEntries = useMemo(
    () =>
      qcQueue?.queue && qcContext
        ? countAnnotatedQueueEntries(
            qcQueue.queue.entries,
            qcContext.annotations,
          )
        : 0,
    [qcContext, qcQueue],
  );

  useEffect(() => {
    const queue = qcQueue?.queue;
    if (!qcQueue || !queue) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.defaultPrevented ||
        event.metaKey ||
        ((event.ctrlKey || event.altKey) &&
          !(event.ctrlKey && event.altKey) &&
          !event.getModifierState("AltGraph")) ||
        (event.key !== "ArrowLeft" && event.key !== "ArrowRight")
      ) {
        return;
      }
      const target = event.target;
      if (
        target instanceof Element &&
        target.closest("input, textarea, select, button, [contenteditable='true']")
      ) {
        return;
      }
      const nextIndex = qcQueue.index + (event.key === "ArrowLeft" ? -1 : 1);
      if (nextIndex < 0 || nextIndex >= queue.entries.length) return;
      event.preventDefault();
      void moveQcQueue(nextIndex);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [qcQueue, qcQueueBusy]);

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
  const persistDisplayPreference = (next: ViewerDisplayPreference) => {
    setDisplayPreference(next);
    writeViewerDisplayPreference(window.sessionStorage, next);
  };
  const chooseDisplayMode = (mode: ViewerDisplayMode) => {
    const selectedName =
      grayscaleChannelIndex == null
        ? displayPreference.grayscaleChannelName
        : (channels[grayscaleChannelIndex]?.name ?? null);
    persistDisplayPreference({
      mode,
      grayscaleChannelName: selectedName,
    });
  };
  const chooseGrayscaleChannel = (index: number) => {
    persistDisplayPreference({
      mode: "grayscale",
      grayscaleChannelName: channels[index]?.name ?? null,
    });
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
            <h2>{timeCount <= 1 ? "Channels" : "Time and channels"}</h2>
          </div>

          {grayscaleAvailable && (
            <fieldset className="viewer-mode-control">
              <legend id="viewer-display-legend">Display</legend>
              <div
                className="viewer-mode-options"
                role="radiogroup"
                aria-labelledby="viewer-display-legend"
              >
                <label>
                  <input
                    type="radio"
                    name="viewer-display-mode"
                    value="fluorescence"
                    checked={displayMode === "fluorescence"}
                    onChange={() => chooseDisplayMode("fluorescence")}
                  />
                  <span>Color</span>
                </label>
                <label>
                  <input
                    type="radio"
                    name="viewer-display-mode"
                    value="grayscale"
                    checked={displayMode === "grayscale"}
                    onChange={() => chooseDisplayMode("grayscale")}
                  />
                  <span>Grayscale</span>
                </label>
              </div>
            </fieldset>
          )}

          {displayMode === "grayscale" && channels.length > 0 && (
            <fieldset className="grayscale-channel-control">
              <legend>Grayscale channel</legend>
              <div className="grayscale-channel-options">
                {channels.map((channel, index) => (
                  <label key={channel.name + String(index)}>
                    <input
                      type="radio"
                      name="grayscale-channel"
                      checked={grayscaleChannelIndex === index}
                      onChange={() => chooseGrayscaleChannel(index)}
                    />
                    <span>{channel.name}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          )}

        {timeCount > 1 && (
          <div className="viewer-time-control">
            <div className="viewer-time-heading">
              <label htmlFor="time-slider">
                Time: {requestedTime + 1} / {timeCount}
              </label>
              <div className="viewer-time-actions">
                <span className="selection-help">
                  <button
                    type="button"
                    className="selection-info"
                    aria-label="Time navigation help"
                    aria-describedby="time-navigation-help"
                  >
                    <svg viewBox="0 0 20 20" aria-hidden="true">
                      <circle cx="10" cy="10" r="8" />
                      <path d="M10 9v5M10 6.2v.1" />
                    </svg>
                  </button>
                  <span
                    id="time-navigation-help"
                    className="selection-tooltip"
                    role="tooltip"
                  >
                    Fast-forward changes the jump. Arrow keys navigate,
                    including when controls are hidden. Dragging selects
                    any frame.
                  </span>
                </span>
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
                requestTimeMove(direction);
              }}
              onPointerUp={() => commitTime(requestedTime)}
            />
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

        {timeCount > 1 && (
          <h3 className="viewer-channel-heading">
            {displayMode === "grayscale" ? "Contrast" : "Channels"}
          </h3>
        )}

        {channelControlIndexes.map((index) => {
          const channel = channels[index];
          if (!channel) return null;
          const step = Math.max(
            1,
            (channel.domain[1] - channel.domain[0]) / 1000,
          );
          return (
            <fieldset key={channel.name + String(index)} className="channel-control">
              <legend>
                {displayMode === "fluorescence" ? (
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
                ) : (
                  channel.name
                )}
              </legend>
              <label>
                <span>Contrast minimum: {Math.round(channel.contrast[0])}</span>
                <input
                  aria-label={channel.name + " contrast minimum"}
                  type="range"
                  disabled={Boolean(qcContext)}
                  min={channel.domain[0]}
                  max={channel.domain[1]}
                  step={qcMode ? "any" : step}
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
                  disabled={Boolean(qcContext)}
                  min={channel.domain[0]}
                  max={channel.domain[1]}
                  step={qcMode ? "any" : step}
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
        {!qcQueue && (
          <a href={backHref}>{qcMode ? "← Back to QC" : "← Back to data"}</a>
        )}
        </aside>
      )}

      <section className="viewer-stage stack" aria-labelledby="viewer-heading">
        <div className="section-heading">
          <div className="viewer-title-block">
            <p className="eyebrow">
              {comparing ? "Synchronized viewer" : "Viewer"}
            </p>
            <div className="viewer-title-content">
              {comparing ? (
                <h1 id="viewer-heading">Compare trajectories</h1>
              ) : primaryIdentity ? (
                <>
                  <h1 id="viewer-heading" className="visually-hidden">
                    Image viewer for {primaryIdentity.compound}
                  </h1>
                  <ViewerSourceFacts identity={primaryIdentity} />
                </>
              ) : (
                <h1 id="viewer-heading">Loading image</h1>
              )}
            </div>
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

        {qcQueue && (
          <QcReviewBar
            index={qcQueue.index}
            total={qcQueue.queue?.entries.length ?? 0}
            annotated={annotatedQueueEntries}
            reason={activeQueueEntry?.reason}
            busy={qcQueueBusy}
            warning={qcQueue.warning}
            onPrevious={() => void moveQcQueue(qcQueue.index - 1)}
            onNext={() => void moveQcQueue(qcQueue.index + 1)}
          />
        )}

        {error && (
          <div className="notice error" role="alert">
            {error}
          </div>
        )}
        {qcError && (
          <div className="notice error" role="alert" data-testid="qc-viewer-error">
            {qcError}
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

          {loader &&
            image &&
            channels.length > 0 &&
            (comparing ? (
              <div
                className="comparison-view-grid"
                data-testid="comparison-view"
                data-count={sourceCount}
              >
                {viewerSources.map((viewerSource, index) => {
                  const label = "ABCD"[index];
                  const identity = viewerSourceIdentity(viewerSource);
                  return (
                    <article
                      className="comparison-view"
                      key={viewerSource.readyKey}
                    >
                      <header>
                        <span className="comparison-index">{label}</span>
                        <div className="comparison-source-identity">
                          <ViewerSourceFacts identity={identity} compact />
                        </div>
                      </header>
                       <SitePositionFixNote field={viewerSource.field} />
                      <VivCanvas
                        loader={viewerSource.loader}
                        image={viewerSource.image}
                        channels={renderedChannels}
                        selections={selectionsBySource[index] ?? []}
                        viewState={viewState}
                        onViewStateChange={handleViewStateChange}
                        onReady={sourceReadyCallbacks[index]}
                        onResize={
                          index === 0 ? setPrimaryDimensions : undefined
                        }
                      />
                    </article>
                  );
                })}
              </div>
            ) : (
              <>
                <SitePositionFixNote field={primaryViewerSource?.field ?? null} />
                <VivCanvas
                  loader={loader}
                  image={image}
                  channels={renderedChannels}
                  selections={selections}
                  viewState={viewState}
                  onViewStateChange={handleViewStateChange}
                  onReady={sourceReadyCallbacks[0]}
                  onResize={setPrimaryDimensions}
                />
              </>
            ))}
        </div>

        <p className="viewer-status-line">
          <span>
            shape {firstLevel?.shape.join(" × ") ?? source?.shape.join(" × ") ?? "—"}
          </span>
          <span>dtype {firstLevel?.dtype ?? source?.dtype ?? "—"}</span>
          {timeCount > 1 && (
            <span data-testid="displayed-time">
            t {displayedTime == null ? "—" : displayedTime + 1} / {timeCount}
            </span>
          )}
        </p>

        {comparing ? (
          <div
            className="comparison-metadata-grid"
            data-count={sourceCount}
          >
            {viewerSources.map((viewerSource, index) => (
              <div className="stack" key={viewerSource.readyKey}>
                <h2>Well {"ABCD"[index]} details</h2>
                <PlateMetadataPanel
                  metadata={viewerSource.metadata}
                  selectedWell={wellIdFromPath(viewerSource.path)}
                  detailsOnly
                />
              </div>
            ))}
          </div>
        ) : (
          <PlateMetadataPanel
            metadata={plateMetadata}
            selectedWell={wellIdFromPath(imagePath)}
            detailsOnly
          />
        )}
        {qcContext && !comparing && (
          <QcFieldPanel
            payload={qcContext.payload}
            siteStepper={qcContext.siteStepper}
            flaggedSites={qcContext.flaggedSites}
            onSiteChange={(option) =>
              window.location.assign(qcSiteHref(window.location.search, option))
            }
            annotationScope={qcContext.scope}
            annotationChannels={channels.map((channel) => channel.name)}
            annotationLabels={qcContext.annotationLabels}
            annotations={currentQcAnnotations}
            annotationsWritable={qcContext.annotationsWritable}
            annotationSubmitting={qcAnnotationBusy}
            annotationDefaultChannel={activeQueueEntry?.channel ?? "all"}
            annotationError={qcAnnotationError}
            annotationStatus={qcAnnotationStatus}
            onSaveAnnotation={(value) => void saveQcAnnotation(value)}
            onRetractAnnotation={(id) => void retractQcAnnotation(id)}
          />
        )}
      </section>
    </div>
  );
}
