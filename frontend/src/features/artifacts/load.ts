import {
  _artBust,
  _artifactLoadReq,
  _projArtFor,
  artifacts as artifactsSignal,
  artifactsFrameId,
  artifactsFrameGeneration,
  dockArtifact,
  filesScope,
} from "../../stores/artifacts";
import { _openGen, currentId, project } from "../../stores/session";
import { activeTab, dock, provMode } from "../../stores/ui";
import { api, asArtifactList, callWindow } from "./api";
import { syncArtifactVersion } from "./cache";
import { browseFiles, filesListingIsCurrent } from "./files-index";
import { artifactsReadError } from "./state";
import type { ArtifactRow } from "./types";

let renderFilesGridImpl: (() => void) | null = null;
let renderConversationArtifactsImpl: (() => void) | null = null;

export function setRenderFilesGridImpl(fn: (() => void) | null): void {
  renderFilesGridImpl = fn;
}

export function setRenderConversationArtifactsImpl(fn: (() => void) | null): void {
  renderConversationArtifactsImpl = fn;
}

function dockOpenOnFiles(): boolean {
  const d = dock.value as { open?: boolean } | null;
  return !!(d && d.open && activeTab.value === "files");
}

/**
 * app.js:8380-8401. REST reload of the open session's artifacts. A
 * generation token drops the result if the session switched mid-flight.
 */
export async function loadArtifacts(id: string): Promise<void> {
  if (id !== currentId.value) return;
  const generation = _openGen.value;
  const request = (_artifactLoadReq.value || 0) + 1;
  _artifactLoadReq.value = request;
  let a: ArtifactRow[] = [];
  let failed = false;
  try {
    a = asArtifactList(await api(`/frames/${encodeURIComponent(id)}/artifacts`));
  } catch {
    failed = true;
  }
  if (id !== currentId.value || request !== _artifactLoadReq.value || generation !== _openGen.value) return;
  // A failed read is not an empty session. A refresh keeps the list this
  // session already confirmed; a first read leaves an empty list of its own
  // (never the previous session's). Either way the grid reports the failure.
  artifactsReadError.value = failed ? { frameId: id, generation } : null;
  if (failed && artifactsFrameId.value === id && artifactsFrameGeneration.value === generation) {
    if (dockOpenOnFiles() && renderFilesGridImpl) renderFilesGridImpl();
    return;
  }
  let refreshProv = false;
  a.forEach((x) => {
    const v = x.version_id || x.latest_version_id || x.checksum;
    const changed = syncArtifactVersion(x, false);
    if (changed && v) _artBust.value[x.id] = v;
    const docked = dockArtifact.value as ArtifactRow | null;
    if (changed && provMode.value && docked && !docked._exactVersion && docked.id === x.id) refreshProv = true;
  });
  artifactsSignal.value = a;
  artifactsFrameId.value = id;
  artifactsFrameGeneration.value = generation;
  if (filesScope.value !== "project") await browseFiles({ refresh: true });
  if (renderConversationArtifactsImpl) renderConversationArtifactsImpl();
  if (refreshProv && dockArtifact.value) callWindow("showProvenance", dockArtifact.value);
  if (dockOpenOnFiles()) {
    if (filesScope.value === "project") {
      await browseFiles({ refresh: true });
    }
    if (renderFilesGridImpl) renderFilesGridImpl();
  }
}

/**
 * app.js:8510-8516. Project-wide listing is M-03's paged artifact-index.
 * `force` busts the per-project cache. There is no array-route fallback.
 */
export async function loadProjectArtifacts(force?: boolean): Promise<void> {
  const pid = project.value;
  if (!pid) {
    _projArtFor.value = null;
    return;
  }
  if (!force && _projArtFor.value === pid && filesScope.value === "project" && filesListingIsCurrent()) return;
  await browseFiles(force ? { refresh: true } : { reset: true });
  if (project.value === pid && filesScope.value === "project") _projArtFor.value = pid;
}

export async function setFilesScope(scope: string): Promise<void> {
  filesScope.value = scope === "project" ? "project" : "frame";
  if (filesScope.value === "project") await loadProjectArtifacts(true);
  else await browseFiles({ reset: true });
  if (renderFilesGridImpl) renderFilesGridImpl();
}
