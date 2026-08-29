/** Versioned API root, ApiError, and the stdlib-style `api()` helper. app.js:84-119. */

export const API = "/api/v1";

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
    this.status = Number((rec && rec.status) || httpStatus);
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
  const r = await fetch(API + p, { headers: { "content-type": "application/json" }, ...o });
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
