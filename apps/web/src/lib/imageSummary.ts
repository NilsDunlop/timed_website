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

export function imageHasPhysicalPixelSize(image: ImageSummary): boolean {
  const xAxisIndex = image.axes.findIndex((axis) => axis.name === "x");
  if (xAxisIndex < 0 || !image.axes[xAxisIndex]?.unit?.trim()) return false;

  return (image.levels[0]?.coordinate_transformations ?? []).some(
    (transformation) => {
      if (transformation.type !== "scale") return false;
      const scale = transformation.scale;
      if (!Array.isArray(scale)) return false;
      const xScale = scale[xAxisIndex];
      return (
        typeof xScale === "number" &&
        Number.isFinite(xScale) &&
        xScale > 0
      );
    },
  );
}
