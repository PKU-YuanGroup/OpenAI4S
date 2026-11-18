/**
 * Permission gate cards. Port of app.js:6460-6614.
 *
 * DOM class names (`.perm-card`, `.perm-card.resolved`, `.allowed`, `.denied`)
 * are frozen — they are in the E2E contract. Registry is null-proto so keys
 * like `__proto__` cannot pollute.
 */

import { LANG, t } from "../../i18n/runtime";
import { permCards, running } from "../../stores/stream";
import { el } from "../messages/dom";
import { down } from "../messages/scroll";
import { adoptIntoLiveStream, ensure, placeInLiveStream, type LiveStream } from "../messages/stream";
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
  /** Pick-one options: the chosen id is the decision's `pattern`. */
  choices?: Array<{ id?: string; label?: string }>;
  allow?: unknown;
  scope?: string;
};

type PermHandle = {
  card: HTMLElement;
  allow: HTMLButtonElement;
  deny: HTMLButtonElement;
  resolved: boolean;
  resolution?: unknown;
  /** What `markPermCard` was told, so a language switch can say it again. */
  allowed?: boolean;
  scope?: string | null;
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
  if (tool === "web_search" || tool === SEARCH_FALLBACK) {
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
  if (tool === "env_use") {
    return { mono: true, text: String(inp.name || m.target || "") };
  }
  if (tool === "dynamic_tool_define") {
    return { mono: true, text: String(inp.name || m.target || "") };
  }
  return { mono: true, text: String(m.target || "") };
}

/** Feature-local copy for the context a card shows before the user decides. */
const CONTEXT_COPY: Record<"en" | "zh", Record<string, string>> = {
  en: {
    installInto: "Installs into: {0}",
    installKeep:
      "Packages already in this environment keep their versions; an install that would change one is refused.",
    localEnv:
      "This is one of your own conda environments, not one OpenAI4S set up. If you allow it, the following cells run with its interpreter.",
    runsIn: "Runs in the {0} environment (python → {1})",
    cwd: "Working directory: {0}",
    sandboxOn: "Kernel sandbox: on ({0}); network {1}",
    sandboxOff: "Kernel sandbox: not enforced; the command can reach the network and files outside the workspace",
    networkBlocked: "blocked",
    networkAllowed: "allowed",
    toolDescription: "What it does",
    toolCode: "Code that will run ({0} characters)",
    toolInput: "Input schema",
  },
  zh: {
    installInto: "安装到：{0}",
    installKeep: "这个环境里已安装的包保持原版本；需要改动它们的安装会被拒绝。",
    localEnv:
      "这是你本机的 conda 环境，不是 OpenAI4S 自带的环境。允许后，后续单元会用它的解释器运行。",
    runsIn: "运行环境：{0}（python → {1}）",
    cwd: "工作目录：{0}",
    sandboxOn: "内核沙盒：已启用（{0}），网络{1}",
    sandboxOff: "内核沙盒：未强制，命令可以联网、可以访问工作区以外的文件",
    networkBlocked: "已阻止",
    networkAllowed: "可访问",
    toolDescription: "用途",
    toolCode: "将要运行的代码（{0} 个字符）",
    toolInput: "输入参数结构",
  },
};

function ctx(key: string, ...args: string[]): string {
  let text = CONTEXT_COPY[LANG === "zh" ? "zh" : "en"][key] || key;
  args.forEach((value, index) => {
    text = text.split("{" + index + "}").join(value);
  });
  return text;
}

/**
 * Plain-text lines and code blocks a card needs so the user knows what they
 * are approving: which environment an install or shell command touches,
 * whether the kernel sandbox holds, and the full code of a dynamic tool.
 */
export function permContext(m: PermEvent): { lines: string[]; code: Array<{ label: string; text: string }> } {
  const inp = rec(m.input);
  const lines: string[] = [];
  const code: Array<{ label: string; text: string }> = [];
  if (m.tool === "env_setup") {
    const env = String(inp.environment || "");
    if (env) {
      lines.push(ctx("installInto", inp.interpreter ? `${env} (${String(inp.interpreter)})` : env));
    }
    lines.push(ctx("installKeep"));
  } else if (m.tool === "env_use") {
    if (inp.local === true) lines.push(ctx("localEnv"));
  } else if (m.tool === "bash") {
    if (inp.environment) {
      lines.push(ctx("runsIn", String(inp.environment), String(inp.python || "?")));
    }
    if (inp.cwd) lines.push(ctx("cwd", String(inp.cwd)));
    const sandbox = rec(inp.sandbox);
    if (Object.keys(sandbox).length) {
      if (sandbox.enforced === true) {
        lines.push(
          ctx(
            "sandboxOn",
            String(sandbox.backend || "sandbox"),
            ctx(sandbox.network === "blocked" ? "networkBlocked" : "networkAllowed"),
          ),
        );
      } else {
        lines.push(ctx("sandboxOff"));
      }
    }
  } else if (m.tool === "dynamic_tool_define") {
    if (inp.description) lines.push(ctx("toolDescription") + ": " + String(inp.description));
    const implementation = typeof inp.implementation === "string" ? inp.implementation : "";
    if (implementation) {
      code.push({ label: ctx("toolCode", String(implementation.length)), text: implementation });
    }
    if (inp.input_schema && typeof inp.input_schema === "object") {
      code.push({ label: ctx("toolInput"), text: JSON.stringify(inp.input_schema, null, 2) });
    }
  }
  return { lines, code };
}

export function defaultRememberScope(m: PermEvent): string {
  return m && m.dangerous ? "once" : "conversation";
}

/** Feature-local copy: `i18n/en.ts` / `zh.ts` are generated extracts of app.js. */
const RESOLVING_COPY: Record<"en" | "zh", string> = {
  en: "Decision received; it is still being recorded.",
  zh: "已收到决定，正在记录。",
};

/** The Host's Doubao-quota fallback prompt (`openai4s/host/search.py`). */
const SEARCH_FALLBACK = "web_search_fallback";
const CHOICE_COPY: Record<"en" | "zh", Record<string, string>> = {
  en: {
    title: "Doubao search quota is spent",
    sub: "Finish this search with another engine? Pick one and allow; deny and this search returns the quota error.",
    choose: "Use instead",
  },
  zh: {
    title: "豆包搜索配额已用尽",
    sub: "要换用其他搜索引擎完成这次搜索吗？选一个引擎后允许；拒绝则这次搜索直接返回配额错误。",
    choose: "换用",
  },
};

function choiceCopy(key: string): string {
  return CHOICE_COPY[LANG === "zh" ? "zh" : "en"][key] || key;
}

/** Outcomes a stored request can have that the live allow/deny copy cannot say. */
const OUTCOME_COPY: Record<"en" | "zh", Record<string, string>> = {
  en: {
    timed_out: "Timed out · not run",
    cancelled: "Cancelled with the turn · not run",
    failed: "Could not be recorded · not run",
    reason: "Reason: {0}",
    decidedElsewhere: "This approval was already decided elsewhere; the card now shows that decision.",
  },
  zh: {
    timed_out: "已超时 · 未执行",
    cancelled: "已随任务取消 · 未执行",
    failed: "未能记录 · 未执行",
    reason: "原因：{0}",
    decidedElsewhere: "该审批已在别处处理，卡片已更新为该决定。",
  },
};

function outcomeCopy(key: string, arg = ""): string {
  return (OUTCOME_COPY[LANG === "zh" ? "zh" : "en"][key] || key).replace("{0}", arg);
}

/**
 * A fixed card label that follows a language switch: `data-i18n` makes the
 * switch's static repaint (`applyStaticI18n`) re-translate it in place, so an
 * open card keeps whatever the user already typed or picked.
 */
function labelEl(tag: string, className: string, key: string): HTMLElement {
  const node = el(tag, className, t(key));
  node.setAttribute("data-i18n", key);
  return node;
}

/**
 * Say every decided card's outcome again in the language just applied. Its
 * status line is composed (a scope, a reason, a feature-local outcome), so the
 * static repaint cannot re-translate it: "Allowed" stayed "已允许".
 */
export function relabelPermissionCards(): void {
  const reg = registry();
  for (const id of Object.keys(reg)) {
    const h = reg[id];
    if (h && h.resolved && h.card.isConnected) markPermCard(id, !!h.allowed, h.scope ?? null, h.resolution);
  }
}

function statusLine(card: HTMLElement): HTMLElement {
  let st = card.querySelector(".perm-status") as HTMLElement | null;
  if (!st) {
    st = el("div", "perm-status");
    card.appendChild(st);
  }
  return st;
}

/** Accepted, commit in flight: the buttons stay down until permission_resolved. */
function showResolving(card: HTMLElement): void {
  statusLine(card).textContent = RESOLVING_COPY[LANG === "zh" ? "zh" : "en"];
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
  h.allowed = allowed;
  h.scope = scope;
  if (h.allow) h.allow.disabled = true;
  if (h.deny) h.deny.disabled = true;
  h.card.classList.add("resolved", allowed ? "allowed" : "denied");
  const st = statusLine(h.card);
  const res = rec(resolution);
  const afterRestart = res.resolution_context === "after_restart";
  const outcome = !allowed && typeof res.state === "string" &&
    ["timed_out", "cancelled", "failed"].includes(res.state) ? res.state : "";
  st.textContent = outcome
    ? outcomeCopy(outcome)
    : afterRestart
    ? allowed
      ? t("perm.status.afterRestartAllowed")
      : t("perm.status.afterRestartDenied")
    : allowed
      ? scope && scope !== "once"
        ? t("perm.status.allowedScope", permScopeCn(scope))
        : t("perm.status.allowed")
      : t("perm.status.denied");
  // A denial's reason is part of the decision: every tab, and a reload, shows
  // the one the deciding user typed (it used to survive only in that tab's
  // input box).
  const reason = !allowed && !outcome && typeof res.message === "string" ? res.message.trim() : "";
  h.card.querySelector(".perm-reason")?.remove();
  if (reason) st.after(el("div", "perm-reason", outcomeCopy("reason", reason)));
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
  if (prev && prev.card && prev.card.isConnected) {
    // Rendered from stored history by a reopen; the replayed (or re-sent
    // pending) event places it in the running turn, as a step's does.
    if (adoptIntoLiveStream(prev.card)) down();
    return;
  }
  let st: LiveStream | null = null;
  try {
    st = ensure();
  } catch {
    st = null;
  }
  const handle = buildPermissionCard(m);
  if (st && st.wrap) {
    // The prose before the request stays above the card and what follows it
    // starts below, matching the stored order (the card used to sit under
    // the turn's text so far, and later text kept growing above it).
    placeInLiveStream(handle.card, st);
  } else {
    const host = typeof document !== "undefined" ? document.getElementById("messages") : null;
    if (!host) return;
    host.appendChild(handle.card);
  }
  reg[id] = handle;
  down();
}

/** A durable approval request as `GET /frames/{fid}/approvals` returns it. */
export type StoredApproval = PermEvent & {
  state?: string | null;
  message?: string | null;
  created_at?: string | null;
  resolution_context?: string | null;
};

/**
 * One stored decision, where history places it. A reload used to drop every
 * card -- the live events were their only source -- so a transcript kept no
 * trace of what was allowed or denied, and each tab showed only the cards it
 * had been open for. A pending one stays answerable.
 */
export function renderStoredPermission(
  stored: StoredApproval,
  target?: ParentNode | null,
): HTMLElement | null {
  const id = String(stored.decision_id || "");
  if (!id) return null;
  const handle = buildPermissionCard(stored);
  handle.card.dataset.ts = String(new Date(String(stored.created_at || "")).getTime() || 0);
  registry()[id] = handle;
  const state = String(stored.state || "pending");
  if (state !== "pending") markPermCard(id, state === "allowed", stored.scope || null, stored);
  const host = target || (typeof document !== "undefined" ? document.getElementById("messages") : null);
  host?.appendChild(handle.card);
  return handle.card;
}

function buildPermissionCard(m: PermEvent): PermHandle {
  const id = String(m.decision_id || "");
  const card = el("div", "perm-card");
  card.dataset.decisionId = id;
  const head = el("div", "perm-head");
  head.appendChild(iconEl("lock", 15, "perm-ic"));
  const fallback = m.tool === SEARCH_FALLBACK;
  head.appendChild(
    el(
      "span",
      "perm-title",
      fallback ? choiceCopy("title") : m.title || t("perm.title.run", m.tool),
    ),
  );
  if (m.sub_agent) head.appendChild(labelEl("span", "perm-badge", "perm.badge.subAgent"));
  if (m.dangerous) head.appendChild(labelEl("span", "perm-badge danger", "perm.badge.dangerous"));
  card.appendChild(head);
  card.appendChild(
    fallback ? el("div", "perm-sub", choiceCopy("sub")) : labelEl("div", "perm-sub", "perm.sub.approvalNeeded"),
  );
  const act = permActionLine(m);
  if (act.text) card.appendChild(el("div", "perm-detail" + (act.mono ? " mono" : ""), act.text));
  const context = permContext(m);
  context.lines.forEach((line) => card.appendChild(el("div", "perm-ctx", line)));
  context.code.forEach((block, index) => {
    const wrap = el("details", "perm-codewrap") as HTMLDetailsElement;
    // The code a dynamic tool will run is the decision itself: open it.
    if (index === 0) wrap.open = true;
    wrap.appendChild(el("summary", "perm-lbl", block.label));
    wrap.appendChild(el("pre", "perm-code", block.text));
    card.appendChild(wrap);
  });
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

  const choices = (m.choices || []).filter((c) => c && c.id);
  let choice = choices.length ? String(choices[0]?.id) : "";
  if (choices.length) {
    card.appendChild(el("div", "perm-lbl", choiceCopy("choose")));
    const choiceRow = el("div", "perm-scope perm-choices");
    const choiceSegs: HTMLElement[] = [];
    choices.forEach((c) => {
      const id = String(c.id);
      const b = el("button", "perm-seg" + (id === choice ? " active" : ""), c.label || id);
      b.setAttribute("data-choice", id);
      b.onclick = () => {
        choice = id;
        choiceSegs.forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
      };
      choiceSegs.push(b);
      choiceRow.appendChild(b);
    });
    card.appendChild(choiceRow);
  }

  let scope = defaultRememberScope(m);
  card.appendChild(labelEl("div", "perm-lbl", "perm.lbl.rememberScope"));
  const scRow = el("div", "perm-scope");
  const segs: Record<string, HTMLElement> = {};
  const patWrap = el("div", "perm-pat");
  (m.scopes || ["once", "conversation", "project", "global"]).forEach((s) => {
    const b = PERM_SCOPE_KEYS[s]
      ? labelEl("button", "perm-seg" + (s === scope ? " active" : ""), PERM_SCOPE_KEYS[s])
      : el("button", "perm-seg" + (s === scope ? " active" : ""), permScopeCn(s));
    b.onclick = () => {
      scope = s;
      Object.values(segs).forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      patWrap.style.display = scope === "once" || choices.length ? "none" : "";
    };
    segs[s] = b;
    scRow.appendChild(b);
  });
  card.appendChild(scRow);
  // With choices the remembered rule's pattern IS the choice, so no free text.
  patWrap.style.display = scope === "once" || choices.length ? "none" : "";

  patWrap.appendChild(labelEl("div", "perm-lbl", "perm.lbl.rememberRule"));
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
  fb.setAttribute("data-i18n-ph", "perm.placeholder.denyReason");
  card.appendChild(fb);

  const btns = el("div", "perm-btns");
  const allow = labelEl("button", "perm-allow", "perm.btn.allow") as HTMLButtonElement;
  const deny = labelEl("button", "perm-deny", "perm.btn.deny") as HTMLButtonElement;
  const submit = async (ok: boolean): Promise<void> => {
    allow.disabled = deny.disabled = true;
    const body: Record<string, unknown> = { decision_id: m.decision_id, allow: ok, scope };
    if (choices.length) body.pattern = choice;
    else if (scope !== "once") body.pattern = patIn.value.trim() || "*";
    if (!ok && fb.value.trim()) body.message = fb.value.trim();
    let resolution: unknown;
    try {
      resolution = await api(`/frames/${encodeURIComponent(String(m.frame_id))}/decision`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      const recRes = rec(resolution);
      if (recRes.code === "decision_resolving") {
        // 202: the decision was accepted and its durable commit is still in
        // flight (the server bounds its own wait). Not a failure: offering
        // the buttons again would re-submit a decision about to commit (409).
        // The tool thread's permission_resolved event settles the card.
        showResolving(card);
        return;
      }
      if (!resolution || recRes.ok !== true) {
        throw new Error(String(recRes.error || "permission decision was not accepted"));
      }
    } catch (e) {
      const err = e as { body?: { output_committed?: unknown }; code?: unknown } | null;
      if (err && (err.code === "decision_already_resolved" || err.code === "decision_expired")) {
        // Answered in another tab (or timed out) while this card still looked
        // open: show what was decided rather than offering the buttons again.
        if (await reconcilePermissionCards(String(m.frame_id || ""), [id])) {
          hint(outcomeCopy("decidedElsewhere"));
          return;
        }
      }
      const committed = !!(err && err.body && err.body.output_committed);
      if (!committed) allow.disabled = deny.disabled = false;
      hint(t("toast.submitFailed", apiErrorText(e)), true);
      return;
    }
    // The reason box hides with the buttons; the card keeps what was typed.
    markPermCard(id, ok, scope, ok ? resolution : { ...rec(resolution), message: body.message });
  };
  allow.onclick = () => void submit(true);
  deny.onclick = () => void submit(false);
  btns.appendChild(allow);
  btns.appendChild(deny);
  card.appendChild(btns);
  return { card, allow, deny, resolved: false };
}

export function resolvePermissionCard(m: PermEvent): void {
  const id = String(m.decision_id || "");
  const reg = registry();
  if (!Object.prototype.hasOwnProperty.call(reg, id)) return;
  const h = reg[id];
  if (h && !h.resolved) markPermCard(id, !!m.allow, m.scope || null, m);
}

/**
 * Settle the cards this tab still shows as open from the stored decisions.
 * A decision made while the socket was down arrives neither in the replay
 * nor in the pending-approval snapshot, so after a reconnect the card kept
 * its buttons and an Allow answered "decision is already allowed". `only`
 * limits the check to those cards. True when at least one card settled.
 */
export async function reconcilePermissionCards(fid: string, only?: string[]): Promise<boolean> {
  const open = (): string[] => {
    const reg = registry();
    return Object.keys(reg).filter((id) => {
      const h = reg[id];
      return !!h && !h.resolved && (!only || only.includes(id));
    });
  };
  if (!fid || !open().length) return false;
  let rows: unknown;
  try {
    rows = await api(`/frames/${encodeURIComponent(fid)}/approvals`);
  } catch {
    return false;
  }
  const list = rec(rows).approvals;
  if (!Array.isArray(list)) return false;
  const pending = new Set(open());
  let settled = false;
  for (const raw of list) {
    const row = rec(raw);
    const id = String(row.decision_id || "");
    const state = String(row.state || "");
    if (!pending.has(id) || !state || state === "pending") continue;
    markPermCard(id, state === "allowed", typeof row.scope === "string" ? row.scope : null, row);
    settled = true;
  }
  return settled;
}
