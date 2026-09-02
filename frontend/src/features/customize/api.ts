/**
 * Same-origin JSON client used by Customize.
 * Port of app.js:83-119. Path must be a single leading slash (no scheme, no //host).
 */

export const API = "/api/v1";

export class ApiError extends Error {
  code: string;
  status: number;
  requestId: string;
  body: unknown;

  constructor(body: unknown, httpStatus: number) {
    const rec =
      body && typeof body === "object" ? (body as Record<string, unknown>) : null;
    super(
      String((rec && (rec.error || rec.detail)) || "") || "HTTP " + httpStatus,
    );
    this.name = "ApiError";
    this.code = rec && typeof rec.code === "string" ? rec.code : "";
    this.status =
      rec && typeof rec.status === "number" ? rec.status : httpStatus;
    this.requestId = rec && typeof rec.request_id === "string" ? rec.request_id : "";
    this.body = body;
  }
}

export function apiErrorText(e: unknown): string {
  const err = e as { message?: unknown; requestId?: unknown } | null;
  const msg = err && err.message != null ? String(err.message) : String(e);
  return err && err.requestId ? `${msg} [${String(err.requestId)}]` : msg;
}

export async function api(
  p: string,
  o: RequestInit = {},
): Promise<Record<string, unknown>> {
  if (typeof p !== "string" || p[0] !== "/" || p[1] === "/") {
    throw new Error("invalid api path");
  }
  const r = await fetch(API + p, {
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
  return (j && typeof j === "object" ? j : {}) as Record<string, unknown>;
}

export type DiagnosticsStatus = {
  security: Record<string, unknown>;
  environment: Record<string, unknown>;
  request_id: string;
};

export async function getDiagnosticsStatus(): Promise<DiagnosticsStatus> {
  const body = await api("/diagnostics/status");
  const requestId =
    typeof body.request_id === "string" ? body.request_id : "";
  return {
    security:
      body.security && typeof body.security === "object"
        ? (body.security as Record<string, unknown>)
        : {},
    environment:
      body.environment && typeof body.environment === "object"
        ? (body.environment as Record<string, unknown>)
        : {},
    request_id: requestId,
  };
}

export async function runDiagnosticsChecks(): Promise<Record<string, unknown>> {
  return api("/diagnostics/checks", { method: "POST", body: "{}" });
}

export async function downloadDiagnosticsBundle(): Promise<void> {
  const r = await fetch(API + "/diagnostics/bundle", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "{}",
  });
  if (!r.ok) {
    const text = await r.text();
    let j: unknown = null;
    try {
      j = text ? JSON.parse(text) : null;
    } catch {
      j = text;
    }
    throw new ApiError(j, r.status);
  }
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "openai4s-diagnostics.zip";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
