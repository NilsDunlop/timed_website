const PREFERRED_JUMP_HOURS = [1, 2, 4, 6, 8, 12, 24] as const;

type TimeAxis = {
  name: string;
  unit?: string | null;
};

type ResolutionLevel = {
  coordinate_transformations?: Record<string, unknown>[];
};

export type TimeMetadata = {
  axes: TimeAxis[];
  levels: ResolutionLevel[];
};

export type TimeJumpOption = {
  frames: number;
  label: string;
};

function unitInHours(unit: string | null | undefined): number | null {
  switch (unit?.trim().toLowerCase()) {
    case "h":
    case "hr":
    case "hrs":
    case "hour":
    case "hours":
      return 1;
    case "m":
    case "min":
    case "mins":
    case "minute":
    case "minutes":
      return 1 / 60;
    case "s":
    case "sec":
    case "secs":
    case "second":
    case "seconds":
      return 1 / 3600;
    default:
      return null;
  }
}

function scaleAt(level: ResolutionLevel | undefined, axisIndex: number): number | null {
  for (const transformation of level?.coordinate_transformations ?? []) {
    if (transformation.type !== "scale" || !Array.isArray(transformation.scale)) {
      continue;
    }
    const value = transformation.scale[axisIndex];
    if (typeof value === "number" && Number.isFinite(value) && value > 0) {
      return value;
    }
  }
  return null;
}

/** Return the elapsed hours represented by one time-axis index. */
export function timeIntervalHours(
  image: TimeMetadata | null,
  acquisitionIntervalMinutes?: number | null,
): number | null {
  if (image) {
    const axisIndex = image.axes.findIndex((axis) => axis.name === "t");
    if (axisIndex >= 0) {
      const unitScale = unitInHours(image.axes[axisIndex]?.unit);
      const coordinateScale = scaleAt(image.levels[0], axisIndex);
      if (unitScale != null && coordinateScale != null) {
        return unitScale * coordinateScale;
      }
    }
  }

  if (
    typeof acquisitionIntervalMinutes === "number" &&
    Number.isFinite(acquisitionIntervalMinutes) &&
    acquisitionIntervalMinutes > 0
  ) {
    return acquisitionIntervalMinutes / 60;
  }

  if (image) {
    const timeAxis = image.axes.find((axis) => axis.name === "t");
    return unitInHours(timeAxis?.unit);
  }
  return null;
}

function durationLabel(hours: number): string {
  const roundedHours = Math.round(hours * 100) / 100;
  if (roundedHours >= 1) return `${roundedHours} h`;

  const minutes = Math.round(hours * 60 * 100) / 100;
  if (minutes >= 1) return `${minutes} min`;

  return `${Math.round(hours * 3600 * 100) / 100} s`;
}

/** Build jumps that correspond to real frames, never fractional indices. */
export function timeJumpOptions(
  timeCount: number,
  intervalHours: number | null,
): TimeJumpOption[] {
  const maximumJump = Math.max(1, Math.floor(timeCount) - 1);
  if (intervalHours == null || !Number.isFinite(intervalHours) || intervalHours <= 0) {
    return PREFERRED_JUMP_HOURS.filter((frames) => frames <= maximumJump).map(
      (frames) => ({
        frames,
        label: `${frames} ${frames === 1 ? "frame" : "frames"}`,
      }),
    );
  }

  const frames = new Set<number>([1]);
  for (const hours of PREFERRED_JUMP_HOURS) {
    const candidate = hours / intervalHours;
    const rounded = Math.round(candidate);
    if (
      rounded >= 1 &&
      rounded <= maximumJump &&
      Math.abs(candidate - rounded) < 1e-6
    ) {
      frames.add(rounded);
    }
  }

  return Array.from(frames)
    .filter((frameCount) => frameCount <= maximumJump)
    .sort((left, right) => left - right)
    .map((frameCount) => ({
      frames: frameCount,
      label: durationLabel(frameCount * intervalHours),
    }));
}

export function moveTimeIndex(
  current: number,
  direction: 1 | -1,
  jumpFrames: number,
  timeCount: number,
): number {
  const maximumTimepoint = Math.max(1, Math.floor(timeCount));
  const jump = Math.max(1, Math.floor(jumpFrames));
  const timepoint = Math.min(maximumTimepoint, Math.max(1, Math.floor(current) + 1));
  const aligned =
    direction === 1
      ? (Math.floor(timepoint / jump) + 1) * jump
      : (Math.ceil(timepoint / jump) - 1) * jump;
  return Math.min(maximumTimepoint, Math.max(1, aligned)) - 1;
}

/** Follow the active direction, or look back by one jump at an endpoint. */
export function prefetchTimeIndex(
  current: number,
  direction: 1 | -1,
  jumpFrames: number,
  timeCount: number,
): number | null {
  const forward = moveTimeIndex(current, direction, jumpFrames, timeCount);
  if (forward !== current) return forward;

  const reverse = moveTimeIndex(current, direction === 1 ? -1 : 1, jumpFrames, timeCount);
  return reverse === current ? null : reverse;
}
