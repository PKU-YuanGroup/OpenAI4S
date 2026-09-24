import { dockArtifact, filesScope } from "../../stores/artifacts";
import { bindNotebookArtifact } from "../notebook/cells";
import { activeTab, dock } from "../../stores/ui";
import { artifactTabKey, syncArtifactVersion } from "./cache";
import { browseFiles } from "./files-index";
import { loadProjectArtifacts, markProjectListingStale } from "./load";
import { renderFilesGrid, renderViewer } from "./ui";
import type { ArtifactRow } from "./types";
import type { WsMessage } from "../ws/types";

function artifactFromEvent(m: WsMessage): Record<string, unknown> {
  const nested = m.artifact;
  if (nested && typeof nested === "object" && !Array.isArray(nested)) return nested;
  return {};
}

function filesVisible(): boolean {
  const d = dock.value as { open?: boolean } | null;
  return !!(d && d.open && activeTab.value === "files");
}

/** A burst of artifact_created (one cell writing many files) refreshes once. */
export const PROJECT_REFRESH_DELAY_MS = 250;
let projectRefresh: ReturnType<typeof setTimeout> | null = null;

/**
 * Project scope lists every session's files, so every session's
 * artifact_created lands here, and each used to walk the paged index even
 * with the dock closed. Only a visible Files tab refreshes, once per burst;
 * a hidden one is marked stale and refreshes when it is next shown.
 */
function scheduleProjectRefresh(): void {
  if (!filesVisible()) {
    markProjectListingStale();
    return;
  }
  if (projectRefresh !== null) clearTimeout(projectRefresh);
  projectRefresh = setTimeout(() => {
    projectRefresh = null;
    if (!filesVisible() || filesScope.value !== "project") {
      markProjectListingStale();
      return;
    }
    void loadProjectArtifacts(true).then(() => {
      if (filesVisible()) renderFilesGrid();
    });
  }, PROJECT_REFRESH_DELAY_MS);
}


/**
 * Remaining `artifact_created` body from app.js:5314-5346.
 * F-06 already upserted the row, busted `_artBust` / `_tbl`, and scheduled
 * `loadArtifacts`. This lane owns version-cache sync, open Viewer refresh,
 * live-cell figure paint, and project-scope Files reload.
 */
export function artifactCreatedSideEffects(m: WsMessage): void {
  const art = artifactFromEvent(m);
  const aid = art.id || art.artifact_id || m.artifact_id;
  if (aid) syncArtifactVersion(art, true);
  if (aid) {
    const docked = dockArtifact.value as ArtifactRow | null;
    if (docked && !docked._exactVersion && docked.id === aid && activeTab.value === artifactTabKey(docked)) {
      renderViewer();
    }
  }
  // Repaints the Notebook itself when a cell gained this file.
  bindNotebookArtifact(m);
  if (filesScope.value === "project") {
    scheduleProjectRefresh();
  } else {
    // The grid lists the paged Files index, not the store the WS upsert
    // wrote, so refresh the index explicitly.
    void browseFiles({ refresh: true }).then(() => {
      if (filesVisible()) renderFilesGrid();
    });
  }
}
