import { beginNavigation } from "./navigation";
/** Home dashboard. app.js:6616-6764, 2685. */

import { LANG, t } from "../../i18n";
import { _openGen, currentId, projects, projectsQuery } from "../../stores/session";
import { _dashPoll } from "../../stores/ui";
import { unsub } from "../ws/connect";
import { api, apiErrorText } from "./api";
import { binds } from "./binds";
import { ensureActivateKeys, reportFailure } from "./chrome";
import { sessionCopy } from "./copy";
import { $, ago, el, navURL, syncMobileChrome } from "./dom";
import {
  canLoadMoreProjects,
  dashProjectList,
  loadProjects,
  normalizeProjectQuery,
  projectDashView,
  type ProjectLike,
} from "./load";
import {
  annotateRunningCounts,
  filterRootFrames,
  recentDashboardSessions,
  runningDashboardFrames,
  type SessionLike,
} from "./paging";

let exampleTimer = 0;
let visBound = false;

function projectCopy(kind: "more" | "retry" | "no-match" | "error"): string {
  if (LANG === "en") {
    if (kind === "more") return "Load more";
    if (kind === "retry") return "Retry";
    if (kind === "no-match") return "No matching projects";
    return "Could not load projects.";
  }
  if (kind === "more") return "加载更多";
  if (kind === "retry") return "重试";
  if (kind === "no-match") return "没有匹配的项目";
  return "无法加载项目。";
}

/** Keystroke debounce for the project search; Enter flushes it. */
export const PROJECT_SEARCH_DEBOUNCE_MS = 150;
let searchTimer = 0;

function searchProjectsNow(): void {
  if (searchTimer) {
    clearTimeout(searchTimer);
    searchTimer = 0;
  }
  void loadProjects({ q: String(projectsQuery.value || "") }).then(() =>
    renderDashProjects(),
  );
}

export function bindProjectSearch(): void {
  const input = $("#dash-project-search") as HTMLInputElement | null;
  if (!input || input.dataset.bound === "1") return;
  input.dataset.bound = "1";
  input.addEventListener("input", () => {
    projectsQuery.value = input.value;
    // One request per pause, not one per keystroke: each request runs the
    // activity aggregate plus a count on the daemon's single SQLite
    // connection, and the generation counter only discards stale replies
    // client-side.
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = window.setTimeout(searchProjectsNow, PROJECT_SEARCH_DEBOUNCE_MS);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      input.value = "";
      projectsQuery.value = "";
      searchProjectsNow();
    }
    if (event.key === "Enter") {
      event.preventDefault();
      searchProjectsNow();
    }
  });
}

export async function loadMoreProjects(): Promise<void> {
  const list = dashProjectList();
  if (
    !canLoadMoreProjects({
      loadingMore: !!list.loadingMore.value || list.replaceInFlight,
      hasMore: !!list.hasMore.value,
      cursor: list.nextCursor.value,
    })
  ) {
    return;
  }
  await loadProjects({ append: true });
  renderDashProjects();
}

/**
 * The example CTA that may run `exampleTimer`. A CTA replaced by a repaint, or
 * stopped with the dashboard, loses it: its late replies can neither start an
 * interval nothing would stop nor reload a dashboard that is not on screen.
 */
let exampleOwner: object | null = null;

function stopExamplePoll(): void {
  exampleOwner = null;
  if (exampleTimer) {
    clearInterval(exampleTimer);
    exampleTimer = 0;
  }
}

export function paintDashSkeleton(): void {
  const skel = (n: number) => {
    const frag = document.createDocumentFragment();
    for (let i = 0; i < n; i++) {
      const row = el("div", "d-row skeleton-row");
      const main = el("div", "d-main");
      main.appendChild(el("div", "d-name", "·"));
      main.appendChild(el("div", "d-sub", "·"));
      row.appendChild(main);
      row.appendChild(el("div", "d-meta", "·"));
      frag.appendChild(row);
    }
    return frag;
  };
  const pc = $("#dash-projects");
  if (pc && !pc.childElementCount) pc.appendChild(skel(3));
  const sc = $("#dash-sessions");
  if (sc && !sc.childElementCount) sc.appendChild(skel(4));
}

