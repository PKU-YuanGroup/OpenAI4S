import {
  dockArtifact,
  filesScope,
} from "../../stores/artifacts";
import { currentId, project, sessions } from "../../stores/session";
import { activeTab, dock, openTabs, provMode } from "../../stores/ui";
import { isReady } from "../../compat/stub";
import { bytes, callWindow, el, hostWindow, translate } from "./api";
import { filesT } from "./copy";
import {
  artifactDeepLinkHref,
  parseArtifactDeepLink,
  rememberViewerVersion,
  resolveArtifactVersion,
  versionResolveMessage,
} from "./deeplink";
import { browseFiles, filesGridArtifacts, visibleArtifacts } from "./files-index";
import { loadProjectArtifacts } from "./load";
import { renderArtifactBody } from "./renderers";
import { filesIndexError, filesIndexItems, viewerVersionState } from "./state";
import { tileThumb, tileThumbBig } from "./thumbs";
import type { ArtifactDeepLink, ArtifactRow } from "./types";

export function addOpenTab(a: ArtifactRow): void {
  const tabs = (openTabs.value as ArtifactRow[]) || [];
  if (!tabs.some((x) => x && x.id === a.id)) {
    openTabs.value = [...tabs, a];
  }
}

export function closeTab(id: string): void {
  const tabs = ((openTabs.value as ArtifactRow[]) || []).filter((x) => x && x.id !== id);
  openTabs.value = tabs;
  if (activeTab.value === id) {
    const last = tabs[tabs.length - 1];
    if (last) {
      dockArtifact.value = last;
      provMode.value = false;
      setActiveTab(last.id);
    } else setActiveTab("notebook");
  }
}

export function showDockPane(pane: string): void {
  if (typeof document === "undefined") return;
  ["viewer", "notebook", "timeline", "files"].forEach((p) => {
    const n = document.getElementById("dock-" + p);
    if (n) n.classList.toggle("hidden", p !== pane);
  });
}

export function dockOpen(): void {
  const d = dock.value as { open: boolean; tab: string };
  dock.value = { ...d, open: true, tab: d.tab };
}

export function setActiveTab(tab: string): void {
  activeTab.value = tab;
  dockOpen();
  const pane =
    tab === "notebook" ? "notebook" : tab === "timeline" ? "timeline" : tab === "files" ? "files" : "viewer";
  showDockPane(pane);
  if (tab === "notebook") callWindow("renderNotebook");
  else if (tab === "timeline") {
    callWindow("loadWorkbenchState", currentId.value);
    callWindow("renderActionTimeline");
  } else if (tab === "files") {
    void (async () => {
      if (filesScope.value === "project") await loadProjectArtifacts();
      else await browseFiles({ reset: true });
      renderFilesGrid();
    })();
  } else renderViewer();
}

export function sessionNameFor(frameId: string | null | undefined): string {
  const list = (sessions.value as Array<{ id?: string; name?: string; task_summary?: string }>) || [];
  const f = list.find((s) => s.id === frameId);
  return (f && (f.name || f.task_summary)) || translate("conv.title.default");
}

export function renderConversationArtifacts(): void {
  if (typeof document === "undefined") return;
  document.querySelectorAll(".generated, .uploaded").forEach((n) => n.remove());
  const arts = visibleArtifacts();
  if (!arts.length) return;
  const mkTile = (a: ArtifactRow) => {
    const tile = el("div", "tile");
    tile.appendChild(tileThumb(a));
    const fn = el("div", "tfn", a.filename || "artifact");
    if ((a.priority || 0) > 0) fn.textContent = "⭐ " + fn.textContent;
    tile.appendChild(fn);
    tile.onclick = () => openViewer(a);
    return tile;
  };
  const CAP = 6;
  const section = (cls: string, label: string, list: ArtifactRow[]) => {
    if (!list.length) return;
    const g = el("div", cls);
    g.appendChild(el("div", "gen-label", `${label} · ${list.length}`));
    const tiles = el("div", "gen-tiles");
    if (list.length <= CAP) {
      list.forEach((a) => tiles.appendChild(mkTile(a)));
    } else {
      list.slice(0, CAP - 1).forEach((a) => tiles.appendChild(mkTile(a)));
      const more = el("div", "tile tile-more");
      more.appendChild(el("div", "tile-more-n", translate("gen.more", list.length - (CAP - 1))));
      more.onclick = () => {
        more.remove();
        list.slice(CAP - 1).forEach((a) => tiles.appendChild(mkTile(a)));
      };
      tiles.appendChild(more);
    }
    g.appendChild(tiles);
    const host = document.getElementById("messages");
    if (!host) return;
    let review: HTMLElement | null = host.querySelector(".step-review");
    while (review && review.parentElement !== host) {
      review = review.parentElement as HTMLElement | null;
    }
    host.insertBefore(g, review || null);
  };
  section("uploaded", translate("art.uploaded"), arts.filter((a) => a.is_user_upload));
  section("generated", translate("art.generated"), arts.filter((a) => !a.is_user_upload));
  callWindow("down");
}

function paintVersionBanner(list: HTMLElement): void {
  const state = viewerVersionState.value;
  if (!state) return;
  const msg = versionResolveMessage(state);
  if (!msg) return;
  const note = el("div", "files-version-error", msg);
  list.appendChild(note);
}

