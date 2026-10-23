import { delimiterFor, parseDelimited, tableShape } from "../csv/csv";
import { _thumbCache } from "../../stores/artifacts";
import { artifactCacheKey, artUrl } from "./cache";
import {
  ArtifactReadError,
  el,
  icon,
  isArtifactReadAborted,
  looksBinary,
  readArtifactPrefix,
  THUMB_READ_CONCURRENCY,
  THUMB_READ_MAX_BYTES,
} from "./api";
import { filesT, readFailureText } from "./copy";
import type { ArtifactRow } from "./types";
import { MOL_EXT, TEXT_EXT } from "./types";

export type MolPoint = { x: number; y: number; z: number };

/** Tile previews kept at once; the least recently shown goes first. */
export const THUMB_CACHE_LIMIT = 64;

/**
 * Frozen thumbnail budget (F1): one file contributes at most 256 KiB read as a
 * prefix, at most four reads run at once, and one read (any mode) may not take
 * longer than the shared 15 s deadline measured from when it actually starts.
 */
export { THUMB_READ_CONCURRENCY, THUMB_READ_MAX_BYTES };

type ThumbKind = "text" | "data" | "mol";

/**
 * One in-flight or queued tile read. Every tile DOM element showing the same
 * cache key is a consumer; a read is dropped only when *all* of its consumers
 * have left the document, so one view closing can never cancel another view's
 * shared read.
 */
type ThumbRead = {
  key: string;
  controller: AbortController;
  consumers: Set<Element>;
};

const thumbReads = new Map<string, ThumbRead>();

/** Global FIFO: a read starts in the order it was requested, at most four at a time. */
let thumbActive = 0;
const thumbQueue: Array<() => void> = [];

function acquireThumbSlot(): Promise<void> {
  if (thumbActive < THUMB_READ_CONCURRENCY) {
    thumbActive += 1;
    return Promise.resolve();
  }
  return new Promise<void>((resolve) => {
    thumbQueue.push(() => {
      thumbActive += 1;
      resolve();
    });
  });
}

function releaseThumbSlot(): void {
  thumbActive = Math.max(0, thumbActive - 1);
  const next = thumbQueue.shift();
  if (next) next();
}

function allConsumersDetached(read: ThumbRead): boolean {
  if (!read.consumers.size) return true;
  for (const consumer of read.consumers) {
    // Real Elements always carry a boolean; a test double without the property
    // is treated as attached so it cannot silently cancel the read.
    if (consumer.isConnected !== false) return false;
  }
  return true;
}

/**
 * Abort tile reads whose every consumer has left the document. Called on every
 * conversation/Files repaint and whenever the Files pane is left, so a read
 * nobody can see does not keep downloading or later write into a detached tile.
 * A read shared with a mounted view is kept.
 */
export function pruneThumbReads(): void {
  for (const [key, read] of thumbReads) {
    if (!allConsumersDetached(read)) continue;
    read.controller.abort();
    thumbReads.delete(key);
    const cache = _thumbCache.value as Record<string, Promise<unknown>>;
    delete cache[key];
  }
}

/**
 * app.js:8428-8432, keyed and bounded. One read per artifact version and
 * preview kind, keeping only what the tile shows.
 *
 * The key was id + size, so a new version of the same length kept the old
 * preview; an error page, or a read that failed, stayed cached for the page's
 * life; and data tiles downloaded and parsed the whole file again on every
 * Files repaint. Entries now live in a bounded LRU, a failed or cancelled read
 * is evicted so the next paint retries it, and only the prefix the tile needs
 * is read (256 KiB, four at a time, shared 15 s deadline).
 */
function thumbRead<T>(
  kind: ThumbKind,
  a: ArtifactRow,
  derive: (read: { text: string; truncated: boolean }) => T,
  consumer: HTMLElement,
): Promise<T> {
  const cache = _thumbCache.value as Record<string, Promise<unknown>>;
  const key = `${kind}|${artifactCacheKey(a)}|${a.size_bytes ?? ""}`;
  const hit = cache[key] as Promise<T> | undefined;
  if (hit) {
    delete cache[key];
    cache[key] = hit;
    thumbReads.get(key)?.consumers.add(consumer);
    return hit;
  }
  const controller = new AbortController();
  const read: ThumbRead = { key, controller, consumers: new Set([consumer]) };
  thumbReads.set(key, read);
  const promise = acquireThumbSlot()
    .then(() => {
      // Dropped before it started: every tile that wanted it has gone.
      if (allConsumersDetached(read)) {
        throw new ArtifactReadError("aborted", "thumbnail read has no attached consumer", {
          maxBytes: THUMB_READ_MAX_BYTES,
        });
      }
      return readArtifactPrefix(artUrl(a), {
        maxBytes: THUMB_READ_MAX_BYTES,
        signal: controller.signal,
      });
    })
    .then(derive)
    .finally(() => {
      releaseThumbSlot();
      if (thumbReads.get(key) === read) thumbReads.delete(key);
    });
  cache[key] = promise;
  // A failed or cancelled read must not stay cached: the next paint retries it.
  promise.catch(() => {
    if (cache[key] === promise) delete cache[key];
  });
  const keys = Object.keys(cache);
  for (const stale of keys.slice(0, Math.max(0, keys.length - THUMB_CACHE_LIMIT))) delete cache[stale];
  return promise;
}

