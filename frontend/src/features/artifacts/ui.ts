import {
  dockArtifact,
  filesScope,
} from "../../stores/artifacts";
import { currentId, sessions } from "../../stores/session";
import { activeTab, dock, openTabs, provMode } from "../../stores/ui";
import { api, apiErrorText, bytes, callWindow, el, icon, translate } from "./api";
import { artifactTabKey } from "./cache";
import { filesT } from "./copy";
import {
  parseArtifactDeepLink,
  rememberViewerVersion,
  resolveArtifactVersion,
  versionResolveMessage,
} from "./deeplink";
import { labTabLabel, mountLab } from "../lab";
import { artifactKind, browseFiles, currentFilesFilter, filesGridArtifacts, filesGridPending, filesListingIsCurrent, filesReadFailed, visibleArtifacts } from "./files-index";
import { loadArtifacts, loadProjectArtifacts } from "./load";
import { filesIndexError, filesIndexItems, viewerVersionState } from "./state";
import { abortPreviewReads } from "./api";
import { pruneThumbReads, tileThumb, tileThumbBig } from "./thumbs";
import type { ArtifactDeepLink, ArtifactRow, VersionResolve } from "./types";

export function addOpenTab(a: ArtifactRow): void {
  const tabs = (openTabs.value as ArtifactRow[]) || [];
  const existing = tabs.find((x) => x && artifactTabKey(x) === artifactTabKey(a));
  if (!existing) openTabs.value = [...tabs, a];
  else {
    // A bare deep link can precede a real listing row for the same exact tab.
    // Enrich stable ownership without importing another head's producer/data.
    if (a.root_frame_id != null) existing.root_frame_id = a.root_frame_id;
    if (a.project_id != null) existing.project_id = a.project_id;
  }
}

export function closeTab(id: string): void {
  const tabs = ((openTabs.value as ArtifactRow[]) || []).filter((x) => x && artifactTabKey(x) !== id);
  openTabs.value = tabs;
  if (activeTab.value === id) {
    const last = tabs[tabs.length - 1];
    if (last) {
      dockArtifact.value = last;
      provMode.value = false;
      setActiveTab(artifactTabKey(last));
    } else setActiveTab("notebook");
  } else renderDockTabs(); // app.js:2756: a background tab must still leave the bar
}

function artIcon(a: ArtifactRow): string {
  const nm = String(a.filename || "").toLowerCase();
  const ct = String(a.content_type || "");
  if (ct.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg)$/i.test(nm)) return "file";
  if (/\.(pdb|cif|mol|mol2|sdf|xyz)$/i.test(nm)) return "atom";
  if (/csv|json|tsv/.test(ct) || /\.(csv|json|tsv)$/i.test(nm)) return "table";
  return "file";
}

function tabBtn(kind: "div" | "button", iconName: string, label: string): HTMLElement {
  const node = el(kind, "dock-tab");
  const ic = el("span", "ic");
  ic.innerHTML = icon(iconName, 14);
  node.appendChild(ic);
  node.appendChild(el("span", "t-name", label));
  return node;
}

