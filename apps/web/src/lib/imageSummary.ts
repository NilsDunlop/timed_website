import type { components } from "./api/dataserver-schema";

type ImageSummary = components["schemas"]["ImageSummary"];

export function imageAxisSize(image: ImageSummary, axisName: string): number {
  const axisIndex = image.axes.findIndex((axis) => axis.name === axisName);
  return axisIndex >= 0 ? (image.levels[0]?.shape[axisIndex] ?? 1) : 1;
}

export function imageChannelLabels(image: ImageSummary): string[] {
  const channelCount = Math.max(
    imageAxisSize(image, "c"),
    image.channels.length,
    1,
  );
  return Array.from(
    { length: channelCount },
    (_, index) => image.channels[index]?.name ?? "Channel " + String(index + 1),
  );
}

export function channelLabelsMatch(
  left: readonly string[],
  right: readonly string[],
): boolean {
  return (
    left.length === right.length &&
    left.every((label, index) => label === right[index])
  );
}
