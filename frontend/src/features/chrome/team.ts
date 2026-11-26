/**
 * Team mode (docs/team-server-plan.md M1-9 + M2-7).
 * Port of the two IIFEs at app.js:13450-13589 and 13592-13682.
 *
 * Combined into one `/auth/me` probe (both IIFEs fetched it). Team modals
 * now go through the unified focus trap — the original bypassed it with
 * `classList.remove("hidden")`.
 */

import { LANG } from "../../i18n/runtime";
import type { MenuItem } from "../sessions/chrome";
import { displayProjectName } from "../sessions/copy";
import { clearResumeMarker } from "../onboarding/machine";
import { API } from "./api";
import { byId, el, iconEl } from "./dom";
import { closeModalEl, openModalEl } from "./modal";
import { showNotice } from "./dialog";
import { openChangePassword, openMyKeys } from "./team-account";
import { auditActionLabel, renderUsersSection, userLabel } from "./team-admin";
import { teamRoleLabel, teamT } from "./team-copy";
import { teamFilesAvailable, teamUser, type TeamUser } from "./team-identity";

/**
 * Admin-panel section headings. A local table: the generated
 * `i18n/en.ts` / `zh.ts` extracts are byte-checked and must not grow.
 */
const SECTION_COPY: Record<"zh" | "en", Record<string, string>> = {
  zh: {
    users: "用户",
    usage: "用量",
    quotas: "配额",
    invites: "邀请",
    audit: "审计（最近 50 条）",
  },
  en: {
    users: "Users",
    usage: "Usage",
    quotas: "Quotas",
    invites: "Invites",
    audit: "Audit (latest 50)",
  },
};

export function teamSectionTitle(key: string): string {
  return (SECTION_COPY[LANG] || SECTION_COPY.en)[key] || SECTION_COPY.en[key] || key;
}

type AuthMe = {
  team_mode?: boolean;
  user?: TeamUser;
};

type TeamFilesRoot = { path: string };
type TeamFilesEntry = { name: string; dir?: boolean; size?: number };
type TeamFilesBody = {
  error?: string;
  roots?: TeamFilesRoot[];
  entries?: TeamFilesEntry[];
  path?: string;
};

/** `path` is the directory on screen (and the Upload target); `seq` the latest read. */
const tfState = { path: "", seq: 0 };

export function resetTeamFilesPath(): void {
  tfState.path = "";
}

export function teamFilesPath(): string {
  return tfState.path;
}

function pageLocation(): { pathname: string; search?: string; replace: (url: string) => void } {
  if (typeof location !== "undefined") return location;
  return { pathname: "/", replace: () => undefined };
}

/**
 * The login page, carrying this page as `?next=` so a session that expires
 * under an open workbench comes back to where it was (TEAM-9). The login
 * page decides whether `next` is a safe same-origin path before it goes there.
 */
export function loginUrl(): string {
  const loc = pageLocation();
  const here = (loc.pathname || "/") + (loc.search || "");
  return here === "/" ? "/login" : "/login?next=" + encodeURIComponent(here);
}

/**
 * The team modals. Idempotent.
 *
 * Only the modals: they are fixed overlays, so `<body>` is where they belong.
 * The signed-in user, the admin entry and the Team files buttons used to be
 * appended here too, which put them after the Shell in the page flow, under
 * the dashboard, where nobody could see or click them. The Shell renders
 * them now (`team-chip.tsx`).
 */
export function ensureTeamDom(): void {
  if (typeof document === "undefined") return;
  if (!byId("team-admin-modal")) {
    document.body.appendChild(buildAdminModal());
  }
  if (!byId("team-files-modal")) {
    document.body.appendChild(buildFilesModal());
  }
}

/**
 * A modal's close control with its icon drawn here: these modals are built
 * after `paintIcons()` ran, so a bare `icon-ghost` button stayed an empty,
 * invisible square (TEAM-25).
 */
function closeButton(id: string): HTMLElement {
  const close = el("button", "icon-ghost");
  close.id = id;
  close.setAttribute("type", "button");
  close.setAttribute("aria-label", teamT("team.close"));
  close.setAttribute("title", teamT("team.close"));
  close.appendChild(iconEl("x", 16));
  return close;
}

