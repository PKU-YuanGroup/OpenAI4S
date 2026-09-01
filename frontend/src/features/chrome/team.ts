/**
 * Team mode (docs/team-server-plan.md M1-9 + M2-7).
 * Port of the two IIFEs at app.js:13450-13589 and 13592-13682.
 *
 * Combined into one `/auth/me` probe (both IIFEs fetched it). Team modals
 * now go through the unified focus trap — the original bypassed it with
 * `classList.remove("hidden")`.
 */

import { API } from "./api";
import { byId, el } from "./dom";
import { closeModalEl, openModalEl } from "./modal";

type TeamUser = {
  id?: string;
  username?: string;
  role?: string;
  kind?: string;
  disabled?: boolean;
};

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

const tfState = { path: "" };

export function resetTeamFilesPath(): void {
  tfState.path = "";
}

export function teamFilesPath(): string {
  return tfState.path;
}

function pageLocation(): { pathname: string; replace: (url: string) => void } {
  if (typeof location !== "undefined") return location;
  return { pathname: "/", replace: () => undefined };
}

/** Inject contract DOM if F-13's shell has not painted it yet. Idempotent. */
export function ensureTeamDom(): void {
  if (typeof document === "undefined") return;
  if (!byId("team-user")) {
    const chip = el("button", "outline-btn hidden");
    chip.id = "team-user";
    chip.title = "Signed in — click to sign out";
    document.body.appendChild(chip);
  }
  if (!byId("team-admin")) {
    const btn = el("button", "outline-btn hidden");
    btn.id = "team-admin";
    const ic = el("span", "ic");
    ic.setAttribute("data-icon", "users");
    ic.setAttribute("data-icon-size", "16");
    btn.appendChild(ic);
    btn.appendChild(el("span", null, "Team admin"));
    document.body.appendChild(btn);
  }
  if (!byId("team-files-dash")) {
    const btn = el("button", "outline-btn hidden");
    btn.id = "team-files-dash";
    btn.appendChild(el("span", null, "Team files"));
    document.body.appendChild(btn);
  }
  if (!byId("team-files-btn")) {
    const btn = el("button", "side-nav-item hidden");
    btn.id = "team-files-btn";
    btn.appendChild(el("span", null, "Team files"));
    document.body.appendChild(btn);
  }
  if (!byId("team-admin-modal")) {
    document.body.appendChild(buildAdminModal());
  }
  if (!byId("team-files-modal")) {
    document.body.appendChild(buildFilesModal());
  }
}

