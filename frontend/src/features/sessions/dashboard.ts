import { beginNavigation } from "./navigation";
/** Home dashboard. app.js:6616-6764, 2685. */

import { LANG, t } from "../../i18n";
import { _openGen, currentId, projects, projectsQuery } from "../../stores/session";
import { _dashPoll } from "../../stores/ui";
import { attentionCards } from "../attention/state";
import { unsub } from "../ws/connect";
import { api, apiErrorText } from "./api";
import { binds } from "./binds";
import { ensureActivateKeys, reportFailure } from "./chrome";
import { dashCopy, displayProjectName, sessionCopy } from "./copy";
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
import { openProject } from "./projects";

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
 * The example CTA whose first status read may start a run. A CTA replaced by a
 * repaint, or stopped with the dashboard, loses it: its late replies can
 * neither start an interval nothing would stop nor reload a dashboard that is
 * not on screen.
 */
let exampleOwner: object | null = null;
/** Bumped whenever the example poll stops; a reply from an older poll is dropped. */
let examplePollEpoch = 0;

type ExampleStatus = {
  running?: boolean;
  error?: string | null;
  seeded?: boolean;
  frame_id?: string | null;
  project_id?: string;
  result?: { cells?: number; failed?: number } | null;
};

/**
 * The example run this page started (or found running), until dismissed.
 * The CTA used to vanish the moment the session existed -- a second after the
 * click -- and nothing said when the six cells finished or that two failed.
 */
type ExampleRun = {
  phase: "running" | "ended" | "error";
  frameId: string;
  projectId: string;
  cells: number;
  failed: number | null;
  error: string;
  listed: boolean;
};
let exampleRun: ExampleRun | null = null;

function stopExamplePoll(): void {
  exampleOwner = null;
  examplePollEpoch += 1;
  if (exampleTimer) {
    clearInterval(exampleTimer);
    exampleTimer = 0;
  }
}

/** Fold one `/example/session` answer into the run on screen. */
function applyExampleStatus(st: ExampleStatus): void {
  const run = exampleRun;
  if (!run) return;
  const before = run.phase;
  if (st.frame_id) run.frameId = String(st.frame_id);
  if (st.project_id) run.projectId = String(st.project_id);
  if (!st.running) {
    stopExamplePoll();
    if (st.error) {
      run.phase = "error";
      run.error = String(st.error);
    } else {
      run.phase = "ended";
      const result = st.result;
      if (result && Number.isFinite(Number(result.cells))) {
        run.cells = Number(result.cells);
        run.failed = Math.max(0, Number(result.failed) || 0);
      }
    }
  }
  // The lists learn about the new session once, and again when it ends.
  if ((run.frameId && !run.listed) || before !== run.phase) {
    run.listed = !!run.frameId;
    void loadDashboard();
  } else {
    renderDashRecent(_dashFrames);
  }
}

function startExamplePoll(): void {
  if (exampleTimer) return;
  const epoch = examplePollEpoch;
  exampleTimer = window.setInterval(() => {
    api("/example/session")
      .then((st) => {
        if (epoch === examplePollEpoch) applyExampleStatus(st as ExampleStatus);
      })
      .catch(() => {
        if (epoch === examplePollEpoch) stopExamplePoll();
      });
  }, 1500) as unknown as number;
}

/** Tests: forget the example run this page followed. */
export function resetExampleRun(): void {
  stopExamplePoll();
  exampleRun = null;
}

function beginExampleRun(st: ExampleStatus): void {
  exampleRun = {
    phase: "running",
    frameId: "",
    projectId: "proj_example",
    cells: 0,
    failed: null,
    error: "",
    listed: false,
  };
  if (st.running) startExamplePoll();
  applyExampleStatus(st);
}

