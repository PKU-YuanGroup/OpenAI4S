/**
 * The signed-in user's own account, from the user menu (TEAM-22): change the
 * password, and the model keys of their own. Both existed only as routes --
 * a member had to curl `PUT /auth/me/llm-key`, and could not change the
 * CLI-generated password at all.
 *
 * Built like the other team modals (DOM, the shared focus trap) and appended
 * to `<body>` once: they are fixed overlays.
 */

import { API } from "./api";
import { byId, el, iconEl } from "./dom";
import { closeModalEl, openModalEl } from "./modal";
import { teamT } from "./team-copy";

type KeyRecord = { provider: string; updated_at?: number | null; created_at?: number | null };

/** A small modal: head with a title and a real close icon, an empty body. */
export function teamDialog(id: string, title: string): { modal: HTMLElement; body: HTMLElement } {
  const existing = byId(id);
  if (existing) {
    return { modal: existing, body: existing.querySelector(".team-form") as HTMLElement };
  }
  const modal = el("div", "modal hidden");
  modal.id = id;
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  modal.setAttribute("aria-labelledby", id + "-title");
  const box = el("div", "modal-box small-box");
  const head = el("div", "modal-head");
  const heading = el("span", null, title);
  heading.id = id + "-title";
  const close = el("button", "icon-ghost") as HTMLButtonElement;
  close.type = "button";
  close.id = id + "-close";
  close.setAttribute("aria-label", teamT("team.close"));
  close.title = teamT("team.close");
  close.appendChild(iconEl("x", 16));
  close.onclick = () => closeModalEl(modal);
  head.appendChild(heading);
  head.appendChild(close);
  const body = el("div", "modal-body team-form");
  box.appendChild(head);
  box.appendChild(body);
  modal.appendChild(box);
  document.body.appendChild(modal);
  return { modal, body };
}

function field(
  body: HTMLElement,
  label: string,
  attrs: { type: string; autocomplete: string; id: string },
): HTMLInputElement {
  const wrap = el("label", "team-field");
  wrap.appendChild(el("span", null, label));
  const input = document.createElement("input");
  input.className = "cust-input";
  input.type = attrs.type;
  input.id = attrs.id;
  input.setAttribute("autocomplete", attrs.autocomplete);
  input.spellcheck = false;
  wrap.appendChild(input);
  body.appendChild(wrap);
  return input;
}

function status(node: HTMLElement, text: string, kind: "err" | "ok" | "" = ""): void {
  node.textContent = text;
  node.className = "team-form-status" + (kind ? " " + kind : "");
}

