import { isReady } from "../../compat/stub";
import { t } from "../../i18n/runtime";
import { API } from "../ws/connect";
import {
  FILES_MAX_PAGE_SIZE,
  FILES_PAGE_SIZE,
  type ArtifactIndexPage,
  type ArtifactRow,
  type ArtifactVersionRow,
} from "./types";

export { API };

export type FetchFn = (input: string, init?: RequestInit) => Promise<Response>;

let fetchImpl: FetchFn | null = null;

export function setArtifactsFetch(fn: FetchFn | null): void {
  fetchImpl = fn;
}

export function artifactsFetch(input: string, init?: RequestInit): Promise<Response> {
  const impl = fetchImpl ?? (globalThis as { fetch?: FetchFn }).fetch;
  if (!impl) return Promise.reject(new Error("fetch is not available"));
  return impl(input, init);
}

/** app.js:93-101 */
export class ApiError extends Error {
  code: string;
  status: number;
  requestId: string;
  body: unknown;
  constructor(body: unknown, httpStatus: number) {
    const rec = body && typeof body === "object" ? (body as Record<string, unknown>) : null;
    super(String((rec && (rec.error || rec.detail)) || "HTTP " + httpStatus));
    this.name = "ApiError";
    this.code = rec && rec.code != null ? String(rec.code) : "";
    this.status = rec && rec.status != null ? Number(rec.status) : httpStatus;
    this.requestId = rec && rec.request_id != null ? String(rec.request_id) : "";
    this.body = body;
  }
}

/** app.js:107-109 */
export function apiErrorText(e: unknown): string {
  const err = e as { message?: unknown; requestId?: unknown } | null;
  const msg = err && err.message != null ? String(err.message) : String(e);
  return err && err.requestId ? `${msg} [${err.requestId}]` : msg;
}

/**
 * app.js:111-118. `p` must be an internal same-origin API path.
 */