/** The root frames the dashboard last loaded; a search or Load-more repaint
 * annotates its fresh server rows (which carry no `running_count`) from
 * these, so the running badge survives a keystroke. */
let _dashFrames: SessionLike[] = [];
/**
 * The last `/frames` read failed. An unread list is not an empty one: the
 * lists keep the rows last confirmed and say so, instead of painting "no
 * sessions yet" and offering the example over sessions that exist.
 */
let dashFramesFailed = false;

export async function loadDashboard(): Promise<void> {
  bindProjectSearch();
  paintDashSkeleton();
  // The search box persists across dashboard visits; the card must match it.
  // While it holds a query the directory is read too: the Running and Recent
  // rows, and the attention cards, name their projects from it.
  const q = normalizeProjectQuery(String(projectsQuery.value || ""));
  await Promise.all([loadProjects({ q }), q ? loadProjects() : null]);
  try {
    const d = (await api("/frames?limit=50")) as { frames?: SessionLike[] };
    _dashFrames = filterRootFrames((d && d.frames) || []);
    dashFramesFailed = false;
  } catch {
    dashFramesFailed = true;
  }
  renderDashProjects();
  renderDashRunning(_dashFrames);
  renderDashRecent(_dashFrames);
}

export function renderDashProjects(): void {
  const pc = $("#dash-projects");
  if (!pc) return;
  pc.innerHTML = "";
  const shown = dashProjectList();
  const list = shown.rows.value as ProjectLike[];
  annotateRunningCounts(list, _dashFrames);
  const view = projectDashView({
    error: !!shown.loadError.value,
    count: list.length,
    query: String(projectsQuery.value || ""),
    hasMore: !!shown.hasMore.value,
    loadingMore: !!shown.loadingMore.value,
  });
  if (view.kind === "error") {
    const box = el("div", "dash-empty", projectCopy("error"));
    const retry = el("button", "outline-btn small", projectCopy("retry"));
    retry.type = "button";
    retry.id = "dash-projects-retry";
    // The same request the box would issue: a failed *filtered* load retried
    // without `q` painted the whole directory under a box still showing the
    // filter, and Load-more then walked the unfiltered set.
    retry.onclick = () => searchProjectsNow();
    pc.appendChild(box);
    pc.appendChild(retry);
    return;
  }
  if (view.kind === "empty") {
    pc.appendChild(el("div", "dash-empty", t("dash.projects.empty")));
    return;
  }
  if (view.kind === "no-match") {
    pc.appendChild(el("div", "dash-empty", projectCopy("no-match")));
    return;
  }
  list.forEach((p) => {
    const row = el("div", "d-row");
    const main = el("div", "d-main");
    main.appendChild(el("div", "d-name", p.name || t("dash.project.untitled")));
    if (/example/i.test(p.name || "")) main.appendChild(el("span", "d-tag", "Example"));
    if (p.running_count) {
      const b = el("span", "d-run");
      b.appendChild(el("span", "d-run-dot"));
      b.appendChild(el("span", null, String(p.running_count)));
      b.title = t("dash.project.runningCount", p.running_count);
      main.appendChild(b);
    }
    row.appendChild(main);
    const n = p.conversation_count || 0;
    row.appendChild(el("div", "d-meta", t(n === 1 ? "dash.meta.session" : "dash.meta.sessions", n)));
    row.appendChild(el("div", "d-meta", ago(p.last_active_at || p.updated_at)));
    const open = () => {
      const id = p.project_id || p.id;
      if (id) import("./projects").then((mod) => mod.openProject(id)).catch(reportFailure);
    };
    row.onclick = open;
    ensureActivateKeys(row);
    pc.appendChild(row);
  });
  if (view.showMore) {
    const more = el(
      "button",
      "outline-btn small",
      view.loadingMore ? t("common.loading") : projectCopy("more"),
    );
    more.type = "button";
    more.id = "dash-projects-more";
    (more as HTMLButtonElement).disabled = view.loadingMore;
    more.onclick = () => {
      void loadMoreProjects();
    };
    pc.appendChild(more);
  }
}

