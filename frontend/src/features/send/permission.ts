/**
 * Permission gate cards. Port of app.js:6460-6614.
 *
 * DOM class names (`.perm-card`, `.perm-card.resolved`, `.allowed`, `.denied`)
 * are frozen — they are in the E2E contract. Registry is null-proto so keys
 * like `__proto__` cannot pollute.
 */

import { t } from "../../i18n/runtime";
import { permCards, running } from "../../stores/stream";
import { el } from "../messages/dom";
import { down } from "../messages/scroll";
import { ensure } from "../messages/stream";
import { api, apiErrorText } from "../sessions/api";
import { hint } from "../sessions/chrome";
import { callLane } from "./host";
import { iconEl } from "./icon";

const PERM_SCOPE_KEYS: Record<string, string> = {
  once: "perm.scope.once",
  conversation: "perm.scope.conversation",
  project: "perm.scope.project",
  global: "perm.scope.global",
};

export function permScopeCn(s: string): string {
  return PERM_SCOPE_KEYS[s] ? t(PERM_SCOPE_KEYS[s]) : s;
}

type PermEvent = {
  decision_id?: string;
  frame_id?: string;
  tool?: string;
  title?: string;
  target?: string;
  input?: Record<string, unknown>;
  sub_agent?: unknown;
  dangerous?: unknown;
  policy_review_kind?: string;
  resolved_file_path?: string;
  scopes?: string[];
  suggested_patterns?: string[];
  allow?: unknown;
  scope?: string;
};

type PermHandle = {
  card: HTMLElement;
  allow: HTMLButtonElement;
  deny: HTMLButtonElement;
  resolved: boolean;
  resolution?: unknown;
};