/** The admin modal's own words, again on each open: the language may have changed. */
function labelAdminModal(): void {
  const title = byId("team-admin-title");
  if (title) title.textContent = teamT("team.admin.title");
  const refresh = byId("team-admin-refresh");
  if (refresh) refresh.textContent = teamT("team.admin.refresh");
  const close = byId("team-admin-close");
  if (close) close.setAttribute("aria-label", teamT("team.close"));
}

function buildAdminModal(): HTMLElement {
  const modal = el("div", "modal hidden");
  modal.id = "team-admin-modal";
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  modal.setAttribute("aria-labelledby", "team-admin-title");
  const box = el("div", "modal-box");
  const head = el("div", "modal-head");
  const title = el("span", null, teamT("team.admin.title"));
  title.id = "team-admin-title";
  const actions = el("div", "modal-actions");
  const refresh = el("button", "outline-btn small", teamT("team.admin.refresh"));
  refresh.id = "team-admin-refresh";
  const close = closeButton("team-admin-close");
  actions.appendChild(refresh);
  actions.appendChild(close);
  head.appendChild(title);
  head.appendChild(actions);
  const bodyWrap = el("div", "modal-body");
  const body = el("div", "team-admin-body");
  body.id = "team-admin-body";
  bodyWrap.appendChild(body);
  box.appendChild(head);
  box.appendChild(bodyWrap);
  modal.appendChild(box);
  return modal;
}

/** I18N-5: the files modal's own words, again on each open, as the admin modal's. */
function labelFilesModal(): void {
  const title = byId("team-files-title");
  if (title) title.textContent = teamT("team.files");
  const upload = byId("team-files-upload");
  if (upload) upload.textContent = teamT("team.files.upload");
  const close = byId("team-files-close");
  if (close) {
    close.setAttribute("aria-label", teamT("team.close"));
    close.setAttribute("title", teamT("team.close"));
  }
}

function buildFilesModal(): HTMLElement {
  const modal = el("div", "modal hidden");
  modal.id = "team-files-modal";
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  modal.setAttribute("aria-labelledby", "team-files-title");
  const box = el("div", "modal-box small-box");
  const head = el("div", "modal-head");
  const title = el("span", null, teamT("team.files"));
  title.id = "team-files-title";
  const actions = el("div", "modal-actions");
  const upload = el("button", "outline-btn small", teamT("team.files.upload"));
  upload.id = "team-files-upload";
  const input = document.createElement("input");
  input.id = "team-files-input";
  input.type = "file";
  input.style.display = "none";
  const close = closeButton("team-files-close");
  actions.appendChild(upload);
  actions.appendChild(input);
  actions.appendChild(close);
  head.appendChild(title);
  head.appendChild(actions);
  const body = el("div", "modal-body");
  const crumbs = el("div", "team-files-crumbs");
  crumbs.id = "team-files-crumbs";
  const list = el("div", "team-files-list");
  list.id = "team-files-list";
  body.appendChild(crumbs);
  body.appendChild(list);
  box.appendChild(head);
  box.appendChild(body);
  modal.appendChild(box);
  return modal;
}

/** app.js:13495-13500 */
export function fmtSize(n: number): string {
  if (n >= 1073741824) return (n / 1073741824).toFixed(1) + " GB";
  if (n >= 1048576) return (n / 1048576).toFixed(1) + " MB";
  if (n >= 1024) return (n / 1024).toFixed(1) + " KB";
  return n + " B";
}

/** What the signed-in user is called on screen: the display name when one is set. */
export function teamUserName(user: TeamUser): string {
  return String(user.display_name || user.username || "");
}

/** Ends the login session, then shows the login page whatever the server said. */
export function signOut(): void {
  const leave = () => pageLocation().replace("/login");
  // The first-run wizard's resume marker belongs to this sign-in (NV-04).
  clearResumeMarker();
  fetch(API + "/auth/logout", { method: "POST" }).then(leave, leave);
}

/**
 * The user menu, as data. Who is signed in (never a button), then what that
 * account may open: `Team admin` for an admin only, the account's own
 * password and model keys for a person (not the CLI identity, which has
 * neither, nor a replay-only guest, whose API is closed to both).
 */