export async function api(p: string, o: RequestInit = {}): Promise<unknown> {
  if (typeof p !== "string" || p[0] !== "/" || p[1] === "/") {
    throw new Error("invalid api path");
  }
  const r = await artifactsFetch(API + p, {
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

export function asArtifactList(value: unknown): ArtifactRow[] {
  if (!Array.isArray(value)) return [];
  const rows: ArtifactRow[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const rec = item as Record<string, unknown>;
    const id = rec.id || rec.artifact_id;
    if (id == null || id === "") continue;
    rows.push({ ...rec, id: String(id) } as ArtifactRow);
  }
  return rows;
}

export type ArtifactIndexQuery = {
  /** Filename substring. Omitted when empty — the page is then the most recent rows. */
  q?: string;
  contentType?: string;
  origin?: string;
  cursor?: string | null;
  /** Clamped to 1…100. Omitted uses the Files page size (50). */
  limit?: number;
  signal?: AbortSignal;
};

/** Page size for artifact-index. Empty, non-finite, or <1 falls back to the Files page. */
export function clampIndexLimit(limit: number | undefined): number {
  if (limit == null || !Number.isFinite(limit) || limit < 1) return FILES_PAGE_SIZE;
  return Math.min(Math.max(1, Math.floor(limit)), FILES_MAX_PAGE_SIZE);
}

/**
 * One page of `GET /projects/{pid}/artifact-index`. The only URL builder and
 * DTO parse for that route: Files and composer autocomplete both call it.
 * `pid` is encoded. An empty `q` is left off the query string.
 */
export async function fetchArtifactIndexPage(
  pid: string,
  query: ArtifactIndexQuery = {},
): Promise<ArtifactIndexPage> {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.contentType) params.set("content_type", query.contentType);
  if (query.origin) params.set("origin", query.origin);
  if (query.cursor) params.set("cursor", query.cursor);
  params.set("limit", String(clampIndexLimit(query.limit)));
  const init: RequestInit = {};
  if (query.signal) init.signal = query.signal;
  const body = await api(
    `/projects/${encodeURIComponent(pid)}/artifact-index?${params.toString()}`,
    init,
  );
  if (!body || typeof body !== "object") {
    return { artifacts: [], next_cursor: null, has_more: false };
  }
  const rec = body as Record<string, unknown>;
  return {
    artifacts: asArtifactList(rec.artifacts),
    next_cursor: rec.next_cursor == null ? null : String(rec.next_cursor),
    has_more: !!rec.has_more,
  };
}

export function asVersionList(value: unknown): ArtifactVersionRow[] {
  if (!value || typeof value !== "object") return [];
  const raw = (value as { versions?: unknown }).versions;
  if (!Array.isArray(raw)) return [];
  const rows: ArtifactVersionRow[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const rec = item as Record<string, unknown>;
    if (typeof rec.version_id !== "string" || !rec.version_id) continue;
    rows.push(rec as ArtifactVersionRow);
  }
  return rows;
}

/** app.js:12919 */
export function bytes(b: number | null | undefined): string {
  b = b || 0;
  if (b < 1024) return b + " B";
  if (b < 1048576) return (b / 1024).toFixed(1) + " KB";
  return (b / 1048576).toFixed(1) + " MB";
}

/** Shortest unbroken `[A-Za-z0-9+/=]` run that can be an encoded blob. */
const BLOB_RUN = 1200;
/** Consecutive `\xNN` escapes that make an escape dump. */
const ESCAPE_RUN = 400;
/**
 * Characters examined for blobs and escape dumps. A kernel stream is capped at
 * 1,000,000 characters plus a truncation marker (MAX_OUTPUT_CHARS in
 * `openai4s/kernel/protocol.py`), so every cell output is judged whole; a
 * longer file is judged by its first MiB, well past the 300,000 characters
 * its text preview shows before "Show complete text".
 */
export const BINARY_SCAN_LIMIT = 1 << 20;

function isHexDigit(c: number): boolean {
  return (c >= 48 && c <= 57) || (c >= 65 && c <= 70) || (c >= 97 && c <= 102);
}

/**
 * One pass over the scan window for an encoded blob or an escape dump.
 *
 * This was `/[A-Za-z0-9+/=]{1200,}/` and `/(?:\\x[0-9a-fA-F]{2}){400,}/` over
 * the whole string, on every call -- once per streamed Notebook chunk. A run
 * shorter than the minimum made the engine retry from every start inside it,
 * so line-wrapped sequence output cost about a second per MiB.
 *
 * Length alone is not evidence of a blob: a protein or DNA sequence written
 * on one line is exactly such a run (a 1273-residue spike FASTA record).
 * Base64 of real bytes mixes both letter cases with digits, `+` and `/` --
 * 2.7-26% of its characters across random, float, integer and text payloads
 * -- while a sequence is letters, carrying at most a name's serial number or
 * a trailing length. A run counts only with both cases and that punctuation
 * at 1% or more of its length; a dump of zero bytes ("AAAA...") stays text,
 * as a poly-A tract must.
 */
function encodedSpan(s: string): boolean {
  const end = Math.min(s.length, BINARY_SCAN_LIMIT);
  let run = 0;
  let upper = false;
  let lower = false;
  let marks = 0;
  let escapes = 0;
  let escapeEnd = -1;
  for (let i = 0; i <= end; i++) {
    const c = i < end ? s.charCodeAt(i) : -1;
    if (c >= 97 && c <= 122) lower = true;
    else if (c >= 65 && c <= 90) upper = true;
    else if ((c >= 48 && c <= 57) || c === 43 || c === 47) marks++;
    else if (c !== 61) {
      if (run >= BLOB_RUN && upper && lower && marks * 100 >= run) return true;
      run = 0;
      upper = lower = false;
      marks = 0;
      if (c === 92 && s.charCodeAt(i + 1) === 120 && isHexDigit(s.charCodeAt(i + 2)) && isHexDigit(s.charCodeAt(i + 3))) {
        escapes = i === escapeEnd ? escapes + 1 : 1;
        if (escapes >= ESCAPE_RUN) return true;
        escapeEnd = i + 4;
      }
      continue;
    }
    run++;
  }
  return false;
}

/**
 * Raw bytes decoded as text: control characters (other than tab, newline and
 * carriage return), DEL and U+FFFD over 12% of the first 4 KiB. This is the
 * whole test for JSON, whose long string values look like encoded blobs.
 */
export function controlDense(text: string): boolean {
  const sample = text.slice(0, 4096);
  let ctrl = 0;
  for (let i = 0; i < sample.length; i++) {
    const c = sample.charCodeAt(i);
    if (c === 9 || c === 10 || c === 13) continue;
    if (c < 32 || c === 127 || c === 0xfffd) ctrl++;
  }
  return sample.length > 0 && ctrl / sample.length > 0.12;
}

/**
 * app.js:6055-6066. Heuristic: raw binary, a giant base64 blob, or a long
 * `\xNN` escape dump.
 */
export function looksBinary(s: string | null | undefined): boolean {
  if (!s) return false;
  return controlDense(s) || encodedSpan(s);
}

/** The element helper for this lane and islands/dom.ts. */
export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string | null,
  text?: string | number | null,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = String(text);
  return node;
}

export function svgElement(name: string, attrs?: Record<string, string | number>): SVGElement {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  }
  return node;
}

