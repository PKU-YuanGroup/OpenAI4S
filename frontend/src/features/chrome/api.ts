/**
 * Same-origin JSON helper. Port of app.js:84-119.
 * Lives in this lane so upload / notes / palette do not wait on a shared client.
 */

export const API = "/api/v1";

export class ApiError extends Error {
  code: string;
  status: number;
  requestId: string;
  body: unknown;

  constructor(body: Record<string, unknown> | null, httpStatus: number) {
    const errorText = body && (body.error || body.detail);
    super(String(errorText || "HTTP " + httpStatus));
    this.name = "ApiError";
    this.code = String((body && body.code) || "");
    this.status = Number((body && body.status) || httpStatus);
    this.requestId = String((body && body.request_id) || "");
    this.body = body;
  }
}

export function apiErrorText(e: unknown): string {
  const err = e as { message?: string; requestId?: string };
  const msg = err && err.message ? String(err.message) : String(e);
  return err && err.requestId ? `${msg} [${err.requestId}]` : msg;
}

export async function api(path: string, opts: RequestInit = {}): Promise<unknown> {
  if (typeof path !== "string" || path[0] !== "/" || path[1] === "/") {
    throw new Error("invalid api path");
  }
  const r = await fetch(API + path, {
    headers: { "content-type": "application/json" },
    ...opts,
  });
  const text = await r.text();
  let parsed: unknown = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = text;
  }
  if (!r.ok) {
    const body =
      parsed && typeof parsed === "object"
        ? (parsed as Record<string, unknown>)
        : { error: parsed };
    throw new ApiError(body, r.status);
  }
  return parsed;
}
