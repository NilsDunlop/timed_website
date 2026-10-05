import {
  getChannelStats,
  loadOmeZarr,
  loadOmeZarrFromStore,
  PictureInPictureViewer,
} from "@hms-dbmi/viv";
import { useEffect, useMemo, useRef, useState } from "react";

import type { components } from "../lib/api/dataserver-schema";
import { apiErrorMessage, dataServerClient } from "../lib/api/client";

type ImageSummary = components["schemas"]["ImageSummary"];
type Summary =
  | ImageSummary
  | components["schemas"]["PlateSummary"];
type Loader = Awaited<ReturnType<typeof loadOmeZarr>>["data"];
type Source = Loader[number];

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
      contrast: [start ?? domain[0], end ?? domain[1]],
    });
  }
  return states;
}

export default function ImageViewer() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 900, height: 600 });
  const [datasetName, setDatasetName] = useState("");
  const [imagePath, setImagePath] = useState("");
  const [image, setImage] = useState<ImageSummary | null>(null);
  const [loader, setLoader] = useState<Loader | null>(null);
  const [channels, setChannels] = useState<ChannelState[]>([]);
  const [time, setTime] = useState(0);
  const [z, setZ] = useState(0);
  const [status, setStatus] = useState("Loading metadata…");
  const [error, setError] = useState("");

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const update = () =>
      setDimensions({
        width: Math.max(320, Math.floor(container.clientWidth)),
        height: Math.max(360, Math.floor(container.clientHeight)),
      });
    update();
    const observer = new ResizeObserver(update);
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const query = new URLSearchParams(window.location.search);
        const datasetId = query.get("dataset");
        const selectedPath = query.get("path");
        if (!datasetId || selectedPath == null) {
          throw new Error("Choose an image field from the Data page first.");
        }

        const [datasetsResult, summaryResult] = await Promise.all([
          dataServerClient.GET("/api/datasets"),
          dataServerClient.GET("/api/datasets/{dataset_id}/summary", {
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

        const zarrRoot = encodedZarrPath(datasetId, selectedPath);
        const loaded =
          selectedImage.zarr_format === 3
            ? await loadOmeZarrFromStore(
                new V3FetchStore(zarrRoot) as Parameters<
                  typeof loadOmeZarrFromStore
                >[0],
              )
            : await loadOmeZarr(zarrRoot, { type: "multiscales" });
        for (const source of loaded.data) {
          source.onTileError = (reason: Error) => {
            if (active) setError("Could not load an image chunk: " + reason.message);
          };
        }
        const initialChannels = await channelStates(loaded.data, selectedImage);
        if (!active) return;
        setDatasetName(record.name);
        setImagePath(selectedPath || selectedImage.path);
        setImage(selectedImage);
        setLoader(loaded.data);
        setChannels(initialChannels);
        setStatus("Rendering image…");
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
    };
  }, []);

  const source = loader?.[0];
  const timeCount = source ? axisSize(source, "t") : 1;
  const zCount = source ? axisSize(source, "z") : 1;
  const selections = useMemo(
    () =>
      source
        ? channels.map((_channel, index) =>
            selectionFor(source, index, time, z),
          )
        : [],
    [channels, source, time, z],
  );

  function updateChannel(index: number, next: Partial<ChannelState>) {
    setChannels((current) =>
      current.map((channel, channelIndex) =>
        channelIndex === index ? { ...channel, ...next } : channel,
      ),
    );
  }

  const firstLevel = image?.levels[0];

  return (
    <div className="viewer-layout">
      <aside className="panel viewer-controls stack">
        <div>
          <p className="eyebrow">Image controls</p>
          <h2>Channels</h2>
        </div>

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

        {timeCount > 1 && (
          <label>
            <span>Time: {time + 1} / {timeCount}</span>
            <input
              data-testid="time-slider"
              type="range"
              min={0}
              max={timeCount - 1}
              value={time}
              onChange={(event) => setTime(Number(event.target.value))}
            />
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
        <a href="/data">← Back to data</a>
      </aside>

      <section className="viewer-stage stack" aria-labelledby="viewer-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Viewer</p>
            <h1 id="viewer-heading">{imagePath || "Loading image"}</h1>
          </div>
          <span
            className={status === "Image ready" ? "status-ready" : "muted"}
            data-testid="viewer-status"
          >
            {status}
          </span>
        </div>

        {error && (
          <div className="notice error" role="alert">
            {error}
          </div>
        )}

        <div className="canvas-frame" ref={containerRef}>
          {loader && channels.length > 0 && (
            <PictureInPictureViewer
              loader={loader}
              contrastLimits={channels.map((channel) => channel.contrast)}
              colors={channels.map((channel) => channel.color)}
              channelsVisible={channels.map((channel) => channel.visible)}
              selections={selections}
              overview={{}}
              overviewOn={loader.length > 1}
              height={dimensions.height}
              width={dimensions.width}
              onViewportLoad={() => setStatus("Image ready")}
            />
          )}
        </div>

        <p className="viewer-status-line">
          <strong>{datasetName || "Dataset"}</strong>
          <span>{imagePath}</span>
          <span>
            shape {firstLevel?.shape.join(" × ") ?? source?.shape.join(" × ") ?? "—"}
          </span>
          <span>dtype {firstLevel?.dtype ?? source?.dtype ?? "—"}</span>
          <span>t {time + 1} / {timeCount}</span>
        </p>
      </section>
    </div>
  );
}
