/** Project / session / folder REST walks. app.js:6766, 6964-7086. */

import { t } from "../../i18n";
import {
  _folderCollapsed,
  _foldersFor,
  _projectsLoadingMore,
  _sessionScope,
  _sessionsLoadingMore,
  _titleName,
  currentId,
  folders,
  project,
  projects,
  projectsHasMore,
  projectsLoadError,
  projectsNextCursor,
  projectsQuery,
  projectsTotal,
  sessionPages,
  sessions,
  sessionsHasMore,
} from "../../stores/session";
import { api, apiErrorText } from "./api";
import { binds } from "./binds";
import { ensureActivateKeys, hint, openMenu } from "./chrome";
import { $, el, setTitle } from "./dom";
import { icon, iconEl } from "./icon";
import {
  DATE_BUCKET_KEYS,
  SESSION_MAX_PAGES,
  SESSION_PAGE_SIZE,
  absorbSessionPage,
  canLoadMoreSessions,
  dateBucketId,
  emptySessionWalk,
  sessionWalkBudget,
  sessionsInProject,
  sortSessionsByUpdatedAt,
  ungroupedSessions,
  type SessionLike,
} from "./paging";

export const PROJECT_PAGE_SIZE = 100;
export const PROJECT_Q_MAX = 128;

export type ProjectLike = {
  project_id?: string;
  id?: string;
  name?: string;
  conversation_count?: number;
  last_active_at?: string;
  updated_at?: string;
  running_count?: number;
};

export type ProjectDashView =
  | { kind: "error" }
  | { kind: "empty" }
  | { kind: "no-match" }
  | { kind: "list"; showMore: boolean; loadingMore: boolean };

export function normalizeProjectQuery(raw: string): string {
  return Array.from(raw.trim())
    .slice(0, PROJECT_Q_MAX)
    .join("");
}

export function projectListQuery(opts: {
  q?: string;
  cursor?: string | null;
  limit?: number;
}): string {
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit ?? PROJECT_PAGE_SIZE));
  const q = normalizeProjectQuery(opts.q || "");
  if (q) params.set("q", q);
  if (opts.cursor) params.set("cursor", opts.cursor);
  return `/projects?${params.toString()}`;
}

export function mergeProjectPage(
  existing: ProjectLike[],
  incoming: ProjectLike[],
  mode: "replace" | "append",
): ProjectLike[] {
  const seen = new Set<string>();
  const out: ProjectLike[] = [];
  const source = mode === "append" ? existing.concat(incoming) : incoming;
  for (const row of source) {
    const id = String(row.project_id || row.id || "");
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push(row);
  }
  return out;
}

export function canLoadMoreProjects(opts: {
  loadingMore: boolean;
  hasMore: boolean;
  cursor: string | null;
}): boolean {
  return !opts.loadingMore && opts.hasMore && !!opts.cursor;
}

export function projectDashView(opts: {
  error: boolean;
  count: number;
  query: string;
  hasMore: boolean;
  loadingMore: boolean;
}): ProjectDashView {
  if (opts.error) return { kind: "error" };
  if (!opts.count) return { kind: opts.query.trim() ? "no-match" : "empty" };
  return {
    kind: "list",
    showMore: opts.hasMore,
    loadingMore: opts.loadingMore,
  };
}

let _projectsLoadGen = 0;

export async function loadProjects(opts?: { append?: boolean }): Promise<void> {
  const append = !!opts?.append;
  if (
    append &&
    !canLoadMoreProjects({
      loadingMore: !!_projectsLoadingMore.value,
      hasMore: !!projectsHasMore.value,
      cursor: projectsNextCursor.value,
    })
  ) {
    return;
  }
  const gen = ++_projectsLoadGen;
  const q = normalizeProjectQuery(String(projectsQuery.value || ""));
  const cursor = append ? projectsNextCursor.value : null;
  const path = projectListQuery({ q, cursor });
  try {
    if (append) _projectsLoadingMore.value = true;
    else projectsLoadError.value = false;
    const d = (await api(path)) as {
      projects?: ProjectLike[];
      next_cursor?: string | null;
      has_more?: boolean;
      total?: number;
    } | null;
    if (gen !== _projectsLoadGen) return;
    const incoming = (d && d.projects) || [];
    projects.value = mergeProjectPage(
      (projects.value as ProjectLike[]) || [],
      incoming,
      append ? "append" : "replace",
    );
    projectsHasMore.value = !!(d && d.has_more);
    projectsNextCursor.value = (d && d.next_cursor) || null;
    projectsTotal.value =
      typeof d?.total === "number" ? d.total : (projects.value as ProjectLike[]).length;
    projectsLoadError.value = false;
  } catch {
    if (gen !== _projectsLoadGen) return;
    projectsLoadError.value = true;
    if (!append) {
      projects.value = [];
      projectsHasMore.value = false;
      projectsNextCursor.value = null;
      projectsTotal.value = 0;
    }
  } finally {
    if (gen === _projectsLoadGen) _projectsLoadingMore.value = false;
  }
}