export function renderDockTabs(): void {
  if (typeof document === "undefined") return;
  const bar = document.getElementById("dock-tabs");
  if (!bar) return;
  bar.innerHTML = "";
  ((openTabs.value as ArtifactRow[]) || []).forEach((a) => {
    const tab = tabBtn("div", artIcon(a), (a.filename || "artifact") + (a._exactVersion ? " · " + a.version_id : ""));
    if (activeTab.value === artifactTabKey(a)) tab.classList.add("active");
    const close = el("span", "t-close");
    close.innerHTML = icon("x", 14);
    close.title = translate("common.close");
    close.onclick = (event) => {
      event.stopPropagation();
      closeTab(artifactTabKey(a));
    };
    tab.appendChild(close);
    tab.onclick = () => {
      dockArtifact.value = a;
      provMode.value = false;
      setActiveTab(artifactTabKey(a));
    };
    bar.appendChild(tab);
  });
  const notebook = tabBtn("button", "notebook", "Notebook");
  if (activeTab.value === "notebook") notebook.classList.add("active");
  notebook.onclick = () => setActiveTab("notebook");
  bar.appendChild(notebook);
  // The OpenMHS Lab sits beside the Notebook: devices, live receipts, results, evidence.
  const lab = tabBtn("button", "server", labTabLabel());
  if (lab.dataset) lab.dataset.dockTab = "lab";
  if (activeTab.value === "lab") lab.classList.add("active");
  lab.onclick = () => setActiveTab("lab");
  bar.appendChild(lab);
  const timeline = tabBtn("button", "clock", translate("dock.tab.timeline"));
  if (activeTab.value === "timeline") timeline.classList.add("active");
  timeline.onclick = () => setActiveTab("timeline");
  bar.appendChild(timeline);
  // ART-15: Files stays a tab once the dock has shown it, so opening a file
  // from the list no longer makes the way back disappear.
  if (activeTab.value === "files") filesTabShown = true;
  if (filesTabShown) {
    const files = tabBtn("button", "files", "Files");
    if (activeTab.value === "files") files.classList.add("active");
    files.onclick = () => setActiveTab("files");
    bar.appendChild(files);
  }
  // Many open files overflow the strip; the active tab is scrolled into view
  // instead of being pushed off the right edge (ART-15).
  const active = typeof bar.querySelector === "function" ? bar.querySelector(".dock-tab.active") : null;
  if (active && typeof (active as HTMLElement).scrollIntoView === "function") {
    (active as HTMLElement).scrollIntoView({ block: "nearest", inline: "nearest" });
  }
}

/** The Files tab joins the strip the first time the Files pane is shown. */
let filesTabShown = false;

export function showDockPane(pane: string): void {
  if (typeof document === "undefined") return;
  ["viewer", "notebook", "lab", "timeline", "files"].forEach((p) => {
    const n = document.getElementById("dock-" + p);
    if (n) n.classList.toggle("hidden", p !== pane);
  });
  // Leaving Files drops every tile read no mounted card still uses; leaving the
  // Viewer aborts the preview read that was only for it.
  if (pane !== "files") pruneThumbReads();
  if (pane !== "viewer") abortPreviewReads(viewerEl());
}

function dockEl(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  return document.getElementById("rightdock");
}

function viewerEl(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  return document.getElementById("dock-viewer");
}

export function dockOpen(): void {
  const d = dock.value as { open: boolean; tab: string };
  dock.value = { ...d, open: true, tab: d.tab };
  dockEl()?.classList.remove("collapsed");
}

export function dockClose(): void {
  // Closing the dock leaves both preview kinds: cancel the artifact read (and
  // release its blob URLs, a notebook's images included) and the tile reads no
  // card shows any more.
  abortPreviewReads(viewerEl());
  pruneThumbReads();
  const d = dock.value as { open: boolean; tab: string };
  dock.value = { ...d, open: false, tab: d.tab };
  dockEl()?.classList.add("collapsed");
}

export function dockToggle(): void {
  const d = dock.value as { open: boolean; tab: string };
  if (d.open) dockClose();
  else setActiveTab(activeTab.value || "notebook");
}

