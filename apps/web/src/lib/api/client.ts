import createClient from "openapi-fetch";

import type { paths as ConnectorPaths } from "./connector-schema";
import type { paths as DataServerPaths } from "./dataserver-schema";

export const connectorClient = createClient<ConnectorPaths>();
export const dataServerClient = createClient<DataServerPaths>({
  baseUrl: "/backend",
});

export const webClientHeaders = {
  "X-Timed-Client": "web",
} as const;

export function apiErrorMessage(error: unknown, fallback: string): string {
  if (typeof error === "string") {
    return error;
  }
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail?: unknown }).detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (item && typeof item === "object" && "msg" in item) {
            return String((item as { msg: unknown }).msg);
          }
          return String(item);
        })
        .join("; ");
    }
  }
  return fallback;
}