function exampleSeedCta(): HTMLElement {
  stopExamplePoll();
  const owner = {};
  exampleOwner = owner;
  const live = () => exampleOwner === owner;
  const box = el("div", "dash-example");
  const btn = el("button", "btn", t("dash.example.cta"));
  btn.type = "button";
  const note = el("div", "dash-example-hint", t("dash.example.hint"));
  box.appendChild(btn);
  box.appendChild(note);
  const startPoll = () => {
    if (!live()) return;
    if (exampleTimer) clearInterval(exampleTimer);
    exampleTimer = window.setInterval(poll, 1500) as unknown as number;
  };
  const paint = (st: { running?: boolean; error?: string; seeded?: boolean }) => {
    if (!live()) return;
    if (st.running) {
      btn.disabled = true;
      btn.textContent = t("dash.example.running");
    } else {
      btn.disabled = false;
      btn.textContent = t("dash.example.cta");
    }
    if (st.error) note.textContent = t("dash.example.failed") + st.error;
    if (st.seeded) {
      stopExamplePoll();
      void loadDashboard();
    }
  };
  const poll = () =>
    api("/example/session")
      .then((st) => paint(st as { running?: boolean; error?: string; seeded?: boolean }))
      .catch(() => {
        if (live()) stopExamplePoll();
      });
  btn.onclick = () => {
    btn.disabled = true;
    api("/example/session", { method: "POST", body: JSON.stringify({ confirm: true }) })
      .then((st) => {
        paint(st as { running?: boolean; error?: string; seeded?: boolean });
        startPoll();
      })
      .catch((e) => {
        btn.disabled = false;
        note.textContent = t("dash.example.failed") + apiErrorText(e);
      });
  };
  api("/example/session")
    .then((raw) => {
      if (!live()) return;
      const st = raw as { running?: boolean; error?: string; seeded?: boolean };
      if (st.seeded) box.remove();
      else paint(st);
      if (st.running) startPoll();
    })
    .catch(() => box.remove());
  return box;
}

export function renderDashRecent(frames: SessionLike[]): void {
  const recent = recentDashboardSessions(frames);
  const sc = $("#dash-sessions");
  if (!sc) return;
  sc.innerHTML = "";
  if (dashFramesFailed) {
    const notice = el("div", "dash-empty", sessionCopy("sessionsError"));
    notice.setAttribute("role", "alert");
    const retry = el("button", "outline-btn small", sessionCopy("retry"));
    retry.type = "button";
    retry.id = "dash-sessions-retry";
    retry.onclick = () => {
      void loadDashboard().catch(() => undefined);
    };
    sc.appendChild(notice);
    sc.appendChild(retry);
  } else if (!recent.length) {
    sc.appendChild(el("div", "dash-empty", t("dash.sessions.empty")));
    sc.appendChild(exampleSeedCta());
  }
  recent.forEach((f) => {
    const row = el("div", "d-row");
    row.appendChild(el("div", f.running ? "d-dot live" : "d-dot"));
    const main = el("div", "d-main");
    main.appendChild(el("div", "d-name", f.name || f.task_summary || t("session.untitled")));
    const pj = (projects.value as ProjectLike[]).find(
      (p) => (p.project_id || p.id) === f.project_id,
    );
    if (pj) main.appendChild(el("div", "d-sub", pj.name || ""));
    row.appendChild(main);
    if (f.running) {
      const b = el("span", "d-run");
      b.appendChild(el("span", "d-run-dot"));
      b.appendChild(el("span", null, t("dash.badge.running")));
      row.appendChild(b);
    } else row.appendChild(el("div", "d-meta", ago(f.updated_at)));
    const open = () => {
      if (f.id) void binds.openConversation(f.id, f.project_id);
    };
    row.onclick = open;
    ensureActivateKeys(row);
    sc.appendChild(row);
  });
}