export function teamMenuItems(user: TeamUser): MenuItem[] {
  const head = el("div", "team-menu-head");
  head.appendChild(el("div", "team-menu-name", teamUserName(user)));
  // The login name is repeated only when a display name stands in for it above.
  const login = user.display_name && user.username ? user.username + " · " : "";
  head.appendChild(el("div", "team-menu-sub", login + teamRoleLabel(user.role)));
  const own: MenuItem[] = [];
  if (user.role === "admin" || user.kind === "service") {
    own.push({ label: teamT("team.menu.admin"), icon: "users", onClick: () => openAdmin() });
  }
  if (user.kind !== "service" && user.role !== "guest") {
    own.push({ label: teamT("team.menu.password"), icon: "lock", onClick: () => openChangePassword() });
    own.push({ label: teamT("team.menu.keys"), icon: "sparkles", onClick: () => openMyKeys() });
  }
  return [
    { node: head },
    { sep: true },
    ...own,
    ...(own.length ? [{ sep: true } as MenuItem] : []),
    { label: teamT("team.menu.signOut"), icon: "arrow-left", onClick: () => signOut() },
  ];
}

/** Opens the user menu under `anchor` (the chip the Shell renders). */
export function openUserMenu(anchor: HTMLElement): void {
  const user = teamUser.value;
  if (!user) return;
  void import("../sessions/chrome").then(({ openMenu }) => openMenu(anchor, teamMenuItems(user)));
}

function applyGovernance(me: AuthMe | null): void {
  if (!me || me.team_mode !== true || !me.user) return;
  if (me.user.role === "guest") {
    if (pageLocation().pathname === "/") pageLocation().replace("/replay");
  }
}

/** app.js:13456-13475 + 13596-13610, one /auth/me. */
export async function probeTeamAuth(): Promise<void> {
  try {
    const r = await fetch(API + "/auth/me");
    if (r.status === 401) {
      pageLocation().replace(loginUrl());
      return;
    }
    const me = r.ok ? ((await r.json()) as AuthMe) : null;
    teamUser.value = me && me.team_mode === true && me.user ? me.user : null;
    applyGovernance(me);
  } catch {
    /* team mode off / network */
  }
}

export function openTeamFilesPanel(): void {
  labelFilesModal();
  openModalEl(byId("team-files-modal"));
  void loadTeamFiles(tfState.path);
}

export function openAdmin(): void {
  labelAdminModal();
  // A generated password from an earlier visit is not shown again.
  adminStatus.text = "";
  adminStatus.kind = "";
  openModalEl(byId("team-admin-modal"));
  void loadAdmin();
}

function probeTeamFiles(): void {
  fetch(API + "/files")
    .then((r) => (r.ok ? r.json() : null))
    .then((d: { roots?: TeamFilesRoot[] } | null) => {
      teamFilesAvailable.value = !!(d && d.roots && d.roots.length);
    })
    .catch(() => undefined);
}

/**
 * Only the latest read renders, and the path changes when its listing does.
 * Opening a slow directory A and then its sibling B used to paint A's rows
 * when A answered last while `tfState.path` said B, and Upload (overwrite=1)
 * then wrote into B under names read from A.
 */
function loadTeamFiles(path: string): void {
  const target = path || "";
  const seq = ++tfState.seq;
  const url = API + "/files" + (target ? "?path=" + encodeURIComponent(target) : "");
  fetch(url)
    .then((r) => r.json().then((b: TeamFilesBody) => ({ ok: r.ok, body: b })))
    .then((res) => {
      if (seq !== tfState.seq) return;
      tfState.path = target;
      renderTeamFiles(res);
    })
    .catch(() => undefined);
}

