import {
  _artBust,
  _artVer,
  _envSnapById,
  dockArtifact,
} from "../../stores/artifacts";
import { _lineageFor, _lineageReq, lineage } from "../../stores/notebook";
import { openTabs } from "../../stores/ui";
import { API } from "./api";
import type { ArtifactPatch, ArtifactRow } from "./types";

function rec(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object") return null;
  return value as Record<string, unknown>;
}

/** app.js:8353-8357 */
export function artifactCacheKey(a: ArtifactRow | null | undefined): string {
  if (!a || !a.id) return "_live";
  const seen = _artVer.value && _artVer.value[a.id];
  const version =
    seen || a.version_id || a.latest_version_id || a.checksum || "unknown";
  return a.id + ":" + version;
}

/**
 * app.js:8359-8378. Mutates dock/openTabs in place so E2E identity holds.
 * Returns whether the seen version changed (or a forced dock refresh).
 */
export function syncArtifactVersion(patch: ArtifactPatch, force?: boolean): boolean {
  const aid = patch && (patch.id || patch.artifact_id);
  if (!aid) return false;
  const id = String(aid);
  const version = patch.version_id || patch.latest_version_id || patch.checksum;
  const seen = _artVer.value;
  const dock = rec(dockArtifact.value);
  const dockMatch = !!(dock && dock.id === id);
  const previous =
    seen[id] ||
    (dockMatch && dock && (dock.version_id || dock.latest_version_id || dock.checksum));
  const changed = !!(version && previous && previous !== version);
  if (version) seen[id] = version;
  const update: Record<string, unknown> = Object.assign({}, patch, { id });
  if (version) update.version_id = version;
  const tabs = openTabs.value;
  if (Array.isArray(tabs)) {
    tabs.forEach((item) => {
      const row = rec(item);
      if (row && row.id === id) Object.assign(row, update);
    });
  }
  if (dockMatch && dock) Object.assign(dock, update);
  if (dockMatch && (changed || force)) {
    lineage.value = null;
    _lineageFor.value = null;
    _lineageReq.value = (_lineageReq.value || 0) + 1;
    const key = artifactCacheKey(dock as ArtifactRow);
    const snaps = _envSnapById.value;
    if (snaps) delete snaps[key];
  }
  return changed || (dockMatch && !!force);
}

/**
 * app.js:8577, plus M-03 exact-version pin.
 *
 * A pinned `_exactVersion` artifact is addressed at `/artifacts/versions/{vid}`
 * so the byte path cannot silently serve latest. Unpinned artifacts keep the
 * original id URL plus `_artBust` query.
 */
export function artUrl(a: ArtifactRow): string {
  if (a._exactVersion && a.version_id) {
    return `${API}/artifacts/versions/${encodeURIComponent(String(a.version_id))}`;
  }
  const b = (_artBust.value || {})[a.id];
  return `${API}/artifacts/${a.id}` + (b ? `?_=${b}` : "");
}

export function artifactRendererVersion(a: ArtifactRow): string {
  return (a && (a.version_id || a.latest_version_id)) || "";
}
