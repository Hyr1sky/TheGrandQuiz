import createClient from "openapi-fetch";
import type { paths } from "./generated/schema";

export const apiClient = createClient<paths>({
  baseUrl: globalThis.location?.origin ?? "http://localhost",
  fetch: (request) => globalThis.fetch(request),
});

export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly code = "request_failed",
    readonly retryable = false,
    readonly traceId: string | null = null,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

export function toApiRequestError(error: unknown): ApiRequestError {
  if (typeof error !== "object" || error === null) {
    return new ApiRequestError("请求失败");
  }
  const candidate = error as Record<string, unknown>;
  return new ApiRequestError(
    typeof candidate.message === "string" ? candidate.message : "请求失败",
    typeof candidate.code === "string" ? candidate.code : "request_failed",
    candidate.retryable === true,
    typeof candidate.trace_id === "string" ? candidate.trace_id : null,
  );
}
