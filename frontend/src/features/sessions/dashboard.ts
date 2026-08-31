/** Home dashboard. app.js:6616-6764, 2685. */

import { t } from "../../i18n";
import { currentId, projects } from "../../stores/session";
import { _dashPoll } from "../../stores/ui";
import { api, apiErrorText } from "./api";
import { binds } from "./binds";
import { ensureActivateKeys } from "./chrome";
import { $, ago, el, navURL, syncMobileChrome } from "./dom";
import { loadProjects } from "./load";
import {
  annotateRunningCounts,
  filterRootFrames,
  recentDashboardSessions,
  runningDashboardFrames,
  type SessionLike,
} from "./paging";

type ProjectLike = {
  project_id?: string;
  id?: string;
  name?: string;
  conversation_count?: number;
  last_active_at?: string;
  updated_at?: string;
  running_count?: number;
};

let exampleTimer = 0;
let visBound = false;

function stopExamplePoll(): void {
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

export async function loadDashboard(): Promise<void> {
  paintDashSkeleton();
  await loadProjects();
  let frames: SessionLike[] = [];
  try {
    const d = (await api("/frames?limit=50")) as { frames?: SessionLike[] };
    frames = filterRootFrames((d && d.frames) || []);
  } catch {
    frames = [];
  }
  annotateRunningCounts(projects.value as ProjectLike[], frames);
  renderDashProjects();
  renderDashRunning(frames);
  renderDashRecent(frames);
}

export function renderDashProjects(): void {
  const pc = $("#dash-projects");
  if (!pc) return;
  pc.innerHTML = "";
  const list = projects.value as ProjectLike[];
  if (!list.length) pc.appendChild(el("div", "dash-empty", t("dash.projects.empty")));
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
      if (id) import("./projects").then((mod) => mod.openProject(id));
    };
    row.onclick = open;
    ensureActivateKeys(row);
    pc.appendChild(row);
  });
}

function exampleSeedCta(): HTMLElement {
  stopExamplePoll();
  const box = el("div", "dash-example");
  const btn = el("button", "btn", t("dash.example.cta"));
  btn.type = "button";
  const note = el("div", "dash-example-hint", t("dash.example.hint"));
  box.appendChild(btn);
  box.appendChild(note);
  const paint = (st: { running?: boolean; error?: string; seeded?: boolean }) => {
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
      .catch(stopExamplePoll);
  btn.onclick = () => {
    btn.disabled = true;
    api("/example/session", { method: "POST", body: JSON.stringify({ confirm: true }) })
      .then((st) => {
        paint(st as { running?: boolean; error?: string; seeded?: boolean });
        stopExamplePoll();
        exampleTimer = window.setInterval(poll, 1500) as unknown as number;
      })
      .catch((e) => {
        btn.disabled = false;
        note.textContent = t("dash.example.failed") + apiErrorText(e);
      });
  };
  api("/example/session")
    .then((raw) => {
      const st = raw as { running?: boolean; error?: string; seeded?: boolean };
      if (st.seeded) box.remove();
      else paint(st);
      if (st.running) exampleTimer = window.setInterval(poll, 1500) as unknown as number;
    })
    .catch(() => box.remove());
  return box;
}

export function renderDashRecent(frames: SessionLike[]): void {
  const recent = recentDashboardSessions(frames);
  const sc = $("#dash-sessions");
  if (!sc) return;
  sc.innerHTML = "";
  if (!recent.length) {
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
  renderDashRunning(frames);
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
  $("#workspace")?.classList.add("hidden");
  $("#dashboard")?.classList.remove("hidden");
  currentId.value = null;
  void loadDashboard();
  startDashPoll();
}

export function showWorkspace(): void {
  stopDashPoll();
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
