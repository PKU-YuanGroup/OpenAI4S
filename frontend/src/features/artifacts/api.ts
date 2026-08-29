import { isReady } from "../../compat/stub";
import { t } from "../../i18n/runtime";
import { API } from "../ws/connect";
import type { ArtifactRow, ArtifactVersionRow } from "./types";

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

/**
 * app.js:6055-6066. Heuristic: raw binary / giant base64|hex blob.
 */
export function looksBinary(s: string | null | undefined): boolean {
  if (!s) return false;
  const sample = s.slice(0, 4096);
  let ctrl = 0;
  for (let i = 0; i < sample.length; i++) {
    const c = sample.charCodeAt(i);
    if (c === 9 || c === 10 || c === 13) continue;
    if (c < 32 || c === 127 || c === 0xfffd) ctrl++;
  }
  if (sample.length && ctrl / sample.length > 0.12) return true;
  return /[A-Za-z0-9+/=]{1200,}/.test(s) || /(?:\\x[0-9a-fA-F]{2}){400,}/.test(s);
}

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string | null,
  text?: string | null,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
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

export function fetchArtifactText(url: string): Promise<string> {
  return artifactsFetch(url).then((response) => {
    if (!response.ok) throw new ApiError(null, response.status);
    return response.text();
  });
}

export function isApiStatus(e: unknown, status: number, code?: string): boolean {
  if (!(e instanceof ApiError)) return false;
  if (e.status !== status) return false;
  if (code && e.code !== code) return false;
  return true;
}
