/**
 * Relative images in a Markdown Artifact (ART-2).
 *
 * `![growth](growth.png)` in a report means the file next to the report. The
 * Markdown renderer leaves such an image as `<img data-md-src>` with no `src`;
 * this resolves the path against the report's own session Artifacts and loads
 * the sibling through the same bounded, cancellable preview read as the image
 * viewer, as a blob URL. Nothing outside the report's session is consulted, and
 * a path that climbs above the workspace resolves to nothing.
 */

import { artifacts as artifactsSignal, artifactsFrameId } from "../../stores/artifacts";
import {
  api,
  artifactMediaType,
  asArtifactList,
  el,
  isArtifactReadAborted,
  previewObjectUrl,
  previewReadSignal,
  readArtifactBytes,
} from "./api";
import { artUrl } from "./cache";
import { filesT } from "./copy";
import type { ArtifactRow } from "./types";

function slashes(value: unknown): string {
  return String(value || "").replace(/\\/g, "/");
}

/**
 * The workspace-relative path `src` names when written in `reportFilename`,
 * or null when it is empty or climbs above the workspace root.
 */
export function siblingPath(reportFilename: string | null | undefined, src: string): string | null {
  let raw = String(src || "").split(/[?#]/, 1)[0] || "";
  try {
    raw = decodeURIComponent(raw);
  } catch {
    /* a literal % stays as written */
  }
  const parts = slashes(reportFilename).split("/").slice(0, -1).filter((part) => part && part !== ".");
  for (const part of slashes(raw).split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") {
      if (!parts.length) return null;
      parts.pop();
      continue;
    }
    parts.push(part);
  }
  return parts.length ? parts.join("/") : null;
}

/**
 * The sibling Artifact a resolved path names: the exact relative filename
 * first, else a basename only one Artifact in the session carries (a figure
 * captured flat while the report links `figures/growth.png`).
 */
export function findSibling(rows: readonly ArtifactRow[], report: ArtifactRow, path: string): ArtifactRow | null {
  const root = report.root_frame_id || null;
  const scope = rows.filter(
    (row) => !!row && row.id !== report.id && (!root || !row.root_frame_id || row.root_frame_id === root),
  );
  const name = (row: ArtifactRow): string => slashes(row.filename).replace(/^\.\//, "");
  const exact = scope.find((row) => name(row) === path);
  if (exact) return exact;
  const base = path.split("/").pop() || "";
  const byBase = scope.filter((row) => name(row).split("/").pop() === base);
  return byBase.length === 1 ? byBase[0] || null : null;
}

/** The report's session Artifacts: the loaded list when it is that session's, else one read. */
async function sessionRows(report: ArtifactRow): Promise<ArtifactRow[]> {
  const root = report.root_frame_id || null;
  const loaded = artifactsSignal.value as ArtifactRow[];
  if (!root || artifactsFrameId.value === root) return Array.isArray(loaded) ? loaded : [];
  try {
    return asArtifactList(await api(`/frames/${encodeURIComponent(root)}/artifacts`));
  } catch {
    return Array.isArray(loaded) ? loaded : [];
  }
}

function markMissing(img: HTMLImageElement, src: string): void {
  if (!img.parentNode) return;
  const note = el("span", "md-relimg-missing", filesT("viewer.md.imageMissing", src));
  const alt = img.getAttribute("alt") || "";
  if (alt) note.title = alt;
  img.replaceWith(note);
}

/** Resolve and load every `img[data-md-src]` under `container`. */
export function resolveRelativeImages(container: HTMLElement, report: ArtifactRow): void {
  if (typeof container.querySelectorAll !== "function") return;
  const images = Array.from(container.querySelectorAll<HTMLImageElement>("img[data-md-src]"));
  if (!images.length) return;
  const signal = previewReadSignal(container);
  void sessionRows(report).then((rows) => {
    for (const img of images) {
      const src = img.getAttribute("data-md-src") || "";
      const path = siblingPath(report.filename, src);
      const sibling = path ? findSibling(rows, report, path) : null;
      if (!sibling) {
        markMissing(img, src);
        continue;
      }
      img.onerror = () => markMissing(img, src);
      readArtifactBytes(artUrl(sibling), { signal })
        .then((bytes) => {
          if (!container.isConnected) return;
          img.src = previewObjectUrl(
            bytes,
            artifactMediaType(sibling.filename, sibling.content_type, "application/octet-stream"),
            container,
          );
          img.title = sibling.filename || src;
        })
        .catch((error: unknown) => {
          if (isArtifactReadAborted(error) || !container.isConnected) return;
          markMissing(img, src);
        });
    }
  });
}
