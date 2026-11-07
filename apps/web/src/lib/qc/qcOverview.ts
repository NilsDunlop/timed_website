/**
 * The plate overview's channel columns: every channel of the triage rows in
 * first-seen order, including the whole-field `site` scope. The overview
 * offers exactly these values, so the URL restores exactly these values.
 */
export function overviewChannels(rows: readonly Record<string, unknown>[]): string[] {
  return [...new Set(rows.map((row) => String(row.channel ?? "")))].filter(Boolean);
}
