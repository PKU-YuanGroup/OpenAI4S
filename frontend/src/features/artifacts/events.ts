import { dockArtifact, filesScope } from "../../stores/artifacts";
import { _liveCell, cells, liveCells } from "../../stores/notebook";
import { running } from "../../stores/stream";
import { activeTab, dock } from "../../stores/ui";
import { callWindow } from "./api";
import { syncArtifactVersion } from "./cache";
import { loadProjectArtifacts } from "./load";
import { renderFilesGrid, renderViewer } from "./ui";
import type { ArtifactRow } from "./types";
import type { WsMessage } from "../ws/types";

function artifactFromEvent(m: WsMessage): Record<string, unknown> {
  const nested = m.artifact;
  if (nested && typeof nested === "object" && !Array.isArray(nested)) return nested;
  return {};
}

function findCell(producer: unknown): { figures?: unknown[] } | null {
  if (!producer) return null;
  const id = String(producer);
  const bags = [liveCells.value, cells.value];
  for (const bag of bags) {
    if (!Array.isArray(bag)) continue;
    for (const item of bag) {
      if (!item || typeof item !== "object") continue;
      const rec = item as { id?: unknown; cell_id?: unknown };
      if (rec.id === id || rec.cell_id === id) return item as { figures?: unknown[] };
    }
  }
  return null;
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
    if (docked && docked.id === aid && activeTab.value === aid) {
      renderViewer();
    }
  }
  const fn = String(art.filename || m.filename || "");
  const isImg =
    /^image\//.test(String(art.content_type || "")) ||
    /\.(png|jpe?g|gif|svg|webp|bmp)$/i.test(fn);
  if (running.value && fn && isImg) {
    const producer = art.producing_cell_id || m.producing_cell_id;
    const live = _liveCell.value as { figures?: unknown[] } | null;
    const bag = liveCells.value as unknown[];
    const last = Array.isArray(bag) ? (bag[bag.length - 1] as { figures?: unknown[] } | undefined) : undefined;
    const cell = findCell(producer) || live || last || null;
    if (cell && !(cell.figures || []).includes(fn)) {
      cell.figures = cell.figures || [];
      cell.figures.push(fn);
      callWindow("nbRender");
    }
  }
  if (filesScope.value === "project") {
    void loadProjectArtifacts(true).then(() => {
      const d = dock.value as { open?: boolean } | null;
      if (d && d.open && activeTab.value === "files") renderFilesGrid();
    });
  }
}
