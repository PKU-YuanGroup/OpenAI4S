import { dockArtifact, filesScope } from "../../stores/artifacts";
import { bindNotebookArtifact } from "../notebook/cells";
import { activeTab, dock } from "../../stores/ui";
import { callWindow } from "./api";
import { artifactTabKey, syncArtifactVersion } from "./cache";
import { loadProjectArtifacts } from "./load";
import { renderFilesGrid, renderViewer } from "./ui";
import type { ArtifactRow } from "./types";
import type { WsMessage } from "../ws/types";

function artifactFromEvent(m: WsMessage): Record<string, unknown> {
  const nested = m.artifact;
  if (nested && typeof nested === "object" && !Array.isArray(nested)) return nested;
  return {};
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
  if (bindNotebookArtifact(m)) callWindow("nbRender");
  if (filesScope.value === "project") {
    void loadProjectArtifacts(true).then(() => {
      const d = dock.value as { open?: boolean } | null;
      if (d && d.open && activeTab.value === "files") renderFilesGrid();
    });
  }
}
