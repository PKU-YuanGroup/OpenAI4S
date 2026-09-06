import {
  _artBust,
  _artifactLoadReq,
  _projArtFor,
  artifacts as artifactsSignal,
  dockArtifact,
  filesScope,
} from "../../stores/artifacts";
import { currentId, project } from "../../stores/session";
import { activeTab, dock, provMode } from "../../stores/ui";
import { api, asArtifactList, callWindow } from "./api";
import { syncArtifactVersion } from "./cache";
import { browseFiles } from "./files-index";
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
  const request = (_artifactLoadReq.value || 0) + 1;
  _artifactLoadReq.value = request;
  let a: ArtifactRow[] = [];
  try {
    a = asArtifactList(await api(`/frames/${encodeURIComponent(id)}/artifacts`));
  } catch {
    a = [];
  }
  if (id !== currentId.value || request !== _artifactLoadReq.value) return;
  let refreshProv = false;
  a.forEach((x) => {
    const v = x.version_id || x.latest_version_id || x.checksum;
    const changed = syncArtifactVersion(x, false);
    if (changed && v) _artBust.value[x.id] = v;
    const docked = dockArtifact.value as ArtifactRow | null;
    if (changed && provMode.value && docked && docked.id === x.id) refreshProv = true;
  });
  artifactsSignal.value = a;
  if (renderConversationArtifactsImpl) renderConversationArtifactsImpl();
  if (refreshProv && dockArtifact.value) callWindow("showProvenance", dockArtifact.value);
  if (dockOpenOnFiles()) {
    if (filesScope.value === "project") {
      await browseFiles({ reset: true });
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
  if (!force && _projArtFor.value === pid && filesScope.value === "project") return;
  await browseFiles({ reset: true });
  _projArtFor.value = pid;
}

export async function setFilesScope(scope: string): Promise<void> {
  filesScope.value = scope === "project" ? "project" : "frame";
  if (filesScope.value === "project") await loadProjectArtifacts(true);
  else await browseFiles({ reset: true });
  if (renderFilesGridImpl) renderFilesGridImpl();
}
