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
  if (verb === "PATCH") return /^\/model-profiles\/[^/]+$/.test(route)
    || route === "/experimental/judgment";
  // `/models/default` activates the chosen profile (or rewrites `llm_model`);
  // `/connectors/<id>` is the connector editor's save.
  if (verb === "PUT") return route === "/network/status"
    || route === "/ollama-web/config"
    || route === "/experimental/judgment"
    || route === "/models/default"
    || /^\/connectors\/[^/]+(?:\/enabled)?$/.test(route);
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

export type JudgmentFlag = {
  enabled: boolean;
  source: string;
};

export type JudgmentDisclosureCap = {
  en: string;
  zh: string;
};

export type JudgmentStatus = {
  experimental: boolean;
  effective: Record<string, JudgmentFlag>;
  provider: string;
  model: string;
  key_configured: boolean;
  disclosure: {
    version: string;
    acked: boolean;
    acknowledged_capabilities: string[];
    capabilities: Record<string, JudgmentDisclosureCap>;
    facts: { en?: string; zh?: string };
  };
  egress: {
    mode: string;
    host: string;
    domain_allowed: boolean;
    remediation: string | null;
  };
};

export type JudgmentUpdate = {
  enabled?: boolean;
  capabilities?: Record<string, boolean>;
  acknowledge?: { version: string; provider: string; capabilities: string[] };
  api_key?: string;
  clear_api_key?: boolean;
};

export type JudgmentProbe = {
  status: string;
  error_code: string | null;
  latency_ms: number;
  model: string | null;
};

function flagOf(value: unknown): JudgmentFlag | null {
  if (!record(value) || typeof value.enabled !== "boolean") return null;
  return {
    enabled: value.enabled,
    source: typeof value.source === "string" ? value.source : "",
  };
}

function disclosureCap(value: unknown): JudgmentDisclosureCap | null {
  if (!record(value) || typeof value.en !== "string" || typeof value.zh !== "string") {
    return null;
  }
  return { en: value.en, zh: value.zh };
}

function judgmentStatus(body: Record<string, unknown>): JudgmentStatus {
  const requestId = typeof body.request_id === "string" ? body.request_id : "";
  const invalid = () =>
    new ApiError(
      { error: "Invalid judgment settings response", code: "invalid_response", request_id: requestId },
      200,
    );
  if (typeof body.experimental !== "boolean" || !record(body.effective)
    || typeof body.provider !== "string" || typeof body.model !== "string"
    || typeof body.key_configured !== "boolean" || !record(body.disclosure)
    || !record(body.egress)) {
    throw invalid();
  }
  const effective: Record<string, JudgmentFlag> = {};
  for (const [name, value] of Object.entries(body.effective)) {
    const flag = flagOf(value);
    if (!flag) throw invalid();
    effective[name] = flag;
  }
  const capsRaw = body.disclosure.capabilities;
  if (!record(capsRaw) || typeof body.disclosure.version !== "string"
    || typeof body.disclosure.acked !== "boolean") {
    throw invalid();
  }
  const capabilities: Record<string, JudgmentDisclosureCap> = {};
  for (const [name, value] of Object.entries(capsRaw)) {
    const cap = disclosureCap(value);
    if (!cap) throw invalid();
    capabilities[name] = cap;
  }
  const factsRaw = record(body.disclosure.facts) ? body.disclosure.facts : {};
  const remediation = body.egress.remediation;
  if (remediation !== null && typeof remediation !== "string") throw invalid();
  if (typeof body.egress.mode !== "string" || typeof body.egress.domain_allowed !== "boolean") {
    throw invalid();
  }
  return {
    experimental: body.experimental,
    effective,
    provider: body.provider,
    model: body.model,
    key_configured: body.key_configured,
    disclosure: {
      version: body.disclosure.version,
      acked: body.disclosure.acked,
      acknowledged_capabilities: Array.isArray(body.disclosure.acknowledged_capabilities)
        ? body.disclosure.acknowledged_capabilities.filter((name): name is string => typeof name === "string")
        : [],
      capabilities,
      facts: {
        ...(typeof factsRaw.en === "string" ? { en: factsRaw.en } : {}),
        ...(typeof factsRaw.zh === "string" ? { zh: factsRaw.zh } : {}),
      },
    },
    egress: {
      mode: body.egress.mode,
      host: typeof body.egress.host === "string" ? body.egress.host
        : body.provider === "typesafe" ? "api.typesafe.ai" : "",
      domain_allowed: body.egress.domain_allowed,
      remediation,
    },
  };
}

