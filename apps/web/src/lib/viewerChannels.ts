import { getChannelStats } from "@hms-dbmi/viv";

import type { ImageSummary, Loader, Source } from "./viewerSources";

export type ChannelState = {
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

export function axisSize(source: Source, axis: string): number {
  const index = Array.from(source.labels).indexOf(axis);
  return index >= 0 ? source.shape[index] ?? 1 : 1;
}

export function selectionFor(
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

export async function channelStates(
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
    const declaredContrast: [number, number] | null =
      start != null && end != null ? [start, end] : null;

    if (declaredContrast == null) {
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
      visible: metadata?.active ?? true,
      domain,
      contrast: declaredContrast ?? [start ?? domain[0], domain[1]],
    });
  }
  return states;
}