/** The example run's notice: running, finished, or how many cells did not complete. */
function exampleStatusBox(run: ExampleRun): HTMLElement {
  const box = el("div", "dash-example dash-example-status");
  box.setAttribute("role", "status");
  box.setAttribute("data-phase", run.phase);
  const text =
    run.phase === "running"
      ? dashCopy("dash.example.runningNote")
      : run.phase === "error"
        ? t("dash.example.failed") + run.error
        : run.failed == null
          ? dashCopy("dash.example.ended")
          : run.failed > 0
            ? dashCopy("dash.example.doneFailed", run.failed, run.cells)
            : dashCopy("dash.example.done", run.cells);
  box.appendChild(el("div", "dash-example-msg", text));
  const actions = el("div", "dash-example-actions");
  if (run.frameId) {
    const open = el("button", "solid-btn", dashCopy("dash.example.open"));
    open.type = "button";
    open.onclick = () => {
      void binds.openConversation(run.frameId, run.projectId);
    };
    actions.appendChild(open);
  }
  if (run.phase !== "running") {
    const dismiss = el("button", "outline-btn", dashCopy("dash.example.dismiss"));
    dismiss.type = "button";
    dismiss.onclick = () => {
      exampleRun = null;
      renderDashRecent(_dashFrames);
    };
    actions.appendChild(dismiss);
  }
  box.appendChild(actions);
  return box;
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
/** A `/frames` read has answered (either way) since the page loaded. */
let dashFramesRead = false;

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
  dashFramesRead = true;
  renderDashProjects();
  renderDashRunning(_dashFrames);
  renderDashRecent(_dashFrames);
}

/** Paint the lists again from what the last reads returned; a language switch needs no new reads. */
export function repaintDashboard(): void {
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
    main.appendChild(el("div", "d-name", displayProjectName(p) || t("dash.project.untitled")));
    if ((p.project_id || p.id) === "proj_example" || /example/i.test(p.name || ""))
      main.appendChild(el("span", "d-tag", dashCopy("dash.example.tag")));
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
      if (id) openProject(id).catch(reportFailure);
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
  // A styled primary button: it was an unstyled native one (`.btn`).
  const btn = el("button", "solid-btn", t("dash.example.cta"));
  btn.type = "button";
  const note = el("div", "dash-example-hint", t("dash.example.hint"));
  box.appendChild(btn);
  box.appendChild(note);
  btn.onclick = () => {
    btn.disabled = true;
    btn.textContent = t("dash.example.running");
    api("/example/session", { method: "POST", body: JSON.stringify({ confirm: true }) })
      .then((st) => {
        if (live()) beginExampleRun(st as ExampleStatus);
      })
      .catch((e) => {
        btn.disabled = false;
        btn.textContent = t("dash.example.cta");
        note.textContent = t("dash.example.failed") + apiErrorText(e);
      });
  };
  api("/example/session")
    .then((raw) => {
      if (!live()) return;
      const st = raw as ExampleStatus;
      // Started from another tab: follow it here too.
      if (st.running) beginExampleRun(st);
      else if (st.seeded) box.remove();
      else if (st.error) note.textContent = t("dash.example.failed") + st.error;
    })
    .catch(() => box.remove());
  return box;
}

/** Sessions "Needs attention" already shows: the home page lists each session once. */
function attentionFrameIds(): Set<string> {
  return new Set(
    (attentionCards.value || []).map((card) => card.frameId).filter((id): id is string => !!id),
  );
}