export function setActiveTab(tab: string, selectArtifact = true): void {
  viewerRequest += 1;
  activeTab.value = tab;
  const selected = (openTabs.value as ArtifactRow[]).find((row) => artifactTabKey(row) === tab);
  if (selected && selectArtifact) {
    dockArtifact.value = selected;
    rememberViewerVersion(viewerVersionFor(selected));
  }
  dockOpen();
  renderDockTabs();
  const pane =
    tab === "notebook"
      ? "notebook"
      : tab === "lab"
        ? "lab"
        : tab === "timeline"
          ? "timeline"
          : tab === "files"
            ? "files"
            : "viewer";
  if (pane !== "viewer") abortPreviewReads(viewerEl());
  showDockPane(pane);
  if (tab === "notebook") callWindow("renderNotebook");
  else if (tab === "lab") mountLab();
  else if (tab === "timeline") {
    callWindow("loadWorkbenchState", currentId.value);
    callWindow("renderActionTimeline");
  } else if (tab === "files") {
    void (async () => {
      if (filesScope.value === "project") await loadProjectArtifacts();
      else if (currentId.value) await loadArtifacts(currentId.value);
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
  // Repaint: reads whose tiles were just removed have no attached consumer.
  pruneThumbReads();
  const arts = visibleArtifacts();
  if (!arts.length) return;
  const mkTile = (a: ArtifactRow) => {
    const tile = el("div", "tile");
    tile.appendChild(tileThumb(a));
    const fn = el("div", "tfn", a.filename || "artifact");
    if ((a.priority || 0) > 0) fn.textContent = "⭐ " + fn.textContent;
    tile.appendChild(fn);
    // Strip rows are `_artifact_json` heads whose `version_id` is the current
    // latest; like the Files grid they must open the latest tab, not pin the
    // head-at-click (which the cache would then refuse to advance).
    tile.onclick = () => presentViewer(a);
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

/** A failed session read says so and offers Retry; it is never "no files". */
function paintReadError(list: HTMLElement, confirmed: boolean): void {
  const note = el("div", "files-version-error files-read-error", filesT(confirmed ? "files.read.stale" : "files.read.failed"));
  note.setAttribute("role", "alert");
  const retry = el("button", "outline-btn small", translate("common.retry"));
  retry.onclick = () => {
    if (currentId.value) void loadArtifacts(currentId.value);
  };
  note.appendChild(retry);
  list.appendChild(note);
}

export function renderFilesGrid(): void {
  if (typeof document === "undefined") return;
  paintFilesGrid();
}

function paintFilesGrid(): void {
  const list = document.getElementById("results-list");
  const count = document.getElementById("results-count");
  if (!list) return;
  const arts = filesGridArtifacts();
  const readFailed = filesReadFailed();
  // No cards yet is not zero files while the read that decides it is pending.
  const pending = !arts.length && !readFailed && filesGridPending();
  list.innerHTML = "";
  // The previous grid's cards are detached now: drop the tile reads no other
  // view still shows before painting their replacements.
  pruneThumbReads();
  if (count) count.textContent = pending ? "…" : String(arts.length);
  paintVersionBanner(list);
  if (readFailed) paintReadError(list, arts.length > 0);
  const indexErr = filesListingIsCurrent() ? filesIndexError.value : null;
  if (indexErr && filesIndexItems.value.length === 0 && !arts.length) {
    list.appendChild(el("div", "files-empty", indexErr));
    return;
  }
  if (!arts.length) {
    if (readFailed) return;
    if (pending) {
      list.appendChild(el("div", "files-empty", translate("common.loading")));
      return;
    }
    const filter = currentFilesFilter();
    const msg = filter.q || filter.contentType || filter.origin || filter.kind
      ? filesT("files.noMatches")
      : filesScope.value === "project" ? translate("files.emptyProject") : translate("files.empty");
    list.appendChild(el("div", "files-empty", msg));
    return;
  }
  arts.forEach((a) => {
    const hidden = (a.priority || 0) < 0;
    const card = el("div", hidden ? "art art-hidden" : "art");
    card.dataset.artifactId = a.id;
    card.appendChild(tileThumbBig(a));
    card.appendChild(
      el("div", "a-name", ((a.priority || 0) > 0 ? "⭐ " : "") + (a.filename || "artifact")),
    );
    // ART-12: a kind a person reads ("表格"), not a MIME type that overflows.
    const meta = el("div", "a-meta", filesT("files.filter.kind." + artifactKind(a)) + " · " + bytes(a.size_bytes));
    meta.title = String(a.content_type || "");
    card.appendChild(meta);
    if (hidden) {
      // Hidden used to be one-way: listed again only through "show hidden".
      const row = el("div", "a-hidden");
      row.appendChild(el("span", "a-hidden-badge", filesT("files.hiddenBadge")));
      const unhide = el("button", "outline-btn small", filesT("artifact.unhide"));
      unhide.setAttribute("type", "button");
      unhide.onclick = (event: Event) => {
        event.stopPropagation();
        void unhideArtifact(a);
      };
      row.appendChild(unhide);
      card.appendChild(row);
    }
    if (filesScope.value === "project") {
      card.appendChild(el("div", "a-src", translate("files.fromSession", sessionNameFor(a.root_frame_id))));
    }
    card.onclick = () => presentViewer(a);
    list.appendChild(card);
  });
}

/** ART-12: put a hidden file back in the list. */
export async function unhideArtifact(a: ArtifactRow): Promise<void> {
  try {
    await api(`/artifacts/${encodeURIComponent(a.id)}/priority`, {
      method: "POST",
      body: JSON.stringify({ priority: 0 }),
    });
    a.priority = 0;
    callWindow("hint", filesT("artifact.unhidden", a.filename || ""));
    if (currentId.value) await loadArtifacts(currentId.value);
    if (filesScope.value === "project") await browseFiles({ refresh: true });
    renderFilesGrid();
  } catch (error) {
    callWindow("hint", filesT("artifact.unhideFailed", apiErrorText(error)), true);
  }
}

let renderViewerImpl: (() => void) | null = null;

/**
 * The Viewer is the F-18 island (islands/viewer.ts), installed by
 * `bootIslands` at startup. It imports this module, so it is injected here
 * rather than imported. The F-17 skeleton that stood in before it was never
 * reached once main.tsx booted both lanes, and has been removed.
 */
export function setRenderViewerImpl(fn: (() => void) | null): void {
  renderViewerImpl = fn;
}

export function renderViewer(): void {
  // A repaint/replacement owns the Viewer: abort the previous render's read
  // before the new body is built (the new render starts its own controller).
  abortPreviewReads(viewerEl());
  if (renderViewerImpl) renderViewerImpl();
}

let viewerRequest = 0;

/** The viewer state a row already carries: pinned rows are exact, others latest. */
function viewerVersionFor(a: ArtifactRow): VersionResolve {
  return a._exactVersion
    ? { status: "exact", artifact: a, versionId: String(a.version_id || "") }
    : { status: "latest", artifact: a, versionId: a.version_id || a.latest_version_id || null };
}

export function presentViewer(a: ArtifactRow): void {
  viewerRequest += 1;
  rememberViewerVersion(viewerVersionFor(a));
  dockArtifact.value = a;
  provMode.value = false;
  addOpenTab(a);
  setActiveTab(artifactTabKey(a));
}

/**
 * app.js:9437. Artifact click → dock Viewer tab.
 *
 * A provided `version_id` that is not already an exact pin is resolved
 * immutably: missing exact → stale/not-found, never silent latest.
 * Palette ⌘K hits forward `version_id` as-is (F-20); this is the pin.
 * Files-grid cards call `presentViewer` instead — those rows carry the
 * current head `version_id` and must not wait on `/versions`.
 */
export function openViewer(a: ArtifactRow): void | Promise<void> {
  if (a.version_id && !a._exactVersion) {
    return applyArtifactDeepLink({
      artifactId: a.id,
      versionId: String(a.version_id),
    }, a);
  }
  presentViewer(a);
}

export async function applyArtifactDeepLink(link: ArtifactDeepLink, source?: Pick<ArtifactRow, "id" | "root_frame_id" | "project_id">): Promise<void> {
  // The versions endpoint does not carry owning-session metadata. Keep only
  // the stable ownership supplied by the selected real row, never its head's
  // producing Cell or version-specific metadata.
  const ownership = source?.id === link.artifactId ? { root_frame_id: source.root_frame_id, project_id: source.project_id } : {};
  const request = ++viewerRequest;
  let result: VersionResolve;
  try {
    result = await resolveArtifactVersion(link);
  } catch {
    result = {
      status: "not-found" as const,
      artifactId: link.artifactId,
      versionId: link.versionId,
    };
  }
  if (request !== viewerRequest) return;
  rememberViewerVersion(result);
  if (result.status === "exact" || result.status === "latest") {
    presentViewer({ ...result.artifact, ...ownership });
    return;
  }
  // stale / not-found: show the banner and do not substitute latest.
  dockArtifact.value = { id: link.artifactId, artifact_id: link.artifactId };
  setActiveTab(artifactTabKey({ id: link.artifactId, version_id: link.versionId, _exactVersion: !!link.versionId }), false);
  renderViewer();
}

export function consumeArtifactDeepLink(
  search: string | null | undefined = typeof location !== "undefined" ? location.search : "",
): void | Promise<void> {
  const link = parseArtifactDeepLink(search);
  if (!link) return;
  return applyArtifactDeepLink(link);
}