const ICONS: Record<string, string> = {
  file: '<path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  files:
    '<path d="M20 7h-3a2 2 0 0 1-2-2V2"/><path d="M9 18a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h7l4 4v10a2 2 0 0 1-2 2Z"/><path d="M3 7.6v12.8A1.6 1.6 0 0 0 4.6 22h9.8"/>',
  notebook:
    '<path d="M2 6h4"/><path d="M2 10h4"/><path d="M2 14h4"/><path d="M2 18h4"/><rect width="16" height="20" x="4" y="2" rx="2"/><path d="M16 2v20"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  "file-text":
    '<path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/><line x1="16" x2="8" y1="13" y2="13"/><line x1="16" x2="8" y1="17" y2="17"/><line x1="10" x2="8" y1="9" y2="9"/>',
  table:
    '<path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2V9M9 21H5a2 2 0 0 1-2-2V9m0 0h18"/>',
  type: '<polyline points="4 7 4 4 20 4 20 7"/><line x1="9" x2="15" y1="20" y2="20"/><line x1="12" x2="12" y1="4" y2="20"/>',
  atom: '<circle cx="12" cy="12" r="1"/><path d="M20.2 20.2c2.04-2.03.02-7.36-4.5-11.9-4.54-4.52-9.87-6.54-11.9-4.5-2.04 2.03-.02 7.36 4.5 11.9 4.54 4.52 9.87 6.54 11.9 4.5Z"/><path d="M15.7 15.7c4.52-4.54 6.54-9.87 4.5-11.9-2.03-2.04-7.36-.02-11.9 4.5-4.52 4.54-6.54 9.87-4.5 11.9 2.03 2.04 7.36.02 11.9-4.5Z"/>',
  package:
    '<path d="M16.5 9.4 7.55 4.24"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>',
  "alert-triangle":
    '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  loader:
    '<path d="M12 2v4"/><path d="m16.2 7.8 2.9-2.9"/><path d="M18 12h4"/><path d="m16.2 16.2 2.9 2.9"/><path d="M12 18v4"/><path d="m4.9 19.1 2.9-2.9"/><path d="M2 12h4"/><path d="m4.9 4.9 2.9 2.9"/>',
};