export function renderFilesGrid(): void {
  if (typeof document === "undefined") return;
  const list = document.getElementById("results-list");
  const count = document.getElementById("results-count");
  if (!list) return;
  const arts = filesGridArtifacts();
  list.innerHTML = "";
  if (count) count.textContent = String(arts.length);
  paintVersionBanner(list);
  const indexErr = filesIndexError.value;
  if (indexErr && filesIndexItems.value.length === 0 && !arts.length) {
    list.appendChild(el("div", "files-empty", indexErr));
    return;
  }
  if (!arts.length) {
    const msg =
      filesScope.value === "project" ? translate("files.emptyProject") : translate("files.empty");
    list.appendChild(el("div", "files-empty", msg));
    return;
  }
  arts.forEach((a) => {
    const card = el("div", "art");
    card.appendChild(tileThumbBig(a));
    card.appendChild(
      el("div", "a-name", ((a.priority || 0) > 0 ? "⭐ " : "") + (a.filename || "artifact")),
    );
    card.appendChild(el("div", "a-meta", (a.content_type || "") + " · " + bytes(a.size_bytes)));
    if (filesScope.value === "project") {
      card.appendChild(el("div", "a-src", translate("files.fromSession", sessionNameFor(a.root_frame_id))));
    }
    card.onclick = () => openViewer(a);
    list.appendChild(card);
  });
}

export function renderViewer(): void {
  if (typeof document === "undefined") return;
  const a = dockArtifact.value as ArtifactRow | null;
  const v = document.getElementById("dock-viewer");
  if (!v) return;
  v.innerHTML = "";
  if (!a) {
    v.appendChild(el("div", "dock-empty", translate("viewer.empty")));
    return;
  }
  const head = el("div", "viewer-head");
  head.appendChild(el("div", "vh-name", a.filename || "artifact"));
  const acts = el("div", "vh-acts");
  const banner = versionResolveMessage(viewerVersionState.value);
  if (banner) {
    v.appendChild(el("div", "files-version-error", banner));
    if (viewerVersionState.value?.status === "stale" || viewerVersionState.value?.status === "not-found") {
      return;
    }
  }
  const copy = el("button", "outline-btn small", filesT("files.deeplink.copy"));
  copy.onclick = () => {
    const href = artifactDeepLinkHref(a.id, a._exactVersion ? a.version_id : null);
    const clip = (globalThis as { navigator?: { clipboard?: { writeText?: (s: string) => Promise<void> } } })
      .navigator?.clipboard?.writeText;
    if (isReady(clip)) void clip(href);
    copy.textContent = filesT("files.deeplink.copied");
  };
  acts.appendChild(copy);
  head.appendChild(acts);
  v.appendChild(head);
  if (provMode.value) {
    callWindow("renderProvenanceInto", v, a);
    return;
  }
  const body = el("div", "viewer-body");
  v.appendChild(body);
  renderArtifactBody(body, a);
}

/** app.js:9437. Artifact click → dock Viewer tab. */
export function openViewer(a: ArtifactRow): void {
  viewerVersionState.value = a._exactVersion
    ? { status: "exact", artifact: a, versionId: String(a.version_id || "") }
    : { status: "latest", artifact: a, versionId: a.version_id || null };
  dockArtifact.value = a;
  provMode.value = false;
  addOpenTab(a);
  setActiveTab(a.id);
}

/**
 * M-03: ⌘K / deep link. Open the owning session first, then the Viewer on
 * the exact version. A provided version_id never falls back to latest.
 */
export async function openArtifactFromHit(hit: {
  id: string;
  root_frame_id?: string | null;
  version_id?: string | null;
  filename?: string | null;
}): Promise<void> {
  const fid = hit.root_frame_id;
  if (fid && fid !== currentId.value) {
    callWindow("openConversation", fid, project.value);
  }
  await applyArtifactDeepLink({
    artifactId: hit.id,
    versionId: hit.version_id ? String(hit.version_id) : null,
  });
}

export async function applyArtifactDeepLink(link: ArtifactDeepLink): Promise<void> {
  const result = await resolveArtifactVersion(link);
  rememberViewerVersion(result);
  if (result.status === "exact" || result.status === "latest") {
    openViewer(result.artifact);
    return;
  }
  dockArtifact.value = { id: link.artifactId, artifact_id: link.artifactId };
  setActiveTab(link.artifactId);
  renderViewer();
}

export function consumeArtifactDeepLink(
  search: string | null | undefined = typeof location !== "undefined" ? location.search : "",
): void {
  const link = parseArtifactDeepLink(search);
  if (!link) return;
  void applyArtifactDeepLink(link);
}

export async function copyArtifactDeepLink(a: ArtifactRow): Promise<string> {
  const href = artifactDeepLinkHref(a.id, a._exactVersion ? a.version_id : a.version_id);
  const clip = hostWindow().navigator as unknown as { clipboard?: { writeText?: (s: string) => Promise<void> } };
  const write = clip && clip.clipboard && clip.clipboard.writeText;
  if (isReady(write)) await write(href);
  return href;
}