function renderTeamFiles(res: { ok: boolean; body: TeamFilesBody }): void {
  const crumbs = byId("team-files-crumbs");
  const list = byId("team-files-list");
  if (!crumbs || !list) return;
  crumbs.textContent = "";
  list.textContent = "";
  if (!res.ok) {
    const reason = res.body && res.body.error;
    list.textContent = reason ? teamT("team.files.failed", reason) : teamT("team.files.unavailable");
    // Nothing is listed, so there is no directory on screen to upload into.
    const upBtn = byId("team-files-upload");
    if (upBtn) upBtn.style.display = "none";
    return;
  }
  const home = document.createElement("a");
  home.href = "#";
  home.textContent = teamT("team.files.roots");
  home.onclick = (e) => {
    e.preventDefault();
    loadTeamFiles("");
  };
  crumbs.appendChild(home);
  if (tfState.path) {
    crumbs.appendChild(document.createTextNode("  ›  " + tfState.path));
  }
  const upBtn = byId("team-files-upload");
  if (upBtn) upBtn.style.display = tfState.path ? "" : "none";
  if (res.body.roots) {
    res.body.roots.forEach((root) => {
      const row = el("div", "team-files-row");
      const a = document.createElement("a");
      a.href = "#";
      a.textContent = "📁 " + root.path;
      a.onclick = (ev) => {
        ev.preventDefault();
        loadTeamFiles(root.path);
      };
      row.appendChild(a);
      list.appendChild(row);
    });
    return;
  }
  (res.body.entries || []).forEach((entry) => {
    const row = el("div", "team-files-row");
    const full = (res.body.path || "") + "/" + entry.name;
    if (entry.dir) {
      const a = document.createElement("a");
      a.href = "#";
      a.textContent = "📁 " + entry.name;
      a.onclick = (ev) => {
        ev.preventDefault();
        loadTeamFiles(full);
      };
      row.appendChild(a);
    } else {
      const link = document.createElement("a");
      link.href = API + "/files/download?path=" + encodeURIComponent(full);
      link.textContent = "📄 " + entry.name;
      row.appendChild(link);
      const size = el("span", "team-files-size", fmtSize(entry.size || 0));
      row.appendChild(size);
    }
    list.appendChild(row);
  });
  if (!(res.body.entries || []).length) {
    const empty = el("div", "team-files-row", teamT("team.files.empty"));
    list.appendChild(empty);
  }
}

function section(parent: HTMLElement, title: string): HTMLElement {
  const h = el("h3", "team-admin-h", title);
  parent.appendChild(h);
  const box = el("div", "team-admin-sec");
  parent.appendChild(box);
  return box;
}

function table(box: HTMLElement, headers: string[], rows: unknown[][]): void {
  const t = document.createElement("table");
  t.className = "team-admin-table";
  const tr = document.createElement("tr");
  headers.forEach((h) => {
    const th = document.createElement("th");
    th.textContent = h;
    tr.appendChild(th);
  });
  t.appendChild(tr);
  rows.forEach((cells) => {
    const r = document.createElement("tr");
    cells.forEach((c) => {
      const td = document.createElement("td");
      td.textContent = c == null ? "" : String(c);
      r.appendChild(td);
    });
    t.appendChild(r);
  });
  box.appendChild(t);
  if (!rows.length) {
    const d = el("div", "team-admin-empty", teamT("team.admin.none"));
    box.appendChild(d);
  }
}

/** The users section's status line, kept across the repaint an action triggers. */
const adminStatus: { text: string; kind: "ok" | "err" | "" } = { text: "", kind: "" };

/**
 * Audit `ts` is epoch milliseconds (storage/team.py `_clock_ms`). As a string
 * it parsed as a date string and every row read "Invalid Date".
 */
export function auditWhen(ts: unknown): string {
  const text = ts == null ? "" : String(ts).trim();
  const when = /^-?\d+(\.\d+)?$/.test(text) ? new Date(Number(text)) : new Date(text);
  return Number.isNaN(when.getTime()) ? text : when.toLocaleString();
}

function jget(path: string): Promise<unknown> {
  return fetch(API + path).then((r) => (r.ok ? r.json() : null));
}