export function renderDashRunning(frames: SessionLike[]): void {
  const running = runningDashboardFrames(frames);
  const cnt = $("#dash-running-count");
  if (cnt) {
    if (running.length) {
      cnt.textContent = t("dash.running.count", running.length);
      cnt.classList.remove("hidden");
    } else cnt.classList.add("hidden");
  }
  const sec = $("#dash-running");
  if (!sec) return;
  sec.innerHTML = "";
  if (!running.length) {
    sec.classList.add("hidden");
    return;
  }
  sec.classList.remove("hidden");
  running.forEach((f) => {
    const card = el("div", "run-card");
    const body = el("div", "run-body");
    body.appendChild(el("div", "run-title", f.name || f.task_summary || t("session.untitled")));
    const pj = (projects.value as ProjectLike[]).find(
      (p) => (p.project_id || p.id) === f.project_id,
    );
    const sub =
      f.task_summary && f.task_summary !== f.name ? f.task_summary : pj ? pj.name : "";
    if (sub) body.appendChild(el("div", "run-sub", sub));
    card.appendChild(body);
    const foot = el("div", "run-foot");
    const badge = el("span", "run-badge");
    badge.appendChild(el("span", "run-dot"));
    badge.appendChild(el("span", null, t("dash.badge.running")));
    foot.appendChild(badge);
    foot.appendChild(el("span", "run-when", t("dash.running.activeNow")));
    card.appendChild(foot);
    card.title = t("session.badge.runningTip");
    const open = () => {
      if (f.id) void binds.openConversation(f.id, f.project_id);
    };
    card.onclick = open;
    ensureActivateKeys(card);
    sec.appendChild(card);
  });
}

export async function refreshDashRunning(): Promise<void> {
  const dash = $("#dashboard");
  if (!dash || dash.classList.contains("hidden")) {
    stopDashPoll();
    return;
  }
  if (typeof document.hidden === "boolean" && document.hidden) return;
  let frames: SessionLike[] = [];
  try {
    const d = (await api("/frames?limit=50")) as { frames?: SessionLike[] };
    frames = filterRootFrames((d && d.frames) || []);
  } catch {
    return;
  }
  if ($("#dashboard")?.classList.contains("hidden")) return;
  // The project repaints annotate from these; leaving them at the last full
  // load would paint "1 running" beside a Running card this poll just
  // emptied -- a wrong badge where the old code merely had none.
  _dashFrames = frames;
  renderDashRunning(frames);
  if (dashFramesFailed) {
    // This poll is the retry the failed read was waiting for.
    dashFramesFailed = false;
    renderDashRecent(frames);
  }
}

export function stopDashPoll(): void {
  if (_dashPoll.value) {
    clearInterval(_dashPoll.value as ReturnType<typeof setInterval>);
    _dashPoll.value = null;
  }
  stopExamplePoll();
}

export function startDashPoll(): void {
  stopDashPoll();
  _dashPoll.value = setInterval(() => {
    void refreshDashRunning();
  }, 4000);
  if (!visBound && typeof document !== "undefined") {
    visBound = true;
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && !$("#dashboard")?.classList.contains("hidden")) {
        void refreshDashRunning();
      }
    });
  }
}

export function showDashboard(): void {
  navURL("/");
  // app.js:2695 bumps here too. Going Home was the one navigation that left
  // every parked continuation believing it still owned the view: an
  // openConversation mid-load would paint into the hidden workspace and arm a
  // resume watchdog that then exits stale on its first tick, stranding the
  // composer disabled behind a spinner nothing clears.
  beginNavigation();
  $("#workspace")?.classList.add("hidden");
  $("#dashboard")?.classList.remove("hidden");
  // Leaving a conversation releases its subscription, as switching to another
  // one does. Kept subscribed, a running session's events went on advancing
  // its resume cursor with nothing showing them; reopening it from here then
  // read a history that moved under it (deferred, so no Stop, an unlocked
  // composer and no watchdog) and resubscribed past everything it had missed.
  const leaving = currentId.value;
  if (leaving) unsub(leaving);
  currentId.value = null;
  void loadDashboard();
  startDashPoll();
}

export function showWorkspace(): void {
  stopDashPoll();
  // A debounced search still pending must not fire after navigation: it
  // would supersede the workspace's unfiltered load and paint the filtered
  // page into the hidden card.
  if (searchTimer) {
    clearTimeout(searchTimer);
    searchTimer = 0;
  }
  $("#dashboard")?.classList.add("hidden");
  $("#workspace")?.classList.remove("hidden");
  const view = $("#conv-view");
  if (view) view.classList.remove("hidden");
  syncMobileChrome(false);
}

binds.loadDashboard = loadDashboard;
binds.startDashPoll = startDashPoll;
binds.stopDashPoll = stopDashPoll;
binds.renderDashProjects = renderDashProjects;
binds.showDashboard = showDashboard;
binds.showWorkspace = showWorkspace;