/** The first line's cells, for a data tile that only has a prefix. */
function partialColumns(text: string, a: ArtifactRow): string[] {
  const firstLine = text.replace(/\r/g, "").split("\n", 1)[0] || "";
  const separator = delimiterFor(a.filename, a.content_type, firstLine);
  const row = parseDelimited(firstLine, separator)[0] || [];
  return row.map((cell) => cell.trim()).filter(Boolean);
}

/** A text tile: its first 16 lines, at most 900 characters; null when binary. */
function textSnippet(txt: string): string | null {
  if (looksBinary(txt)) return null;
  return txt
    .slice(0, 4096)
    .replace(/\r/g, "")
    .split("\n")
    .slice(0, 16)
    .join("\n")
    .slice(0, 900)
    .replace(/\s+$/, "");
}

/** app.js:8435 */
export function thumbFallback(d: HTMLElement, name?: string): void {
  d.className = "big";
  d.innerHTML = icon(name || "file", 28);
}

/**
 * app.js:8455-8473. Prefers CA backbone (PDB fixed columns); falls back to
 * all atoms, then to whitespace-split xyz. Caps at 500 points.
 */
export function parseMolPoints(txt: string | null | undefined): MolPoint[] {
  const lines = (txt || "").split("\n");
  const all: MolPoint[] = [];
  const ca: MolPoint[] = [];
  for (const ln of lines) {
    if (ln.startsWith("ATOM") || ln.startsWith("HETATM")) {
      const x = parseFloat(ln.slice(30, 38));
      const y = parseFloat(ln.slice(38, 46));
      const z = parseFloat(ln.slice(46, 54));
      if (!isFinite(x) || !isFinite(y)) continue;
      const p = { x, y, z: isFinite(z) ? z : 0 };
      all.push(p);
      if (ln.slice(12, 16).trim() === "CA") ca.push(p);
    }
  }
  let pts: MolPoint[] = ca.length >= 3 ? ca : all;
  if (!pts.length) {
    pts = [];
    for (const ln of lines) {
      const m = ln.trim().split(/\s+/);
      if (m.length >= 4) {
        const x = parseFloat(m[1] || "");
        const y = parseFloat(m[2] || "");
        const z = parseFloat(m[3] || "");
        if (isFinite(x) && isFinite(y) && isFinite(z)) pts.push({ x, y, z });
      }
    }
  }
  if (pts.length > 500) {
    const step = Math.ceil(pts.length / 500);
    pts = pts.filter((_, i) => i % step === 0);
  }
  return pts;
}

/**
 * app.js:8477-8490. Spectrum-colored XY projection (blue→red along the chain),
 * Z as depth cue for radius/opacity.
 */
