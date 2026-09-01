/**
 * Same-origin /api/v1 fetch that keeps HTTP status on failure so a 409
 * fork-without-checkpoint is distinguishable from other errors.
 */

/** Local copy so this module does not import the WS layer (and its stores). */
export const API = "/api/v1";

export type FetchFn = (input: string, init?: RequestInit) => Promise<Response>;

let fetchImpl: FetchFn | null = null;

export function setExecutionFetch(fn: FetchFn | null): void {
  fetchImpl = fn;
}

function executionFetch(input: string, init?: RequestInit): Promise<Response> {
  const impl = fetchImpl ?? (globalThis as { fetch?: FetchFn }).fetch;
  if (!impl) return Promise.reject(new Error("fetch is not available"));
  return impl(input, init);
}

export class ApiError extends Error {
  name = "ApiError";
  code: string;
  status: number;
  requestId: string;
  body: unknown;

  constructor(body: unknown, httpStatus: number) {
    const rec = body && typeof body === "object" ? (body as Record<string, unknown>) : null;
    super(String((rec && (rec.error || rec.detail)) || "HTTP " + httpStatus));
    this.code = rec && rec.code != null ? String(rec.code) : "";
    this.status = Number.isFinite(Number(httpStatus)) ? Number(httpStatus) : 0;
    this.requestId = rec && rec.request_id != null ? String(rec.request_id) : "";
    this.body = body;
  }
}

export function apiErrorText(e: unknown): string {
  const err = e as { message?: unknown; requestId?: unknown } | null;
  const msg = err && err.message != null ? String(err.message) : String(e);
  return err && err.requestId ? `${msg} [${err.requestId}]` : msg;
}

export async function api(p: string, o: RequestInit = {}): Promise<unknown> {
  if (typeof p !== "string" || p[0] !== "/" || p[1] === "/") {
    throw new Error("invalid api path");
  }
  const r = await executionFetch(API + p, {
    headers: { "content-type": "application/json" },
    ...o,
  });
  const text = await r.text();
  let j: unknown = null;
  try {
    j = text ? JSON.parse(text) : null;
  } catch {
    j = text;
  }
  if (!r.ok) throw new ApiError(j, r.status);
  return j;
}