export async function loadAdmin(): Promise<void> {
  const body = byId("team-admin-body");
  if (!body) return;
  body.textContent = teamT("team.loading");
  try {
    const res = await Promise.all([
      jget("/team/users"),
      jget("/team/usage"),
      jget("/team/audit?limit=50"),
      jget("/team/invites"),
      jget("/team/quotas"),
      // Only to put names beside project ids; the panel works without it.
      jget("/projects").catch(() => null),
    ]);
    const users = ((res[0] as { users?: TeamUser[] } | null) || {}).users || [];
    const usage = ((res[1] as { usage?: Record<string, unknown>[] } | null) || {}).usage || [];
    const audit = ((res[2] as { audit?: Record<string, unknown>[] } | null) || {}).audit || [];
    const invites = ((res[3] as { invites?: Record<string, unknown>[] } | null) || {}).invites || [];
    const quotas = ((res[4] as { quotas?: Record<string, unknown>[] } | null) || {}).quotas || [];
    const projects = ((res[5] as { projects?: Array<{ project_id?: string; name?: string }> } | null) || {}).projects || [];
    body.textContent = "";
    // TEAM-25: names, not internal ids, wherever a row points at a person or
    // a project; the id stays when nothing better is known.
    const byUserId: Record<string, TeamUser> = {};
    users.forEach((u) => {
      if (u.id) byUserId[u.id] = u;
    });
    const projectName: Record<string, string> = {};
    projects.forEach((p) => {
      if (p.project_id) projectName[p.project_id] = String(displayProjectName(p) || p.project_id);
    });
    const person = (id: unknown): string => userLabel(byUserId[String(id)], id);
    const place = (id: unknown): string => (id == null || id === "" ? "" : projectName[String(id)] || String(id));
    const col = (...keys: string[]): string[] => keys.map((key) => teamT("team.col." + key));
    renderUsersSection(section(body, teamSectionTitle("users")), users, () => void loadAdmin(), adminStatus);
    table(section(body, teamSectionTitle("usage")), col("user", "project", "kind", "total", "events"),
      usage.map((r) => [
        person(r.user_id),
        place(r.project_id),
        r.kind,
        Math.round(Number(r.total) * 100) / 100,
        r.events,
      ]));
    table(section(body, teamSectionTitle("quotas")), col("scope", "scopeId", "kind", "limit", "window"),
      quotas.map((r) => [
        r.scope,
        r.scope === "user" ? person(r.scope_id) : r.scope === "project" ? place(r.scope_id) : r.scope_id,
        r.kind,
        r.limit_amount,
        r.window,
      ]));
    table(section(body, teamSectionTitle("invites")), col("prefix", "project", "by", "state"),
      invites.map((r) => [
        r.token_prefix,
        place(r.project_id),
        r.created_by,
        teamT(r.live ? "team.invite.live" : (r.used_at ? "team.invite.used" : "team.invite.expired")),
      ]));
    table(section(body, teamSectionTitle("audit")), col("when", "actor", "action", "target"),
      audit.map((r) => [
        auditWhen(r.ts),
        r.actor,
        auditActionLabel(r.action),
        r.target ? (byUserId[String(r.target)] ? person(r.target) : r.target)
          : r.user_id ? person(r.user_id) : place(r.project_id),
      ]));
  } catch {
    const failed = byId("team-admin-body");
    if (failed) failed.textContent = teamT("team.admin.failed");
  }
}

function bindTeamChrome(): void {
  const closeAdmin = byId("team-admin-close");
  if (closeAdmin) {
    closeAdmin.onclick = () => closeModalEl(byId("team-admin-modal"));
  }
  const refresh = byId("team-admin-refresh");
  if (refresh) refresh.onclick = () => void loadAdmin();
  const closeFiles = byId("team-files-close");
  if (closeFiles) {
    closeFiles.onclick = () => closeModalEl(byId("team-files-modal"));
  }
  const uploadBtn = byId("team-files-upload");
  const uploadInput = byId("team-files-input") as HTMLInputElement | null;
  if (uploadBtn && uploadInput) {
    uploadBtn.onclick = () => {
      if (tfState.path) uploadInput.click();
    };
    uploadInput.onchange = () => {
      const file = uploadInput.files && uploadInput.files[0];
      uploadInput.value = "";
      if (!file || !tfState.path) return;
      const url =
        API +
        "/files/upload?dir=" +
        encodeURIComponent(tfState.path) +
        "&name=" +
        encodeURIComponent(file.name) +
        "&overwrite=1";
      fetch(url, { method: "POST", body: file })
        .then((r) => {
          if (!r.ok) {
            const failed = (detail?: string) =>
              void showNotice(teamT("team.files.uploadFailed", detail || "HTTP " + r.status));
            return r.json().then(
              (b: { error?: string }) => failed(b && b.error),
              () => failed(),
            );
          }
          loadTeamFiles(tfState.path);
        })
        .catch(() => {
          void showNotice(teamT("team.files.uploadUnreachable"));
        });
    };
  }
}

export function bootTeam(): void {
  ensureTeamDom();
  bindTeamChrome();
  if (import.meta.env.MODE === "test") return;
  void probeTeamAuth();
  probeTeamFiles();
}