export function invalidateFolders(): void {
  _foldersFor.value = null;
}

export async function loadFolders(): Promise<void> {
  const pid = project.value;
  if (!pid) {
    folders.value = [];
    _foldersFor.value = null;
    return;
  }
  if (_foldersFor.value === pid && folders.value) return;
  try {
    const d = (await api(`/projects/${pid}/folders`)) as { folders?: unknown[] } | null;
    folders.value = (d && d.folders) || [];
    _foldersFor.value = pid;
  } catch {
    folders.value = [];
  }
}

export async function loadSessions(): Promise<void> {
  const scope = project.value ? `&project_id=${encodeURIComponent(project.value)}` : "";
  if (_sessionScope.value !== (project.value || "")) {
    _sessionScope.value = project.value || "";
    sessionPages.value = 1;
  }
  const want = sessionWalkBudget(sessionPages.value || 1);
  const state = emptySessionWalk();
  let cursor: string | null = null;
  try {
    while (state.walked < want) {
      const f = (await api(
        `/frames?limit=${SESSION_PAGE_SIZE}${scope}` +
          (cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""),
      )) as {
        frames?: SessionLike[];
        has_more?: boolean;
        next_cursor?: string | null;
      } | null;
      const step = absorbSessionPage(state, f);
      if (step.stop) break;
      cursor = step.cursor;
    }
    sessions.value = state.rows;
    sessionPages.value = Math.max(1, state.walked);
    sessionsHasMore.value = state.hasMore;
  } catch {
    sessions.value = [];
    sessionPages.value = 1;
    sessionsHasMore.value = false;
  }
  await loadFolders();
  renderSessions();
  syncCurrentTitle();
  const dash = $("#dashboard");
  if (dash && !dash.classList.contains("hidden")) binds.loadDashboard();
}

export async function loadMoreSessions(): Promise<void> {
  if (
    !canLoadMoreSessions({
      loadingMore: !!_sessionsLoadingMore.value,
      hasMore: !!sessionsHasMore.value,
      sessionPages: sessionPages.value || 1,
    })
  ) {
    return;
  }
  _sessionsLoadingMore.value = true;
  sessionPages.value = (sessionPages.value || 1) + 1;
  renderSessions();
  try {
    await loadSessions();
  } finally {
    _sessionsLoadingMore.value = false;
    renderSessions();
  }
}

export function syncCurrentTitle(): void {
  if (!currentId.value) return;
  const rows = sessions.value as SessionLike[];
  const f = rows.find((x) => x.id === currentId.value);
  if (!f) return;
  const ct = $("#conv-title");
  if (ct && document.activeElement === ct) return;
  const name = f.name || f.task_summary || t("conv.title.default");
  if (name !== _titleName.value) {
    _titleName.value = name;
    setTitle(name);
  }
}

export function sessionRow(f: SessionLike): HTMLElement {
  const d = el(
    "div",
    "session" + (f.id === currentId.value ? " active" : "") + (f.running ? " running" : ""),
  );
  d.appendChild(el("div", "s-dot"));
  d.appendChild(el("div", "s-name", f.name || f.task_summary || t("session.untitled")));
  if (f.running) {
    const b = el("span", "s-badge run", t("dash.badge.running"));
    b.title = t("session.badge.runningTip");
    d.appendChild(b);
  } else if (f.kernel_alive) {
    const b = el("span", "s-badge live");
    b.title = t("session.badge.liveTip");
    d.appendChild(b);
  }
  const menu = el("button", "s-menu");
  menu.type = "button";
  menu.appendChild(iconEl("more-horizontal", 16));
  menu.title = t("session.menu.tip");
  menu.onclick = (e) => {
    e.stopPropagation();
    if (f.id) import("./actions").then((mod) => mod.sessionMenu(menu, f.id as string));
  };
  d.appendChild(menu);
  d.setAttribute("role", "button");
  d.tabIndex = 0;
  d.setAttribute("aria-current", f.id === currentId.value ? "page" : "false");
  const open = () => {
    if (f.id) void binds.openConversation(f.id, f.project_id);
  };
  d.onkeydown = (e) => {
    if (e.target === d && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      open();
    }
  };
  d.onclick = open;
  return d;
}