export function renderDashRecent(frames: SessionLike[]): void {
  const all = recentDashboardSessions(frames);
  const attention = attentionFrameIds();
  // One place per session: a running one has its Running card, one waiting
  // for a decision its "Needs attention" card. Recent repeated both.
  const recent = recentDashboardSessions(
    frames.filter((f) => !f.running && !(f.id && attention.has(f.id))),
  );
  const sc = $("#dash-sessions");
  if (!sc) return;
  sc.innerHTML = "";
  if (exampleRun) {
    sc.appendChild(exampleStatusBox(exampleRun));
    // Back on the dashboard while it still runs: keep following it.
    if (exampleRun.phase === "running") startExamplePoll();
  }
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
  } else if (!all.length) {
    // No session at all: the empty state and the example, unless the
    // example's own notice is already on screen.
    if (!exampleRun) {
      sc.appendChild(el("div", "dash-empty", t("dash.sessions.empty")));
      sc.appendChild(exampleSeedCta());
    }
  } else if (!recent.length) {
    // Every session is above (Running / Needs attention): not "no sessions yet".
    sc.appendChild(el("div", "dash-empty", dashCopy("dash.sessions.allAbove")));
  }
  recent.forEach((f) => {
    const row = el("div", "d-row");
    // A finished session and one whose last turn failed looked the same here.
    const ended = !f.running && (f.status === "failed" || f.status === "done") ? f.status : "";
    const dot = el("div", f.running ? "d-dot live" : ended ? "d-dot " + ended : "d-dot");
    if (ended) dot.title = dashCopy("dash.status." + ended);
    row.appendChild(dot);
    const main = el("div", "d-main");
    main.appendChild(el("div", "d-name", f.name || f.task_summary || t("session.untitled")));
    const pj = (projects.value as ProjectLike[]).find(
      (p) => (p.project_id || p.id) === f.project_id,
    );
    if (pj) main.appendChild(el("div", "d-sub", displayProjectName(pj)));
    row.appendChild(main);
    if (f.running) {
      const b = el("span", "d-run");
      b.appendChild(el("span", "d-run-dot"));
      b.appendChild(el("span", null, t("dash.badge.running")));
      row.appendChild(b);
    } else {
      if (ended === "failed") {
        row.appendChild(el("span", "d-status failed", dashCopy("dash.status.failed")));
      }
      row.appendChild(el("div", "d-meta", ago(f.updated_at)));
    }
    const open = () => {
      if (f.id) void binds.openConversation(f.id, f.project_id);
    };
    row.onclick = open;
    ensureActivateKeys(row);
    sc.appendChild(row);
  });
}

/**
 * Under a Running card's title: the project, and the task summary when it
 * says more than the title. A session's name is often made from its summary,
 * so "summary unless equal to the name" put the session's name on the card
 * twice.
 */
export function runCardSubtitle(f: SessionLike, project?: ProjectLike | null): string {
  const name = String(f.name || "").trim();
  const summary = String(f.task_summary || "").trim();
  const repeats =
    !summary || !name || summary === name || summary.startsWith(name) || name.startsWith(summary);
  return [displayProjectName(project), repeats ? "" : summary].filter(Boolean).join(" · ");
}

export function renderDashRunning(frames: SessionLike[]): void {
  // A running session waiting for a decision is listed under "Needs
  // attention" (approve / restore), not here as well.
  const attention = attentionFrameIds();
  const running = runningDashboardFrames(frames).filter((f) => !(f.id && attention.has(f.id)));
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
    const sub = runCardSubtitle(f, pj);
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

/**
 * Refreshes that ride the dashboard's 4s poll besides its Running card (the
 * attention stream): one interval and one visibility listener for all of
 * them, started and stopped with the dashboard, where each feature used to
 * keep its own interval, listener and class observer.
 */
const dashPollers = new Set<() => void>();

export function onDashPoll(refresh: () => void): () => void {
  dashPollers.add(refresh);
  return () => {
    dashPollers.delete(refresh);
  };
}

function pollDashboard(): void {
  void refreshDashRunning();
  dashPollers.forEach((refresh) => refresh());
}

export function startDashPoll(): void {
  stopDashPoll();
  // loadDashboard reads the lists; the riders read now, not a tick from now.
  dashPollers.forEach((refresh) => refresh());
  _dashPoll.value = setInterval(pollDashboard, 4000);
  if (!visBound && typeof document !== "undefined") {
    visBound = true;
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && !$("#dashboard")?.classList.contains("hidden")) {
        pollDashboard();
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
binds.renderDashProjects = renderDashProjects;

/**
 * The attention stream is read on the same poll, separately: when the set of
 * sessions it lists changes, Running and Recent repaint so each session stays
 * in one place. Only a change of that set repaints (the stream assigns a new
 * array on every 4 s read).
 */
let attentionKey = "";
attentionCards.subscribe((cards) => {
  const key = (cards || [])
    .map((card) => card.frameId)
    .sort()
    .join("\u0000");
  if (key === attentionKey) return;
  attentionKey = key;
  // Before the first /frames read the lists hold the skeleton: repainting
  // them from an empty list would flash "no sessions yet" and the example.
  if (!dashFramesRead) return;
  const dash = $("#dashboard");
  if (!dash || dash.classList.contains("hidden")) return;
  renderDashRunning(_dashFrames);
  renderDashRecent(_dashFrames);
});
