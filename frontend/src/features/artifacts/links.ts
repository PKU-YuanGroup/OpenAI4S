/**
 * Artifact links in rendered markdown open the in-app Viewer.
 *
 * A completion's "Artifacts:" list is markdown, `[report.md](/api/v1/artifacts/<id>)`,
 * which `renderMd` turns into an ordinary `target="_blank"` anchor. Following
 * one left the workbench for the stored bytes: a report as markdown source, a
 * table as CSV text. The Viewer renders that same Artifact, and the tile for
 * it a few lines below the list already opens it there.
 *
 * One delegated listener, as `md/copy.ts` has, because the anchors are
 * produced as HTML strings. Only a link to an Artifact of the open session is
 * taken over. Any other link, and any modified click (new tab, new window,
 * download), stays the browser's.
 */

import { artifacts } from "../../stores/artifacts";
import type { ArtifactRow } from "./types";
import { presentViewer } from "./ui";

const ARTIFACT_HREF = /^\/api\/v1\/artifacts\/([^/?#]+)$/;

type ClickLike = Pick<
  MouseEvent,
  "altKey" | "button" | "ctrlKey" | "defaultPrevented" | "metaKey" | "preventDefault" | "shiftKey" | "target"
>;

/** The open session's Artifact that `href` reads, or null to leave the link alone. */
export function linkedArtifact(href: string | null | undefined): ArtifactRow | null {
  const match = ARTIFACT_HREF.exec(String(href || ""));
  if (!match) return null;
  let id: string;
  try {
    id = decodeURIComponent(match[1] || "");
  } catch {
    return null;
  }
  const rows = artifacts.value as ArtifactRow[];
  return rows.find((row) => !!row && (row.id === id || row.artifact_id === id)) || null;
}

/** Open the Viewer for a plain click on a session Artifact link in markdown. */
export function openLinkedArtifact(click: ClickLike): boolean {
  if (click.defaultPrevented || click.button !== 0) return false;
  if (click.metaKey || click.ctrlKey || click.shiftKey || click.altKey) return false;
  const target = click.target as { closest?: (selector: string) => Element | null } | null;
  const anchor = target && typeof target.closest === "function" ? target.closest(".md a[href]") : null;
  const row = anchor ? linkedArtifact(anchor.getAttribute("href")) : null;
  if (!row) return false;
  click.preventDefault();
  presentViewer(row);
  return true;
}

let bound = false;

/** Idempotent; needs `document`, not any node in it. */
export function bindArtifactLinks(root: Pick<Document, "addEventListener">): void {
  if (bound) return;
  bound = true;
  root.addEventListener("click", (event) => {
    openLinkedArtifact(event as MouseEvent);
  });
}

/** Test seam: forget the one-time bind. */
export function resetArtifactLinkBinding(): void {
  bound = false;
}
