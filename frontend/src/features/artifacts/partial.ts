/**
 * The F3 `partial` contract, built in one place: the persistent notice naming
 * how much is shown, and the exact-version download that accompanies it.
 * Every renderer that can show part of a file builds them here, so the role,
 * the data-state and the anchor cannot drift apart between previews.
 */

import { el, translate } from "./api";
import { filesT } from "./copy";
import type { ArtifactRow } from "./types";

/**
 * How much was shown, as a persistent `role="status"` notice. Complete previews
 * add nothing, so short content renders exactly as it did before the
 * truncation work; a failed read is the third state (`rendererFailure`).
 */
export function partialNotice(container: HTMLElement, shown: number): void {
  const note = el(
    "div",
    "renderer-note renderer-completeness",
    filesT("viewer.preview.partial", shown.toLocaleString()),
  );
  note.setAttribute("data-state", "partial");
  note.setAttribute("role", "status");
  container.appendChild(note);
}

/** The exact-version download anchor. */
export function downloadLink(url: string, a: ArtifactRow): HTMLAnchorElement {
  const link = el("a", "outline-btn small", translate("common.download")) as HTMLAnchorElement;
  link.href = url;
  link.setAttribute("download", a.filename || "artifact");
  return link;
}

/** The exact-version download that must accompany every partial preview. */
export function previewDownload(container: HTMLElement, a: ArtifactRow, url: string): void {
  container.appendChild(downloadLink(url, a));
}