export async function getJudgmentStatus(): Promise<JudgmentStatus> {
  return judgmentStatus(await api("/experimental/judgment"));
}

export async function updateJudgmentSettings(
  body: JudgmentUpdate,
): Promise<JudgmentStatus> {
  return judgmentStatus(
    await api("/experimental/judgment", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  );
}

export async function testJudgmentConnection(): Promise<JudgmentProbe> {
  const body = await api("/experimental/judgment/test", {
    method: "POST",
    body: "{}",
  });
  const latency = body.latency_ms;
  const latencyMs = typeof latency === "number" && Number.isFinite(latency) ? Math.trunc(latency) : 0;
  return {
    status: typeof body.status === "string" ? body.status : "",
    error_code: typeof body.error_code === "string" ? body.error_code : null,
    latency_ms: latencyMs,
    model: typeof body.model === "string" ? body.model : null,
  };
}

/* --- U2 / §10 U2: explicit update checking (appendix B.2) -----------------
 *
 * Two routes, one feature. `GET /updates/status` is a read-only local
 * projection; `POST /updates/check` is the only call that may contact the
 * release index and only the user's explicit click issues it. This client
 * validates appendix B.2 strictly and degrades to `null` (the caller then says
 * it cannot confirm anything) rather than publishing a partially valid answer.
 */

export type UpdateCheckStatus = "update_available" | "up_to_date" | "unknown";
export const UPDATE_CHECK_STATUSES: readonly UpdateCheckStatus[] = [
  "update_available",
  "up_to_date",
  "unknown",
];
export type UpdateSource = "pypi" | "offline" | "none";
export const UPDATE_SOURCES: readonly UpdateSource[] = ["pypi", "offline", "none"];
export type UpdateIntegrity = "two_witnesses" | "single_witness" | "none";
export const UPDATE_INTEGRITY: readonly UpdateIntegrity[] = [
  "two_witnesses",
  "single_witness",
  "none",
];
export type UpdatePreviousKnown = {
  status: UpdateCheckStatus;
  latest: string;
  checked_at_ms: number;
};
export type UpdateTrust = {
  integrity: UpdateIntegrity;
  /** Always `not_established`: digest agreement is not a release signature. */
  authorship: "not_established";
};
export type UpdateCheck = {
  status: UpdateCheckStatus;
  reason: string;
  latest: string;
  source: UpdateSource;
  checked_at_ms: number;
  retry_after_at: number;
  cached: boolean;
  age_s: number;
  previous: UpdatePreviousKnown | null;
  trust: UpdateTrust;
};
export type UpdateChannel = {
  id: string;
  refusal_text_key: string;
  refusal_command: string[];
};
export type UpdateStatus = {
  running: string;
  channel: UpdateChannel;
  /** `null` is appendix B.2's "no cached answer yet". */
  check: UpdateCheck | null;
  apply_available: false;
};

function updateRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function updateText(value: unknown, limit: number): string | null {
  return typeof value === "string" ? value.slice(0, limit) : null;
}

function updateInt(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? Math.floor(value)
    : null;
}

function updateStatusOf(value: unknown): UpdateCheckStatus | null {
  return (UPDATE_CHECK_STATUSES as readonly string[]).includes(String(value))
    ? (value as UpdateCheckStatus)
    : null;
}

function updateSourceOf(value: unknown): UpdateSource | null {
  return (UPDATE_SOURCES as readonly string[]).includes(String(value))
    ? (value as UpdateSource)
    : null;
}

function updatePreviousOf(raw: unknown): UpdatePreviousKnown | null | undefined {
  if (raw == null) return null;
  const row = updateRecord(raw);
  if (!row) return undefined;
  // Appendix B.2 sends `{}` when there is nothing to carry forward.
  if (!Object.keys(row).length) return null;
  const status = updateStatusOf(row.status);
  const latest = updateText(row.latest, 64);
  const checked = updateInt(row.checked_at_ms);
  if (!status || latest === null || checked === null) return undefined;
  return { status, latest, checked_at_ms: checked };
}

function updateTrustOf(raw: unknown): UpdateTrust | undefined {
  const row = updateRecord(raw);
  if (!row) return undefined;
  const integrity = (UPDATE_INTEGRITY as readonly string[]).includes(
    String(row.integrity),
  )
    ? (row.integrity as UpdateIntegrity)
    : null;
  if (!integrity || row.authorship !== "not_established") return undefined;
  return { integrity, authorship: "not_established" };
}

function updateCheckOf(raw: unknown): UpdateCheck | null | undefined {
  if (raw === null) return null;
  const row = updateRecord(raw);
  if (!row) return undefined;
  const status = updateStatusOf(row.status);
  const reason = updateText(row.reason, 64);
  const latest = updateText(row.latest, 64);
  const source = updateSourceOf(row.source);
  const checked = updateInt(row.checked_at_ms);
  const retryAfter = updateInt(row.retry_after_at);
  const age = updateInt(row.age_s);
  const previous = updatePreviousOf(row.previous);
  const trust = updateTrustOf(row.trust);
  if (
    !status ||
    reason === null ||
    latest === null ||
    !source ||
    checked === null ||
    retryAfter === null ||
    age === null ||
    typeof row.cached !== "boolean" ||
    previous === undefined ||
    !trust
  ) {
    return undefined;
  }
  return {
    status,
    reason,
    latest,
    source,
    checked_at_ms: checked,
    retry_after_at: retryAfter,
    cached: row.cached,
    age_s: age,
    previous,
    trust,
  };
}

/**
 * Strict appendix B.2 projection. `null` means the payload is not the frozen
 * shape; the caller reports that it cannot confirm anything instead of
 * rendering whatever happened to parse.
 */
export function sanitizeUpdateStatus(raw: unknown): UpdateStatus | null {
  const row = updateRecord(raw);
  if (!row) return null;
  const running = updateText(row.running, 64);
  const channelRow = updateRecord(row.channel);
  if (running === null || !channelRow) return null;
  const id = updateText(channelRow.id, 64);
  const refusalKey = updateText(channelRow.refusal_text_key, 160);
  if (id === null || refusalKey === null) return null;
  const commandsRaw = channelRow.refusal_command;
  if (!Array.isArray(commandsRaw)) return null;
  const refusalCommand: string[] = [];
  for (const command of commandsRaw.slice(0, 8)) {
    const text = updateText(command, 300);
    if (text === null) return null;
    if (text) refusalCommand.push(text);
  }
  if (row.apply_available !== false) return null;
  const check = updateCheckOf(row.check);
  if (check === undefined) return null;
  return {
    running,
    channel: { id, refusal_text_key: refusalKey, refusal_command: refusalCommand },
    check,
    apply_available: false,
  };
}

/** The local, read-only status. `null` means the payload was not B.2-shaped. */
export async function getUpdateStatus(): Promise<UpdateStatus | null> {
  return sanitizeUpdateStatus(await api("/updates/status"));
}

/** The explicit check. Only a user click reaches this call. */
export async function checkForUpdates(
  refresh: boolean,
  signal?: AbortSignal,
): Promise<UpdateStatus | null> {
  return sanitizeUpdateStatus(
    await api("/updates/check", {
      method: "POST",
      body: JSON.stringify({ refresh }),
      signal,
    }),
  );
}

/** The bounded retry hint a 429 carries, for display only — never auto-retried. */
export function updateRetryAfterSeconds(error: unknown): number | null {
  const body = updateRecord((error as { body?: unknown } | null)?.body);
  return body ? updateInt(body.retry_after_s) : null;
}

/** Team mode refuses members before the cache read or any network access. */
export function isUpdateAdminOnly(error: unknown): boolean {
  return (
    error instanceof ApiError && error.status === 403 && error.code === "admin_only"
  );
}