export function molSvg(pts: MolPoint[]): string {
  const W = 180,
    H = 104,
    pad = 12;
  let minx = Infinity,
    maxx = -Infinity,
    miny = Infinity,
    maxy = -Infinity,
    minz = Infinity,
    maxz = -Infinity;
  for (const p of pts) {
    minx = Math.min(minx, p.x);
    maxx = Math.max(maxx, p.x);
    miny = Math.min(miny, p.y);
    maxy = Math.max(maxy, p.y);
    minz = Math.min(minz, p.z);
    maxz = Math.max(maxz, p.z);
  }
  const sx = maxx - minx || 1;
  const sy = maxy - miny || 1;
  const zr = maxz - minz || 1;
  const scale = (Math.min(W, H) - 2 * pad) / Math.max(sx, sy);
  const ox = (W - sx * scale) / 2;
  const oy = (H - sy * scale) / 2;
  const last = pts.length - 1 || 1;
  let dots = "";
  pts.forEach((p, i) => {
    const cx = ox + (p.x - minx) * scale;
    const cy = H - (oy + (p.y - miny) * scale);
    const hue = 240 - 240 * (i / last);
    const depth = (p.z - minz) / zr;
    dots += `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${(1.4 + depth * 1.6).toFixed(1)}" fill="hsl(${hue.toFixed(0)} 65% 52%)" opacity="${(0.45 + depth * 0.5).toFixed(2)}"/>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="100%" preserveAspectRatio="xMidYMid meet">${dots}</svg>`;
}

function dataCol(iconName: string, label: string): HTMLElement {
  const c = el("div", "col");
  const ic = el("span", "ic");
  ic.innerHTML = icon(iconName, 12);
  c.appendChild(ic);
  c.appendChild(el("span", null, label));
  return c;
}

function fillDataPreview(d: HTMLElement, a: ArtifactRow): void {
  thumbRead(
    "data",
    a,
    (read) => ({
      truncated: read.truncated,
      shape: read.truncated ? null : tableShape(read.text, a),
      columns: read.truncated ? partialColumns(read.text, a) : [],
    }),
    d,
  )
    .then((state) => {
      if (d.isConnected === false) return;
      d.innerHTML = "";
      if (state.truncated) {
        // Only a prefix was read: name the columns, never a row count.
        d.appendChild(el("div", "rc", filesT("files.thumb.partial")));
        state.columns.slice(0, 3).forEach((cn) => d.appendChild(dataCol("type", cn)));
        return;
      }
      if (!state.shape) {
        d.appendChild(dataCol("table", "data"));
        return;
      }
      const { rows, columns: cols } = state.shape;
      d.appendChild(
        el(
          "div",
          "rc",
          rows +
            (rows === 1 ? " row · " : " rows · ") +
            cols.length +
            (cols.length === 1 ? " column" : " columns"),
        ),
      );
      cols.slice(0, 3).forEach((cn) => d.appendChild(dataCol("type", cn)));
    })
    .catch((error: unknown) => {
      if (d.isConnected === false) return;
      d.innerHTML = "";
      d.appendChild(dataCol("table", "data"));
      // A structured tile cannot show a reason inline, but it must not hide
      // why it fell back: keep the classified reason as the tile's tooltip.
      if (!isArtifactReadAborted(error)) d.title = readFailureText(error);
    });
}

function fillTextPreview(d: HTMLElement, a: ArtifactRow): void {
  thumbRead(
    "text",
    a,
    (read) => ({ snippet: textSnippet(read.text), truncated: read.truncated }),
    d,
  )
    .then(({ snippet: snip, truncated }) => {
      if (d.isConnected === false) return;
      if (snip === null) return thumbFallback(d, "file");
      if (!snip.trim()) return thumbFallback(d, "file-text");
      // Only a thumbnail text preview may show a prefix: the rest of the body
      // was never read, and the download keeps the complete exact version.
      d.textContent = (truncated ? filesT("files.thumb.partial") + "\n" : "") + snip;
    })
    .catch((error: unknown) => {
      if (d.isConnected === false || isArtifactReadAborted(error)) return;
      thumbFallback(d, "file-text");
      d.title = readFailureText(error);
    });
}

function fillMolPreview(d: HTMLElement, a: ArtifactRow): void {
  thumbRead(
    "mol",
    a,
    (read) => ({ points: parseMolPoints(read.text), truncated: read.truncated }),
    d,
  )
    .then(({ points: pts, truncated }) => {
      if (d.isConnected === false) return;
      if (pts.length < 3) return thumbFallback(d, "atom");
      d.innerHTML = molSvg(pts);
      if (truncated) d.appendChild(el("div", "renderer-note", filesT("files.thumb.partial")));
    })
    .catch((error: unknown) => {
      if (d.isConnected === false) return;
      thumbFallback(d, "atom");
      if (!isArtifactReadAborted(error)) d.title = readFailureText(error);
    });
}

/** app.js:8416-8424 */
export function tileThumb(a: ArtifactRow): HTMLElement {
  const t = el("div", "thumb");
  const ct = a.content_type || "";
  const nm = (a.filename || "").toLowerCase();
  if (ct.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg)$/i.test(nm)) {
    const im = el("img");
    im.src = artUrl(a);
    t.appendChild(im);
  } else if (/csv|tsv/.test(ct) || /\.(csv|tsv)$/i.test(nm)) {
    const d = el("div", "data");
    d.appendChild(el("div", "rc", "…"));
    t.appendChild(d);
    fillDataPreview(d, a);
  } else if (MOL_EXT.test(nm)) {
    const d = el("div", "molmini");
    t.appendChild(d);
    fillMolPreview(d, a);
  } else if (/\bjson\b/.test(ct) || /\.json$/i.test(nm)) {
    const d = el("div", "data");
    d.appendChild(el("div", "rc", "…"));
    t.appendChild(d);
    fillDataPreview(d, a);
  } else if (TEXT_EXT.test(nm) || ct.startsWith("text/")) {
    const d = el("div", "txt");
    t.appendChild(d);
    fillTextPreview(d, a);
  } else {
    const b = el("span", "big");
    b.innerHTML = icon("file", 28);
    t.appendChild(b);
  }
  return t;
}

export function tileThumbBig(a: ArtifactRow): HTMLElement {
  const t = tileThumb(a);
  t.className = "a-thumb";
  return t;
}