export function icon(name: string, size = 16): string {
  const path = ICONS[name] || ICONS.file || "";
  return `<svg class="ic-svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
}

export function iconEl(name: string, size = 16, extraClass?: string): HTMLElement {
  const span = el("span", extraClass ? "ic " + extraClass : "ic");
  span.innerHTML = icon(name, size);
  return span;
}

type HostWindow = Record<string, unknown> & {
  t?: (key: string, ...args: unknown[]) => string;
};

export function hostWindow(): HostWindow {
  return globalThis as unknown as HostWindow;
}

/** Call a later-lane window export. Stubs are functions; `isReady` is the only gate. */
export function callWindow(name: string, ...args: unknown[]): unknown {
  const fn = hostWindow()[name];
  if (!isReady(fn)) return undefined;
  return (fn as (...a: unknown[]) => unknown)(...args);
}

export function translate(key: string, ...args: unknown[]): string {
  const fromWindow = hostWindow().t;
  if (isReady(fromWindow)) return fromWindow(key, ...args);
  return t(key, ...args);
}

/** Frozen preview budgets (PLAN §2.4). */
export const PREVIEW_MAX_BYTES = 8 * 1024 * 1024;

/** Frozen display limits (PLAN §2.4) — display budgets, never read budgets. */
export const MARKDOWN_PREVIEW_CHARACTERS = 1_000_000;
export const CHEMISTRY_SOURCE_PREVIEW_CHARACTERS = 300_000;
export const LATEX_SOURCE_PREVIEW_CHARACTERS = 500_000;
export const MOLECULE_FALLBACK_PREVIEW_CHARACTERS = 8_000;
export const LEGACY_TABLE_PREVIEW_CHARACTERS = 300_000;
export const THUMB_MAX_BYTES = 256 * 1024;
export const THUMB_MAX_CONCURRENCY = 4;
export const THUMB_READ_TIMEOUT_MS = 15_000;

/**
 * Why an artifact read did not produce a usable text preview. `too-large` is a
 * proven over-budget body, `unbounded` a response whose size cannot be proven
 * (no readable stream), `timeout` the bounded wait from initiation, `cancelled`
 * the caller leaving, and `read-failed` anything else (HTTP error, network).
 */
export type ArtifactReadFailure =
  | "too-large"
  | "unbounded"
  | "timeout"
  | "cancelled"
  | "read-failed";

/** A read that the preview budget, its deadline, or its owner stopped. */
export class ArtifactReadError extends Error {
  code: ArtifactReadFailure;
  /** The byte budget the read was enforcing. */
  maxBytes: number;
  /** Bytes actually read from the response body before stopping. */
  bytes: number;
  constructor(
    code: ArtifactReadFailure,
    message: string,
    context: { maxBytes: number; bytes?: number },
  ) {
    super(message);
    this.name = "ArtifactReadError";
    this.code = code;
    this.maxBytes = context.maxBytes;
    this.bytes = context.bytes || 0;
  }
}

export type ArtifactReadOptions = {
  /** Byte budget for the decoded body. Defaults to the main preview budget. */
  maxBytes?: number;
  /** Cancels the read when its view goes away or a newer read replaces it. */
  signal?: AbortSignal | null;
  /** Time from initiating the request to the last body byte read. */
  timeoutMs?: number;
  /**
   * Size from trusted artifact metadata. Used only to reject early; a body
   * larger than its own headers (missing, wrong, compressed) is still stopped
   * while streaming, so this value can never bypass the budget.
   */
  declaredBytes?: number | null;
  /**
   * Keep the prefix read before the budget was exceeded instead of failing.
   * Only thumbnail text tiles may do this: structure parsers must never see a
   * partial document, and a partial file cannot claim a total row count.
   */
  allowPartial?: boolean;
};

export type ArtifactTextRead = {
  text: string;
  /** Bytes read from the response body. */
  bytes: number;
  /** True when the read stopped at `maxBytes`; `text` is then a prefix. */
  truncated: boolean;
};

function aborted(error: unknown): boolean {
  const e = error as { name?: unknown } | null;
  if (e && e.name === "AbortError") return true;
  return e instanceof DOMException && e.name === "AbortError";
}

/**
 * Read one artifact version as text under a hard byte budget (R1).
 *
 * The budget is enforced on the bytes that actually arrive: the HTTP status is
 * checked first, and `declaredBytes` can only reject before the body is read.
 * Overflow cancels the reader immediately and never reaches a parser. Without
 * a readable stream a response's size cannot be proven, so automatic preview is
 * refused (`unbounded`) rather than read whole. Every failure is classified so
 * the UI can say "over the preview limit / read timed out / read failed" and
 * still offer the exact-version download.
 */
export async function readArtifactText(
  url: string,
  options: ArtifactReadOptions = {},
): Promise<ArtifactTextRead> {
  const maxBytes = options.maxBytes ?? PREVIEW_MAX_BYTES;
  const allowPartial = !!options.allowPartial;
  const declared = options.declaredBytes;
  if (!allowPartial && declared != null && Number.isFinite(declared) && declared > maxBytes) {
    throw new ArtifactReadError(
      "too-large",
      `artifact is ${declared} bytes, over the ${maxBytes}-byte preview budget`,
      { maxBytes, bytes: 0 },
    );
  }
  const controller = new AbortController();
  let timedOut = false;
  const external = options.signal || null;
  const onAbort = (): void => controller.abort();
  if (external) {
    if (external.aborted) controller.abort();
    else external.addEventListener("abort", onAbort, { once: true });
  }
  const timeoutMs = options.timeoutMs && options.timeoutMs > 0 ? options.timeoutMs : 0;
  const timer = timeoutMs
    ? setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, timeoutMs)
    : null;
  const finish = (): void => {
    if (timer) clearTimeout(timer);
    if (external) external.removeEventListener("abort", onAbort);
  };
  try {
    let response: Response;
    try {
      response = await artifactsFetch(url, { signal: controller.signal });
    } catch (error) {
      if (timedOut) {
        throw new ArtifactReadError("timeout", "artifact read timed out", { maxBytes });
      }
      if (controller.signal.aborted || aborted(error)) {
        throw new ArtifactReadError("cancelled", "artifact read cancelled", { maxBytes });
      }
      throw new ArtifactReadError("read-failed", "artifact read failed", { maxBytes });
    }
    // The HTTP status is checked before any body byte is interpreted.
    if (!response.ok) throw new ApiError(null, response.status);
    const body = response.body;
    if (!body || typeof body.getReader !== "function") {
      throw new ArtifactReadError(
        "unbounded",
        "response cannot be read as a bounded stream; automatic preview refused",
        { maxBytes },
      );
    }
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let bytes = 0;
    let text = "";
    let truncated = false;
    // A body read may stay pending forever on a stalled transport; racing the
    // abort signal turns the deadline/cancellation into a real stop even for a
    // stream that is not wired to the fetch's own abort handling.
    const stop = new Promise<void>((resolve) => {
      if (controller.signal.aborted) resolve();
      else controller.signal.addEventListener("abort", () => resolve(), { once: true });
    });
    try {
      for (;;) {
        const pending = reader.read();
        pending.catch(() => {
          /* an aborted stream may reject after this read was abandoned */
        });
        const step = await Promise.race([pending, stop.then(() => "aborted" as const)]);
        if (step === "aborted") {
          try {
            await reader.cancel();
          } catch {
            /* already closed */
          }
          if (timedOut) {
            throw new ArtifactReadError("timeout", "artifact read timed out", { maxBytes, bytes });
          }
          throw new ArtifactReadError("cancelled", "artifact read cancelled", { maxBytes, bytes });
        }
        const { done, value } = step;
        if (done) break;
        if (!value || !value.byteLength) continue;
        const over = bytes + value.byteLength - maxBytes;
        if (over > 0) {
          // Stop at the budget: decode no more than the remaining allowance,
          // cancel the transport, and never touch the rest of the body.
          const keep = Math.max(0, value.byteLength - over);
          if (keep > 0) {
            text += decoder.decode(value.subarray(0, keep), { stream: true });
            bytes += keep;
          }
          truncated = true;
          try {
            await reader.cancel();
          } catch {
            /* already closed */
          }
          break;
        }
        bytes += value.byteLength;
        text += decoder.decode(value, { stream: true });
      }
      if (!truncated) text += decoder.decode();
    } catch (error) {
      try {
        await reader.cancel();
      } catch {
        /* already closed */
      }
      if (timedOut) {
        throw new ArtifactReadError("timeout", "artifact read timed out", { maxBytes, bytes });
      }
      if (controller.signal.aborted || aborted(error)) {
        throw new ArtifactReadError("cancelled", "artifact read cancelled", { maxBytes, bytes });
      }
      throw new ArtifactReadError("read-failed", "artifact read failed", { maxBytes, bytes });
    }
    if (truncated && !allowPartial) {
      throw new ArtifactReadError(
        "too-large",
        `artifact exceeds the ${maxBytes}-byte preview budget`,
        { maxBytes, bytes },
      );
    }
    return { text, bytes, truncated };
  } finally {
    finish();
  }
}

/** True when a read stopped only because its view went away (never shown as an error). */
export function isArtifactReadCancelled(error: unknown): boolean {
  return error instanceof ArtifactReadError && error.code === "cancelled";
}

/**
 * The read scope of the artifact currently open in the viewer. A newer paint
 * aborts it, so replacing a preview cancels its transport instead of only
 * ignoring the late result.
 */
let previewScope: AbortController | null = null;

export function beginPreviewScope(): AbortSignal {
  previewScope?.abort();
  previewScope = new AbortController();
  return previewScope.signal;
}

/** Options for one preview read in the current scope; metadata may only reject early. */
export function previewReadOptions(a?: { size_bytes?: number | null } | null): ArtifactReadOptions {
  return { signal: previewScope?.signal, declaredBytes: a ? a.size_bytes : null };
}

/**
 * Slice for display without splitting a surrogate pair. Returning half of an
 * astral character (emoji, some CJK extensions) would render a replacement
 * glyph that looks like corrupted content rather than a labelled prefix.
 */
export function unicodeSafeSlice(text: string, limit: number): string {
  if (limit <= 0 || !text) return "";
  if (text.length <= limit) return text;
  const last = text.charCodeAt(limit - 1);
  const end = last >= 0xd800 && last <= 0xdbff ? limit - 1 : limit;
  return text.slice(0, Math.max(0, end));
}

/**
 * Markdown shown only up to the character limit. A fenced code block cut open
 * by the limit is closed so the retained prefix renders as the partial report
 * it is instead of swallowing the completeness note into a code block.
 */
export function truncateMarkdown(
  text: string,
  limit: number = MARKDOWN_PREVIEW_CHARACTERS,
): { text: string; truncated: boolean } {
  if (text.length <= limit) return { text, truncated: false };
  const sliced = unicodeSafeSlice(text, limit);
  const fences = (sliced.match(/^```/gm) || []).length;
  return { text: fences % 2 === 1 ? sliced + "\n```\n" : sliced, truncated: true };
}

/**
 * Complete text under the main preview budget (8 MiB). Callers that display a
 * prefix (thumbnails) use `readArtifactText` with `allowPartial`; structure
 * parsers must use this one so they can never see half a document.
 */
export function fetchArtifactText(url: string, options: ArtifactReadOptions = {}): Promise<string> {
  return readArtifactText(url, options).then((read) => read.text);
}

export function isApiStatus(e: unknown, status: number, code?: string): boolean {
  if (!(e instanceof ApiError)) return false;
  if (e.status !== status) return false;
  if (code && e.code !== code) return false;
  return true;
}