function buildAdminModal(): HTMLElement {
  const modal = el("div", "modal hidden");
  modal.id = "team-admin-modal";
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  modal.setAttribute("aria-labelledby", "team-admin-title");
  const box = el("div", "modal-box");
  const head = el("div", "modal-head");
  const title = el("span", null, "Team admin");
  title.id = "team-admin-title";
  const actions = el("div", "modal-actions");
  const refresh = el("button", "outline-btn small", "Refresh");
  refresh.id = "team-admin-refresh";
  const close = el("button", "icon-ghost");
  close.id = "team-admin-close";
  close.setAttribute("aria-label", "Close");
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

function buildFilesModal(): HTMLElement {
  const modal = el("div", "modal hidden");
  modal.id = "team-files-modal";
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  modal.setAttribute("aria-labelledby", "team-files-title");
  const box = el("div", "modal-box small-box");
  const head = el("div", "modal-head");
  const title = el("span", null, "Team files");
  title.id = "team-files-title";
  const actions = el("div", "modal-actions");
  const upload = el("button", "outline-btn small", "Upload");
  upload.id = "team-files-upload";
  const input = document.createElement("input");
  input.id = "team-files-input";
  input.type = "file";
  input.style.display = "none";
  const close = el("button", "icon-ghost");
  close.id = "team-files-close";
  close.setAttribute("aria-label", "Close");
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

function applyIdentity(me: AuthMe | null): void {
  if (!me || me.team_mode !== true || !me.user) return;
  const chip = byId("team-user");
  if (chip) {
    chip.textContent =
      (me.user.username || "") + (me.user.role === "admin" ? " (admin)" : "");
    chip.classList.remove("hidden");
    chip.onclick = () => {
      if (!confirm("Sign out?")) return;
      fetch(API + "/auth/logout", { method: "POST" })
        .then(() => pageLocation().replace("/login"))
        .catch(() => pageLocation().replace("/login"));
    };
  }
}

function applyGovernance(me: AuthMe | null): void {
  if (!me || me.team_mode !== true || !me.user) return;
  if (me.user.role === "guest") {
    if (pageLocation().pathname === "/") pageLocation().replace("/replay");
    return;
  }
  if (me.user.role === "admin" || me.user.kind === "service") {
    const btn = byId("team-admin");
    if (btn) {
      btn.classList.remove("hidden");
      btn.onclick = () => openAdmin();
    }
  }
}

/** app.js:13456-13475 + 13596-13610, one /auth/me. */
export async function probeTeamAuth(): Promise<void> {
  try {
    const r = await fetch(API + "/auth/me");
    if (r.status === 401) {
      pageLocation().replace("/login");
      return;
    }
    const me = r.ok ? ((await r.json()) as AuthMe) : null;
    applyIdentity(me);
    applyGovernance(me);
  } catch {
    /* team mode off / network */
  }
}

export function openTeamFilesPanel(): void {
  openModalEl(byId("team-files-modal"));
  void loadTeamFiles(tfState.path);
}

export function openAdmin(): void {
  openModalEl(byId("team-admin-modal"));
  void loadAdmin();
}

function probeTeamFiles(): void {
  fetch(API + "/files")
    .then((r) => (r.ok ? r.json() : null))
    .then((d: { roots?: TeamFilesRoot[] } | null) => {
      if (!d || !d.roots || !d.roots.length) return;
      (["team-files-btn", "team-files-dash"] as const).forEach((id) => {
        const b = byId(id);
        if (b) {
          b.classList.remove("hidden");
          b.onclick = () => openTeamFilesPanel();
        }
      });
    })
    .catch(() => undefined);
}

function loadTeamFiles(path: string): void {
  tfState.path = path || "";
  const url = API + "/files" + (tfState.path ? "?path=" + encodeURIComponent(tfState.path) : "");
  fetch(url)
    .then((r) => r.json().then((b: TeamFilesBody) => ({ ok: r.ok, body: b })))
    .then((res) => renderTeamFiles(res))
    .catch(() => undefined);
}

function renderTeamFiles(res: { ok: boolean; body: TeamFilesBody }): void {
  const crumbs = byId("team-files-crumbs");
  const list = byId("team-files-list");
  if (!crumbs || !list) return;
  crumbs.textContent = "";
  list.textContent = "";
  if (!res.ok) {
    list.textContent = (res.body && res.body.error) || "unavailable";
    return;
  }
  const home = document.createElement("a");
  home.href = "#";
  home.textContent = "roots";
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
    const empty = el("div", "team-files-row", "(empty)");
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
    const d = el("div", "team-admin-empty", "(none)");
    box.appendChild(d);
  }
}

function jget(path: string): Promise<unknown> {
  return fetch(API + path).then((r) => (r.ok ? r.json() : null));
}

export async function loadAdmin(): Promise<void> {
  const body = byId("team-admin-body");
  if (!body) return;
  body.textContent = "loading…";
  try {
    const res = await Promise.all([
      jget("/team/users"),
      jget("/team/usage"),
      jget("/team/audit?limit=50"),
      jget("/team/invites"),
      jget("/team/quotas"),
    ]);
    const users = ((res[0] as { users?: TeamUser[] } | null) || {}).users || [];
    const usage = ((res[1] as { usage?: Record<string, unknown>[] } | null) || {}).usage || [];
    const audit = ((res[2] as { audit?: Record<string, unknown>[] } | null) || {}).audit || [];
    const invites = ((res[3] as { invites?: Record<string, unknown>[] } | null) || {}).invites || [];
    const quotas = ((res[4] as { quotas?: Record<string, unknown>[] } | null) || {}).quotas || [];
    body.textContent = "";
    const idName: Record<string, string> = {};
    users.forEach((u) => {
      if (u.id && u.username) idName[u.id] = u.username;
    });
    table(section(body, "Users"), ["user", "role", "state", "id"],
      users.map((u) => [u.username, u.role, u.disabled ? "disabled" : "active", u.id]));
    table(section(body, "Usage"), ["user", "project", "kind", "total", "events"],
      usage.map((r) => [
        idName[String(r.user_id)] || r.user_id,
        r.project_id,
        r.kind,
        Math.round(Number(r.total) * 100) / 100,
        r.events,
      ]));
    table(section(body, "Quotas"), ["scope", "scope id", "kind", "limit", "window"],
      quotas.map((r) => [r.scope, r.scope_id, r.kind, r.limit_amount, r.window]));
    table(section(body, "Invites"), ["prefix", "project", "by", "state"],
      invites.map((r) => [
        r.token_prefix,
        r.project_id,
        r.created_by,
        r.live ? "live" : (r.used_at ? "used/revoked" : "expired"),
      ]));
    table(section(body, "Audit (latest 50)"), ["when", "actor", "action", "target"],
      audit.map((r) => [
        new Date(String(r.ts)).toLocaleString(),
        r.actor,
        r.action,
        r.target || r.user_id || "",
      ]));
  } catch {
    const failed = byId("team-admin-body");
    if (failed) failed.textContent = "failed to load";
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
            return r.json().then((b: { error?: string }) => {
              alert((b && b.error) || "upload failed (" + r.status + ")");
            });
          }
          loadTeamFiles(tfState.path);
        })
        .catch(() => {
          alert("upload failed");
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