function rec(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

export function permActionLine(m: PermEvent): { mono: boolean; text: string } {
  const inp = rec(m.input);
  const tool = m.tool;
  if (tool === "bash") return { mono: true, text: String(inp.command || m.target || "") };
  if (
    tool === "write_file" ||
    tool === "edit_file" ||
    tool === "read_file" ||
    tool === "save_artifact"
  ) {
    return { mono: true, text: String(inp.path || inp.filename || m.target || "") };
  }
  if (tool === "web_fetch") return { mono: true, text: String(inp.url || m.target || "") };
  if (tool === "web_search") {
    return { mono: false, text: "“" + String(inp.query || m.target || "") + "”" };
  }
  if (tool === "env_setup") {
    const packages = Array.isArray(inp.packages) ? inp.packages : [];
    return {
      mono: true,
      text: packages.length ? packages.join(" ") : String(inp.name || m.target || ""),
    };
  }
  if (tool === "mcp_call") {
    return { mono: true, text: String(inp.server || "") + "/" + String(inp.tool || "") };
  }
  if (tool === "delegate") {
    return { mono: false, text: String(inp.specialist || m.target || "") };
  }
  return { mono: true, text: String(m.target || "") };
}

export function defaultRememberScope(m: PermEvent): string {
  return m && m.dangerous ? "once" : "conversation";
}

function registry(): Record<string, PermHandle> {
  let reg = permCards.value as Record<string, PermHandle> | null;
  if (!reg) {
    reg = Object.create(null) as Record<string, PermHandle>;
    permCards.value = reg;
  }
  return reg;
}

export function markPermCard(
  id: string,
  allowed: boolean,
  scope: string | null,
  resolution: unknown,
): void {
  const reg = registry();
  if (!Object.prototype.hasOwnProperty.call(reg, id)) return;
  const h = reg[id];
  if (!h) return;
  h.resolved = true;
  h.resolution = resolution || null;
  if (h.allow) h.allow.disabled = true;
  if (h.deny) h.deny.disabled = true;
  h.card.classList.add("resolved", allowed ? "allowed" : "denied");
  let st = h.card.querySelector(".perm-status") as HTMLElement | null;
  if (!st) {
    st = el("div", "perm-status");
    h.card.appendChild(st);
  }
  const res = rec(resolution);
  const afterRestart = res.resolution_context === "after_restart";
  st.textContent = afterRestart
    ? allowed
      ? t("perm.status.afterRestartAllowed")
      : t("perm.status.afterRestartDenied")
    : allowed
      ? scope && scope !== "once"
        ? t("perm.status.allowedScope", permScopeCn(scope))
        : t("perm.status.allowed")
      : t("perm.status.denied");
  const oldContinue = h.card.querySelector(".perm-continue");
  if (oldContinue) oldContinue.remove();
  if (allowed && res.requires_continue === true) {
    const cont = el("button", "perm-continue", t("perm.btn.continueReplan")) as HTMLButtonElement;
    cont.onclick = async () => {
      if (running.value) {
        hint(t("toast.running"), false, true);
        return;
      }
      cont.disabled = true;
      try {
        await callLane("send", t("perm.continuePrompt"));
      } finally {
        if (cont.isConnected && !running.value) cont.disabled = false;
      }
    };
    h.card.appendChild(cont);
  }
}

export function renderPermissionCard(m: PermEvent): void {
  const id = String(m.decision_id || "");
  if (!id) return;
  const reg = registry();
  const prev = Object.prototype.hasOwnProperty.call(reg, id) ? reg[id] : undefined;
  if (prev && prev.card && prev.card.isConnected) return;
  let host: HTMLElement | null = null;
  try {
    const st = ensure();
    host = st && st.wrap ? st.wrap : null;
  } catch {
    host = null;
  }
  if (!host && typeof document !== "undefined") {
    host = document.getElementById("messages");
  }
  if (!host) return;
  const card = el("div", "perm-card");
  const head = el("div", "perm-head");
  head.appendChild(iconEl("lock", 15, "perm-ic"));
  head.appendChild(el("span", "perm-title", m.title || t("perm.title.run", m.tool)));
  if (m.sub_agent) head.appendChild(el("span", "perm-badge", t("perm.badge.subAgent")));
  if (m.dangerous) head.appendChild(el("span", "perm-badge danger", t("perm.badge.dangerous")));
  card.appendChild(head);
  card.appendChild(el("div", "perm-sub", t("perm.sub.approvalNeeded")));
  const act = permActionLine(m);
  if (act.text) card.appendChild(el("div", "perm-detail" + (act.mono ? " mono" : ""), act.text));
  const fileReviewKeys: Record<string, string> = {
    credential_path: "perm.review.credential_path",
    dynamic_file_search: "perm.review.dynamic_file_search",
    unreviewable_path: "perm.review.unreviewable_path",
    verification_failed: "perm.review.verification_failed",
  };
  const fileReviewKey = m.policy_review_kind ? fileReviewKeys[m.policy_review_kind] : undefined;
  if (fileReviewKey) {
    card.appendChild(el("div", "perm-sub", t(fileReviewKey)));
    if (m.resolved_file_path) {
      card.appendChild(
        el("div", "perm-detail mono", t("perm.lbl.resolvedPath", m.resolved_file_path)),
      );
    }
  }

  let scope = defaultRememberScope(m);
  card.appendChild(el("div", "perm-lbl", t("perm.lbl.rememberScope")));
  const scRow = el("div", "perm-scope");
  const segs: Record<string, HTMLElement> = {};
  const patWrap = el("div", "perm-pat");
  (m.scopes || ["once", "conversation", "project", "global"]).forEach((s) => {
    const b = el("button", "perm-seg" + (s === scope ? " active" : ""), permScopeCn(s));
    b.onclick = () => {
      scope = s;
      Object.values(segs).forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      patWrap.style.display = scope === "once" ? "none" : "";
    };
    segs[s] = b;
    scRow.appendChild(b);
  });
  card.appendChild(scRow);
  patWrap.style.display = scope === "once" ? "none" : "";

  patWrap.appendChild(el("div", "perm-lbl", t("perm.lbl.rememberRule")));
  const patIn = el("input", "perm-in") as HTMLInputElement;
  patIn.type = "text";
  patIn.value = (m.suggested_patterns && m.suggested_patterns[0]) || m.target || "*";
  patWrap.appendChild(patIn);
  if (m.suggested_patterns && m.suggested_patterns.length > 1) {
    const chips = el("div", "perm-chips");
    m.suggested_patterns.forEach((p) => {
      const c = el("button", "perm-chip", p);
      c.onclick = () => {
        patIn.value = p;
      };
      chips.appendChild(c);
    });
    patWrap.appendChild(chips);
  }
  card.appendChild(patWrap);

  const fb = el("input", "perm-fb") as HTMLInputElement;
  fb.type = "text";
  fb.placeholder = t("perm.placeholder.denyReason");
  card.appendChild(fb);

  const btns = el("div", "perm-btns");
  const allow = el("button", "perm-allow", t("perm.btn.allow")) as HTMLButtonElement;
  const deny = el("button", "perm-deny", t("perm.btn.deny")) as HTMLButtonElement;
  const submit = async (ok: boolean): Promise<void> => {
    allow.disabled = deny.disabled = true;
    const body: Record<string, unknown> = { decision_id: m.decision_id, allow: ok, scope };
    if (scope !== "once") body.pattern = patIn.value.trim() || "*";
    if (!ok && fb.value.trim()) body.message = fb.value.trim();
    let resolution: unknown;
    try {
      resolution = await api(`/frames/${encodeURIComponent(String(m.frame_id))}/decision`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      const recRes = rec(resolution);
      if (!resolution || recRes.ok !== true) {
        throw new Error(String(recRes.error || "permission decision was not accepted"));
      }
    } catch (e) {
      const err = e as { body?: { output_committed?: unknown } } | null;
      const committed = !!(err && err.body && err.body.output_committed);
      if (!committed) allow.disabled = deny.disabled = false;
      hint(t("toast.submitFailed", apiErrorText(e)), true);
      return;
    }
    markPermCard(id, ok, scope, resolution);
  };
  allow.onclick = () => void submit(true);
  deny.onclick = () => void submit(false);
  btns.appendChild(allow);
  btns.appendChild(deny);
  card.appendChild(btns);

  host.appendChild(card);
  reg[id] = { card, allow, deny, resolved: false };
  down();
}

export function resolvePermissionCard(m: PermEvent): void {
  const id = String(m.decision_id || "");
  const reg = registry();
  if (!Object.prototype.hasOwnProperty.call(reg, id)) return;
  const h = reg[id];
  if (h && !h.resolved) markPermCard(id, !!m.allow, m.scope || null, m);
}
