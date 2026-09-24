import { tableShape } from "../csv/csv";
import { _thumbCache } from "../../stores/artifacts";
import { artifactCacheKey, artUrl } from "./cache";
import { el, fetchArtifactText, icon, looksBinary } from "./api";
import type { ArtifactRow } from "./types";
import { MOL_EXT, TEXT_EXT } from "./types";

export type MolPoint = { x: number; y: number; z: number };

/** Tile previews kept at once; the least recently shown goes first. */
export const THUMB_CACHE_LIMIT = 64;

/**
 * app.js:8428-8432, keyed and bounded. One read per artifact version and
 * preview kind, keeping only what the tile shows.
 *
 * The key was id + size, so a new version of the same length kept the old
 * preview; an error page, or a read that failed, stayed cached for the page's
 * life; and data tiles downloaded and parsed the whole file again on every
 * Files repaint. Entries now live in a bounded LRU, and a failed read is
 * dropped so the next paint retries it.
 */
function thumbRead<T>(kind: string, a: ArtifactRow, derive: (text: string) => T): Promise<T> {
  const cache = _thumbCache.value as Record<string, Promise<unknown>>;
  const key = `${kind}|${artifactCacheKey(a)}|${a.size_bytes ?? ""}`;
  const hit = cache[key] as Promise<T> | undefined;
  if (hit) {
    delete cache[key];
    cache[key] = hit;
    return hit;
  }
  const read = fetchArtifactText(artUrl(a)).then(derive);
  cache[key] = read;
  read.catch(() => {
    if (cache[key] === read) delete cache[key];
  });
  const keys = Object.keys(cache);
  for (const stale of keys.slice(0, Math.max(0, keys.length - THUMB_CACHE_LIMIT))) delete cache[stale];
  return read;
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
  thumbRead("data", a, (txt) => tableShape(txt, a))
    .then((shape) => {
      d.innerHTML = "";
      if (!shape) {
        d.appendChild(dataCol("table", "data"));
        return;
      }
      const { rows, columns: cols } = shape;
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
    .catch(() => {
      d.innerHTML = "";
      d.appendChild(dataCol("table", "data"));
    });
}

function fillTextPreview(d: HTMLElement, a: ArtifactRow): void {
  thumbRead("text", a, textSnippet)
    .then((snip) => {
      if (snip === null) return thumbFallback(d, "file");
      if (!snip.trim()) return thumbFallback(d, "file-text");
      d.textContent = snip;
    })
    .catch(() => thumbFallback(d, "file-text"));
}

function fillMolPreview(d: HTMLElement, a: ArtifactRow): void {
  thumbRead("mol", a, parseMolPoints)
    .then((pts) => {
      if (pts.length < 3) return thumbFallback(d, "atom");
      d.innerHTML = molSvg(pts);
    })
    .catch(() => thumbFallback(d, "atom"));
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
