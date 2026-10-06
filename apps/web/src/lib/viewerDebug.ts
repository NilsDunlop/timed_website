const DEBUG_PARAMETER = "viewer";
const DEBUG_STORAGE_KEY = "timed.viewer.debug";

export function viewerDebugEnabled(): boolean {
  if (typeof window === "undefined") return false;
  const requested = new URLSearchParams(window.location.search)
    .get("debug")
    ?.split(",")
    .map((value) => value.trim().toLowerCase());
  if (requested?.includes(DEBUG_PARAMETER) === true) return true;
  try {
    return window.localStorage.getItem(DEBUG_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function viewerDebug(
  event: string,
  details: Record<string, unknown> = {},
): void {
  if (!viewerDebugEnabled()) return;
  const elapsed = Math.round(window.performance.now());
  console.info(`[TIMED viewer +${elapsed}ms] ${event}`, details);
}