async function send(path: string, method: string, body: unknown): Promise<{ status: number; body: Record<string, unknown> }> {
  const r = await fetch(API + path, {
    method,
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  let parsed: Record<string, unknown> = {};
  try {
    parsed = (await r.json()) as Record<string, unknown>;
  } catch {
    /* an empty or non-JSON answer: the status says enough */
  }
  return { status: r.status, body: parsed || {} };
}

/** What a refused change says, by the route's stable code. */
export function passwordFailure(code: string, httpStatus: number): string {
  if (code === "wrong_password") return teamT("team.password.wrong");
  if (code === "weak_password") return teamT("team.password.weak");
  if (code === "rate_limited" || httpStatus === 429) return teamT("team.password.rateLimited");
  return teamT("team.password.failed", httpStatus);
}

export function openChangePassword(): void {
  const { modal, body } = teamDialog("team-password-modal", teamT("team.password.title"));
  body.textContent = "";
  const form = el("form", "team-form-inner") as HTMLFormElement;
  const current = field(form, teamT("team.password.current"), {
    type: "password", autocomplete: "current-password", id: "team-password-current",
  });
  current.setAttribute("data-autofocus", "");
  const next = field(form, teamT("team.password.new"), {
    type: "password", autocomplete: "new-password", id: "team-password-new",
  });
  const again = field(form, teamT("team.password.confirm"), {
    type: "password", autocomplete: "new-password", id: "team-password-again",
  });
  form.appendChild(el("p", "team-form-note", teamT("team.password.note")));
  const line = el("div", "team-form-status");
  line.setAttribute("role", "status");
  form.appendChild(line);
  const actions = el("div", "team-form-actions");
  const cancel = el("button", "outline-btn", teamT("team.cancel")) as HTMLButtonElement;
  cancel.type = "button";
  cancel.onclick = () => closeModalEl(modal);
  const save = el("button", "solid-btn", teamT("team.save")) as HTMLButtonElement;
  save.type = "submit";
  actions.appendChild(cancel);
  actions.appendChild(save);
  form.appendChild(actions);
  form.onsubmit = (event) => {
    event.preventDefault();
    if (next.value !== again.value) return status(line, teamT("team.password.mismatch"), "err");
    if (next.value.length < 8) return status(line, teamT("team.password.weak"), "err");
    save.disabled = true;
    status(line, "");
    void send("/auth/me/password", "POST", { current_password: current.value, new_password: next.value })
      .then((res) => {
        if (res.status >= 200 && res.status < 300) {
          current.value = next.value = again.value = "";
          status(line, teamT("team.password.done"), "ok");
          return;
        }
        status(line, passwordFailure(String(res.body.code || ""), res.status), "err");
      })
      .catch(() => status(line, teamT("team.password.failed", 0), "err"))
      .finally(() => {
        save.disabled = false;
      });
  };
  body.appendChild(form);
  openModalEl(modal);
}

function when(ms: unknown): string {
  const n = Number(ms);
  if (!Number.isFinite(n) || n <= 0) return "";
  const d = new Date(n);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString();
}

/** The providers the team's saved models use, offered as suggestions. */
async function knownProviders(): Promise<string[]> {
  try {
    const r = await fetch(API + "/models");
    if (!r.ok) return [];
    const data = (await r.json()) as { models?: Record<string, Array<{ provider?: unknown }>> };
    const out = new Set<string>();
    for (const list of Object.values(data.models || {})) {
      for (const entry of list || []) {
        const name = String(entry?.provider || "").trim().toLowerCase();
        if (name) out.add(name);
      }
    }
    return [...out].sort();
  } catch {
    return [];
  }
}

export function openMyKeys(): void {
  const { modal, body } = teamDialog("team-keys-modal", teamT("team.keys.title"));
  body.textContent = "";
  body.appendChild(el("p", "team-form-note", teamT("team.keys.note")));
  const list = el("div", "team-keys-list");
  body.appendChild(list);
  const form = el("form", "team-form-inner") as HTMLFormElement;
  const provider = field(form, teamT("team.keys.provider"), {
    type: "text", autocomplete: "off", id: "team-keys-provider",
  });
  provider.setAttribute("list", "team-keys-providers");
  provider.setAttribute("autocapitalize", "none");
  const options = document.createElement("datalist");
  options.id = "team-keys-providers";
  form.appendChild(options);
  const key = field(form, teamT("team.keys.key"), {
    type: "password", autocomplete: "off", id: "team-keys-value",
  });
  const line = el("div", "team-form-status");
  line.setAttribute("role", "status");
  form.appendChild(line);
  const actions = el("div", "team-form-actions");
  const save = el("button", "solid-btn", teamT("team.keys.save")) as HTMLButtonElement;
  save.type = "submit";
  actions.appendChild(save);
  form.appendChild(actions);
  body.appendChild(form);

  const render = async (): Promise<void> => {
    list.textContent = teamT("team.loading");
    let keys: KeyRecord[] = [];
    try {
      const r = await fetch(API + "/auth/me/llm-key");
      if (!r.ok) throw new Error(String(r.status));
      keys = ((await r.json()) as { keys?: KeyRecord[] }).keys || [];
    } catch {
      list.textContent = teamT("team.keys.loadFailed");
      return;
    }
    list.textContent = "";
    if (!keys.length) {
      list.appendChild(el("div", "team-keys-empty", teamT("team.keys.none")));
      return;
    }
    for (const record of keys) {
      const row = el("div", "team-keys-row");
      row.appendChild(el("span", "team-keys-provider", record.provider));
      row.appendChild(el("span", "team-keys-when", teamT("team.keys.set", when(record.updated_at || record.created_at))));
      const remove = el("button", "outline-btn small", teamT("team.keys.remove")) as HTMLButtonElement;
      remove.type = "button";
      remove.onclick = () => {
        remove.disabled = true;
        void send("/auth/me/llm-key", "DELETE", { provider: record.provider })
          .then((res) => {
            if (res.status >= 300) status(line, teamT("team.keys.failed", String(res.body.error || res.status)), "err");
          })
          .catch(() => status(line, teamT("team.keys.failed", "network"), "err"))
          .finally(() => void render());
      };
      row.appendChild(remove);
      list.appendChild(row);
    }
  };

  form.onsubmit = (event) => {
    event.preventDefault();
    const name = provider.value.trim().toLowerCase();
    if (!name) return status(line, teamT("team.keys.needProvider"), "err");
    if (!key.value.trim()) return status(line, teamT("team.keys.needKey"), "err");
    save.disabled = true;
    status(line, "");
    void send("/auth/me/llm-key", "PUT", { provider: name, api_key: key.value })
      .then((res) => {
        if (res.status >= 200 && res.status < 300) {
          key.value = "";
          status(line, teamT("team.keys.saved", name), "ok");
          return render();
        }
        status(line, teamT("team.keys.failed", String(res.body.error || res.status)), "err");
        return undefined;
      })
      .catch(() => status(line, teamT("team.keys.failed", "network"), "err"))
      .finally(() => {
        save.disabled = false;
      });
  };

  void render();
  void knownProviders().then((names) => {
    options.textContent = "";
    for (const name of names) {
      const option = document.createElement("option");
      option.value = name;
      options.appendChild(option);
    }
  });
  openModalEl(modal);
}
