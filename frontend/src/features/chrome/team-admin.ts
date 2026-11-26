/**
 * The Team admin panel's account management (TEAM-25): add a user, and per
 * row disable / enable / reset password -- what used to need the host's CLI
 * or hand-written curl. The panel itself (sections, tables) is `team.ts`
 * `loadAdmin`; this file owns the users section and the labels it shares.
 *
 * No native confirm(): a destructive button asks once more by turning into
 * "Confirm …" for a few seconds.
 */

import { API } from "./api";
import { el } from "./dom";
import { teamRoleLabel, teamT } from "./team-copy";
import { teamUser, type TeamUser } from "./team-identity";

type Answer = { status: number; body: Record<string, unknown> };

async function post(path: string, body: unknown): Promise<Answer> {
  const r = await fetch(API + path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  let parsed: Record<string, unknown> = {};
  try {
    parsed = (await r.json()) as Record<string, unknown>;
  } catch {
    /* the status says enough */
  }
  return { status: r.status, body: parsed || {} };
}

/** An audit action in the interface language; an unknown one as stored. */
export function auditActionLabel(action: unknown): string {
  const raw = String(action ?? "");
  const key = "team.audit." + raw;
  const label = teamT(key);
  return label === key ? raw : label;
}

/** A user's name for a table cell: the display name with the login beside it. */
export function userLabel(user: TeamUser | undefined, fallback: unknown = ""): string {
  if (!user) return fallback == null ? "" : String(fallback);
  const login = String(user.username || "");
  return user.display_name ? `${user.display_name} (${login})` : login;
}

function failure(res: Answer): string {
  if (res.body.code === "last_admin") return teamT("team.user.lastAdmin");
  return teamT("team.user.failed", String(res.body.error || "HTTP " + res.status));
}

/**
 * A button that does nothing on its first click but ask again: its label
 * becomes `confirmLabel` until a second click within 4 s runs `action`.
 */
function confirmButton(label: string, confirmLabel: string, action: () => void): HTMLButtonElement {
  const button = el("button", "outline-btn small", label) as HTMLButtonElement;
  button.type = "button";
  let armed: ReturnType<typeof setTimeout> | null = null;
  button.onclick = () => {
    if (armed == null) {
      button.textContent = confirmLabel;
      button.classList.add("danger");
      armed = setTimeout(() => {
        armed = null;
        button.textContent = label;
        button.classList.remove("danger");
      }, 4000);
      return;
    }
    clearTimeout(armed);
    armed = null;
    button.disabled = true;
    action();
  };
  return button;
}

function cell(row: HTMLElement, content: string | Node, className?: string): HTMLElement {
  const td = document.createElement("td");
  if (className) td.className = className;
  if (typeof content === "string") td.textContent = content;
  else td.appendChild(content);
  row.appendChild(td);
  return td;
}

/**
 * The users section: an add form, then one row per account with its actions.
 * `reload` repaints the whole panel after a change; `say` is the section's
 * status line, which survives that repaint (a generated password must stay
 * readable after the table under it refreshes).
 */
export function renderUsersSection(
  box: HTMLElement,
  users: TeamUser[],
  reload: () => void,
  said: { text: string; kind: "ok" | "err" | "" },
): void {
  const line = el("div", "team-form-status" + (said.kind ? " " + said.kind : ""), said.text);
  line.setAttribute("role", "status");
  const say = (text: string, kind: "ok" | "err" | "" = ""): void => {
    said.text = text;
    said.kind = kind;
    line.textContent = text;
    line.className = "team-form-status" + (kind ? " " + kind : "");
  };
  const act = (path: string, body: unknown, done: (res: Answer) => string): void => {
    void post(path, body)
      .then((res) => {
        if (res.status >= 200 && res.status < 300) say(done(res), "ok");
        else say(failure(res), "err");
      })
      .catch(() => say(teamT("team.user.failed", "network"), "err"))
      .finally(() => reload());
  };

  // -- add a user --------------------------------------------------------
  const form = el("form", "team-admin-add") as HTMLFormElement;
  const name = document.createElement("input");
  name.className = "cust-input";
  name.id = "team-add-username";
  name.placeholder = teamT("team.add.username");
  name.setAttribute("aria-label", teamT("team.add.username"));
  name.setAttribute("autocomplete", "off");
  name.setAttribute("autocapitalize", "none");
  name.spellcheck = false;
  const display = document.createElement("input");
  display.className = "cust-input";
  display.id = "team-add-display";
  display.placeholder = teamT("team.add.displayName");
  display.setAttribute("aria-label", teamT("team.add.displayName"));
  display.setAttribute("autocomplete", "off");
  const role = document.createElement("select");
  role.className = "cust-input";
  role.id = "team-add-role";
  role.setAttribute("aria-label", teamT("team.col.role"));
  for (const value of ["member", "admin", "guest"]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = teamRoleLabel(value);
    role.appendChild(option);
  }
  const add = el("button", "solid-btn", teamT("team.add.submit")) as HTMLButtonElement;
  add.type = "submit";
  form.appendChild(name);
  form.appendChild(display);
  form.appendChild(role);
  form.appendChild(add);
  form.onsubmit = (event) => {
    event.preventDefault();
    const username = name.value.trim();
    if (!username) return say(teamT("team.add.needName"), "err");
    add.disabled = true;
    act(
      "/team/users",
      { username, display_name: display.value.trim(), role: role.value || "member" },
      (res) => teamT("team.add.done", username, String(res.body.password || "")),
    );
  };
  box.appendChild(form);
  box.appendChild(line);

  // -- the accounts ------------------------------------------------------
  const me = teamUser.value;
  const table = document.createElement("table");
  table.className = "team-admin-table";
  const head = document.createElement("tr");
  for (const key of ["team.col.user", "team.col.displayName", "team.col.role", "team.col.state", "team.col.id", "team.col.actions"]) {
    const th = document.createElement("th");
    th.textContent = teamT(key);
    head.appendChild(th);
  }
  table.appendChild(head);
  for (const user of users) {
    const row = document.createElement("tr");
    const login = String(user.username || "");
    const self = !!me && !!user.id && me.id === user.id;
    cell(row, self ? `${login} (${teamT("team.user.you")})` : login);
    cell(row, String(user.display_name || ""));
    cell(row, teamRoleLabel(user.role));
    cell(row, user.disabled ? teamT("team.state.disabled") : teamT("team.state.active"));
    cell(row, String(user.id || ""));
    const actions = el("div", "team-admin-actions");
    const id = encodeURIComponent(String(user.id || ""));
    if (user.disabled) {
      const enable = el("button", "outline-btn small", teamT("team.user.enable")) as HTMLButtonElement;
      enable.type = "button";
      enable.onclick = () => {
        enable.disabled = true;
        act(`/team/users/${id}/enable`, {}, () => teamT("team.user.enabled", login));
      };
      actions.appendChild(enable);
    } else if (!self) {
      actions.appendChild(
        confirmButton(teamT("team.user.disable"), teamT("team.user.confirmDisable"), () =>
          act(`/team/users/${id}/disable`, {}, () => teamT("team.user.disabledDone", login)),
        ),
      );
    }
    // Neither is offered on one's own row: both sign this admin out of the
    // panel they are using (their own password is the user menu's).
    if (!self) {
      actions.appendChild(
        confirmButton(teamT("team.user.reset"), teamT("team.user.confirmReset"), () =>
          act(`/team/users/${id}/reset-password`, {}, (res) =>
            teamT("team.user.resetDone", login, String(res.body.password || "")),
          ),
        ),
      );
    }
    cell(row, actions, "team-admin-actions-cell");
    table.appendChild(row);
  }
  box.appendChild(table);
  if (!users.length) box.appendChild(el("div", "team-admin-empty", teamT("team.admin.none")));
}
