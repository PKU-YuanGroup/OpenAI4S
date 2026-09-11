/**
 * Same-origin JSON client used by Customize.
 * Port of app.js:83-119. Path must be a single leading slash (no scheme, no //host).
 */

import { invalidateDiagnostics } from "./state";
import { CUST_LOAD_TIMEOUT_MS } from "./load";

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
  if (isDiagnosticsConfigWrite(p, o.method || "GET")) invalidateDiagnostics();
  return (j && typeof j === "object" ? j : {}) as Record<string, unknown>;
}

/** Exact configuration routes used by Customize; probes and jobs stay excluded. */
const CONFIG_POSTS = new Set([
  "/model-profiles", "/connectors", "/permissions", "/permissions/reset",
  "/config/llm", "/search/config", "/doubao-search/config", "/datapro/config",
  "/compute/remote", "/volcengine/configure", "/volcengine/disconnect",
]);

function isDiagnosticsConfigWrite(path: string, method: string): boolean {
  const verb = method.toUpperCase();
  const route = path.split("?", 1)[0] || "";
  if (verb === "POST") return CONFIG_POSTS.has(route)
    || /^\/model-profiles\/[^/]+\/activate$/.test(route);
  if (verb === "PATCH") return /^\/model-profiles\/[^/]+$/.test(route);
  if (verb === "PUT") return route === "/network/status"
    || /^\/connectors\/[^/]+\/enabled$/.test(route);
  if (verb === "DELETE") return /^\/(?:model-profiles|connectors|permissions|compute\/remote)\/[^/]+$/.test(route);
  return false;
}

export type DiagnosticCheck = {
  name: string;
  status: "ok" | "warn" | "fail";
  detail: string;
  remedy?: string;
  facts?: Record<string, unknown>;
};
export type DiagnosticsChecks = {
  status: DiagnosticCheck["status"];
  request_id: string;
  checks: DiagnosticCheck[];
};

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function checkStatus(value: unknown): value is DiagnosticCheck["status"] {
  return value === "ok" || value === "warn" || value === "fail";
}

/** Required fields cannot turn an unreadable report into a successful empty one. */
function diagnosticsChecks(body: Record<string, unknown>): DiagnosticsChecks {
  const requestId = typeof body.request_id === "string" ? body.request_id : "";
  const invalid = () => new ApiError({
    error: "Invalid diagnostics response", code: "invalid_response", request_id: requestId,
  }, 200);
  if (!checkStatus(body.status) || !Array.isArray(body.checks)) throw invalid();
  const checks = body.checks.map((row: unknown): DiagnosticCheck => {
    if (!record(row) || typeof row.name !== "string" || !row.name.trim()
      || !checkStatus(row.status) || typeof row.detail !== "string"
      || (row.remedy !== undefined && typeof row.remedy !== "string")
      || (row.facts !== undefined && !record(row.facts))) throw invalid();
    return {
      name: row.name, status: row.status, detail: row.detail,
      ...(row.remedy !== undefined ? { remedy: row.remedy as string } : {}),
      ...(row.facts !== undefined ? { facts: row.facts as Record<string, unknown> } : {}),
    };
  });
  const worst = checks.some((row) => row.status === "fail") ? "fail"
    : checks.some((row) => row.status === "warn") ? "warn" : "ok";
  if (body.status !== worst) throw invalid();
  return { status: body.status, request_id: requestId, checks };
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

export async function runDiagnosticsChecks(): Promise<DiagnosticsChecks> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => {
      const error = new DOMException("The diagnostics request timed out.", "TimeoutError");
      reject(error);
      // Stop the browser's request, without claiming the server's checks
      // have stopped. The race also bounds transports that ignore abort.
      controller.abort(error);
    }, CUST_LOAD_TIMEOUT_MS);
  });
  try {
    const body = await Promise.race([
      api("/diagnostics/checks", { method: "POST", body: "{}", signal: controller.signal }),
      deadline,
    ]);
    return diagnosticsChecks(body);
  } finally {
    clearTimeout(timer);
  }
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
  // Deferred like every other object-URL site in this tree (sessions/actions,
  // islands/viewer): the specs intend the click to capture the blob
  // synchronously, but WebKit has not honoured that on adjacent blob-URL
  // paths, and a same-tick revoke is the one pattern with no upside.
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}
