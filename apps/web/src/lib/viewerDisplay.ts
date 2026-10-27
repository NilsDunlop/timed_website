export type ViewerDisplayMode = "fluorescence" | "grayscale";

export type ViewerDisplayPreference = {
  mode: ViewerDisplayMode;
  grayscaleChannelName: string | null;
};

export type ViewerDisplayStorage = Pick<Storage, "getItem" | "setItem">;

const VIEWER_DISPLAY_KEY = "timed.viewer.display.v1";

export const defaultViewerDisplayPreference: ViewerDisplayPreference = {
  mode: "fluorescence",
  grayscaleChannelName: null,
};

export function readViewerDisplayPreference(
  storage: ViewerDisplayStorage,
): ViewerDisplayPreference {
  try {
    const encoded = storage.getItem(VIEWER_DISPLAY_KEY);
    if (!encoded) return defaultViewerDisplayPreference;
    const value: unknown = JSON.parse(encoded);
    if (!value || typeof value !== "object") {
      return defaultViewerDisplayPreference;
    }
    const candidate = value as Partial<ViewerDisplayPreference>;
    if (candidate.mode !== "fluorescence" && candidate.mode !== "grayscale") {
      return defaultViewerDisplayPreference;
    }
    return {
      mode: candidate.mode,
      grayscaleChannelName:
        typeof candidate.grayscaleChannelName === "string" &&
        candidate.grayscaleChannelName.length > 0
          ? candidate.grayscaleChannelName
          : null,
    };
  } catch {
    return defaultViewerDisplayPreference;
  }
}

export function writeViewerDisplayPreference(
  storage: ViewerDisplayStorage,
  preference: ViewerDisplayPreference,
): void {
  try {
    storage.setItem(VIEWER_DISPLAY_KEY, JSON.stringify(preference));
  } catch {
    // Viewer controls remain usable when browser storage is unavailable.
  }
}

export function preferredGrayscaleChannelIndex(
  channelNames: readonly string[],
  preferredName: string | null,
): number | null {
  if (channelNames.length === 0) return null;
  const preferredIndex = preferredName
    ? channelNames.indexOf(preferredName)
    : -1;
  return preferredIndex >= 0 ? preferredIndex : 0;
}