export function renderSessions(): void {
  const list = $("#session-list");
  if (!list) return;
  list.innerHTML = "";
  const frag = document.createDocumentFragment();
  let ss = sessions.value as SessionLike[];
  if (project.value) ss = sessionsInProject(ss, project.value);
  ss = sortSessionsByUpdatedAt(ss);
  const folderRows = (folders.value || []) as Array<{ folder_id: string; name: string }>;
  if (!ss.length && !folderRows.length) {
    list.appendChild(el("div", "side-label", t("session.empty.label")));
    return;
  }
  if (!_folderCollapsed.value || typeof _folderCollapsed.value !== "object") {
    _folderCollapsed.value = Object.create(null) as Record<string, unknown>;
  }
  const collapsedMap = _folderCollapsed.value as Record<string, unknown>;
  folderRows.forEach((fold) => {
    const inFold = ss.filter((f) => f.folder_id === fold.folder_id);
    const head = el("div", "folder-head");
    const collapsed = collapsedMap[fold.folder_id];
    const chev = el("span", "folder-chev");
    chev.innerHTML = icon(collapsed ? "chevron-right" : "chevron-down", 14);
    head.appendChild(chev);
    head.appendChild(iconEl("folder", 14));
    head.appendChild(el("span", "folder-name", fold.name));
    head.appendChild(el("span", "folder-count", String(inFold.length)));
    const menu = el("button", "s-menu");
    menu.type = "button";
    menu.appendChild(iconEl("more-horizontal", 15));
    menu.onclick = (e) => {
      e.stopPropagation();
      folderMenu(menu, fold);
    };
    head.appendChild(menu);
    head.onclick = () => {
      collapsedMap[fold.folder_id] = !collapsed;
      renderSessions();
    };
    ensureActivateKeys(head);
    frag.appendChild(head);
    if (!collapsed) {
      inFold.forEach((f) => {
        const r = sessionRow(f);
        r.style.paddingLeft = "20px";
        frag.appendChild(r);
      });
    }
  });
  const leftover = ungroupedSessions(ss, folderRows);
  let lastBucket: string | null = null;
  leftover.forEach((f) => {
    const b = t(DATE_BUCKET_KEYS[dateBucketId(f.updated_at, Date.now())]);
    if (b !== lastBucket) {
      lastBucket = b;
      frag.appendChild(el("div", "side-label", b));
    }
    frag.appendChild(sessionRow(f));
  });
  if (sessionsHasMore.value && (sessionPages.value || 1) >= SESSION_MAX_PAGES) {
    frag.appendChild(el("div", "side-label", t("session.loadMoreLimit")));
  } else if (sessionsHasMore.value) {
    const more = el(
      "button",
      "outline-btn small",
      _sessionsLoadingMore.value ? t("common.loading") : t("session.loadMore"),
    );
    more.id = "session-more";
    (more as HTMLButtonElement).disabled = !!_sessionsLoadingMore.value;
    more.style.margin = "10px 8px";
    more.onclick = () => {
      void loadMoreSessions();
    };
    frag.appendChild(more);
  }
  list.appendChild(frag);
}

export async function newFolder(): Promise<void> {
  const name = prompt(t("folder.new.prompt"));
  if (!name || !project.value) return;
  try {
    await api(`/projects/${project.value}/folders`, {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    invalidateFolders();
    await loadFolders();
    await loadSessions();
  } catch (e) {
    hint(t("folder.create.failed", apiErrorText(e)), true);
  }
}

function folderMenu(anchor: HTMLElement, fold: { folder_id: string; name: string }): void {
  openMenu(anchor, [
    {
      label: t("folder.menu.rename"),
      icon: "pencil",
      onClick: async () => {
        const n = prompt(t("folder.rename.prompt"), fold.name);
        if (!n) return;
        try {
          await api(`/folders/${fold.folder_id}`, {
            method: "PATCH",
            body: JSON.stringify({ name: n }),
          });
          invalidateFolders();
          await loadFolders();
          await loadSessions();
        } catch {
          /* ignore */
        }
      },
    },
    {
      label: t("folder.menu.delete"),
      icon: "trash-2",
      danger: true,
      onClick: async () => {
        if (!confirm(t("folder.delete.confirm", fold.name))) return;
        try {
          await api(`/folders/${fold.folder_id}`, { method: "DELETE" });
          invalidateFolders();
          await loadFolders();
          await loadSessions();
        } catch {
          /* ignore */
        }
      },
    },
  ]);
}

export async function assignFolder(fid: string, folder_id: string | null): Promise<void> {
  try {
    await api(`/frames/${fid}/folder`, { method: "POST", body: JSON.stringify({ folder_id }) });
    await loadSessions();
    hint(folder_id ? t("folder.assigned.in") : t("folder.assigned.out"));
  } catch (e) {
    hint(t("folder.move.failed", apiErrorText(e)), true);
  }
}

binds.renderSessions = renderSessions;
