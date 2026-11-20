/**
 * A loaded Skill's network limit, as the session timeline shows it.
 *
 * A `host_only` Skill manifest holds every later Host request in the session
 * to its declared domains. The server puts that on two kinds of step:
 *
 * - its own notice card (`kind: "network"`, `output.notice: "skill_network"`),
 *   emitted once per manifest by whichever call bound it;
 * - the Skill's load card (`output.session_network`);
 * - a request the limit refused (`output.blocked`), which is refused before
 *   any approval is asked for.
 *
 * The server's summary and title are English; these helpers give the card
 * its words in the UI language and the domain list on expand.
 */

import { copyLookup, type CopyTable } from "../../i18n/copy";
import { LANG } from "../../i18n/runtime";
import { el } from "../messages/dom";

const COPY: CopyTable = {
  zh: {
    "net.notice.title": "已加载 {0}：本会话联网限定为 {1} 个域名",
    "net.notice.meta": "{0} 个域名",
    "net.notice.explain":
      "加载 {0} 后，本会话之后的联网请求（网页检索、网页读取、下载以及 host.bash 命令中的网址）只能访问下列域名；访问其他域名的请求会在发出前被拒绝，批准也无法放宽这一限制。",
    "net.notice.skills": "限制来自技能：{0}",
    "net.domains": "允许的域名（{0} 个）",
    "net.load.meta": "已加载 · 本会话联网限定为 {0} 个域名",
    "net.blocked.meta": "被技能的联网限制拦下",
    "net.blocked.explain": "{0} 把本会话联网限定为 {1} 个域名，{2} 不在其中，这个请求没有发出，也不需要你批准。",
    "net.blocked.unnamed": "已加载的技能",
    "net.blocked.reason": "原始原因",
    "net.blocked.search":
      "{0} 把本会话联网限定为 {1} 个域名，已启用的搜索引擎都不在其中，这次检索没有发出——这不是「网上没有结果」。",
    "net.notice.lost": "本会话因此不能再使用这些内置连接器：{0}。",
    "net.blocked.next": "本会话的这个限制无法解除。要访问其他网站，请在新会话中继续——新会话没有加载这个技能，你的上一条消息会放进输入框。",
    "net.blocked.newSession": "在新会话中继续",
  },
  en: {
    "net.notice.title": "Loaded {0}: this session's network is limited to {1}",
    "net.notice.meta": "{0}",
    "net.notice.explain":
      "After loading {0}, this session's network requests (web search, web fetch, downloads and URLs in host.bash commands) can reach only the domains below. A request to any other domain is refused before it is sent, and approving it cannot widen the limit.",
    "net.notice.skills": "Limited by: {0}",
    "net.domains": "Allowed domains ({0})",
    "net.load.meta": "loaded · network limited to {0}",
    "net.blocked.meta": "blocked by a Skill's network limit",
    "net.blocked.explain":
      "{0} limits this session's network to {1}; {2} is not one of them, so the request was not sent and needs no approval.",
    "net.blocked.unnamed": "A loaded Skill",
    "net.blocked.reason": "Original reason",
    "net.blocked.search":
      "{0} limits this session's network to {1}, and none of the enabled search engines is one of them, so the search was not sent. This is not \"no results\".",
    "net.notice.lost": "This session can no longer use these built-in connectors: {0}.",
    "net.blocked.next":
      "This session's limit cannot be lifted. To reach other sites, continue in a new session: it has not loaded this Skill, and your last message is put in the message box.",
    "net.blocked.newSession": "Continue in a new session",
  },
};

export const netT = copyLookup(COPY);

/** "18 domains" in English; the Chinese strings carry their own counter. */
function domainCount(n: number): string {
  if (LANG !== "en") return String(n);
  return n === 1 ? "1 domain" : `${n} domains`;
}

export type SkillNetworkView = {
  /** The Skill whose load this card is about. */
  skill: string;
  /** Every Skill narrowing the session, in load order. */
  skills: string[];
  /** The union the session is held to. */
  domains: string[];
  /** Built-in connectors the limit takes away (SCI-14); empty when none or unknown. */
  unavailable?: string[];
};

type StepLike = { kind?: string; output?: unknown; input?: unknown };

function rec(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : [];
}

/** The notice card's view, or null for any other step. */
export function noticeView(step: StepLike): SkillNetworkView | null {
  const out = rec(step.output);
  if (step.kind !== "network" || out.notice !== "skill_network") return null;
  const domains = strings(out.domains);
  const skills = strings(out.skills);
  const skill = String(out.skill || skills[skills.length - 1] || "");
  return {
    skill,
    skills: skills.length ? skills : [skill],
    domains,
    unavailable: strings(out.unavailable_connectors),
  };
}

/** A Skill load card that narrowed the session, or null. */
export function loadView(step: StepLike): SkillNetworkView | null {
  if (step.kind !== "skill") return null;
  const out = rec(step.output);
  const session = rec(out.session_network);
  const domains = strings(session.domains);
  if (!domains.length) return null;
  const skills = strings(session.skills);
  const skill = String(out.name || rec(step.input).name || skills[0] || "");
  return {
    skill,
    skills: skills.length ? skills : [skill],
    domains,
    unavailable: strings(session.unavailable_connectors),
  };
}

export type BlockedView = SkillNetworkView & {
  /** The refused destination; empty when the refusal named none. */
  destination: string;
  /** A web search none of whose engines the limit allows. */
  search: boolean;
};

/** A request the session's Skill limit refused, or null. */
export function blockedView(step: StepLike): BlockedView | null {
  const blocked = rec(rec(step.output).blocked);
  if (blocked.by !== "skill_network") return null;
  const skills = strings(blocked.skills);
  return {
    skill: skills.join(", "),
    skills,
    domains: strings(blocked.domains),
    destination: String(blocked.destination || ""),
    search: blocked.search === true,
  };
}

/** The localized card title, when the step is a notice. */
export function stepNetworkLabel(step: StepLike): string | null {
  const view = noticeView(step);
  if (!view) return null;
  return netT("net.notice.title", view.skill, domainCount(view.domains.length));
}

/** The localized one-line status, when the step is about the Skill limit. */
export function stepNetworkMeta(step: StepLike): string | null {
  if (blockedView(step)) return netT("net.blocked.meta");
  const notice = noticeView(step);
  if (notice) return netT("net.notice.meta", domainCount(notice.domains.length));
  const load = loadView(step);
  if (load) return netT("net.load.meta", domainCount(load.domains.length));
  return null;
}

/** True when the card should carry the network-limit accent. */
export function marksNetworkLimit(step: StepLike): boolean {
  return !!(noticeView(step) || loadView(step) || blockedView(step));
}

function domainList(domains: string[]): HTMLElement {
  const wrap = el("div", "s-net-list");
  wrap.appendChild(el("div", "s-net-head", netT("net.domains", domains.length)));
  const chips = el("div", "s-net-doms");
  domains.forEach((domain) => chips.appendChild(el("span", "s-net-dom", domain)));
  wrap.appendChild(chips);
  return wrap;
}

/** Explanation plus the allowed domains, for a notice or a load card. */
export function networkNoticeBody(view: SkillNetworkView): HTMLElement {
  const box = el("div", "s-net");
  box.appendChild(el("div", "s-net-text", netT("net.notice.explain", view.skill)));
  if (view.skills.length > 1) {
    box.appendChild(el("div", "s-net-text", netT("net.notice.skills", view.skills.join(", "))));
  }
  // SCI-14: what the limit takes away, before the first refused lookup says so.
  if (view.unavailable && view.unavailable.length) {
    box.appendChild(el("div", "s-net-text s-net-lost", netT("net.notice.lost", view.unavailable.join(", "))));
  }
  box.appendChild(domainList(view.domains));
  return box;
}

/** The newest user message on screen, for the new session's message box. */
function lastUserMessage(): string {
  if (typeof document === "undefined") return "";
  const bubbles = document.querySelectorAll("#messages .msg.user .bubble");
  const last = bubbles.length ? bubbles[bubbles.length - 1] : null;
  return String((last && last.textContent) || "").trim();
}

/**
 * SCI-14: the way out of a limit that cannot be lifted. A new session in the
 * same project has no Skill bound, and the user's last request goes into its
 * message box (unsent) unless the box already holds something.
 */
export async function continueInNewSession(): Promise<void> {
  const draft = lastUserMessage();
  const open = (globalThis as Record<string, unknown>).newSession;
  if (typeof open !== "function") return;
  await (open as () => Promise<void> | void)();
  const composer = typeof document !== "undefined" ? (document.getElementById("composer") as HTMLTextAreaElement | null) : null;
  if (!composer) return;
  if (draft && !composer.value.trim()) {
    composer.value = draft;
    composer.dispatchEvent(new Event("input", { bubbles: true }));
  }
  composer.focus();
}

/** What a refused request's card says, with the server's reason kept below. */
export function blockedBody(view: BlockedView, reason: string): HTMLElement {
  const box = el("div", "s-net s-net-blocked");
  const who = view.skill || netT("net.blocked.unnamed");
  const count = domainCount(view.domains.length);
  const text = view.search
    ? netT("net.blocked.search", who, count)
    : netT("net.blocked.explain", who, count, view.destination || "?");
  box.appendChild(el("div", "s-net-text", text));
  // SCI-14: what to do now. The "why" above was clear; the way out was not.
  const next = el("div", "s-net-next");
  next.appendChild(el("div", "s-net-text", netT("net.blocked.next")));
  const again = el("button", "outline-btn small s-net-new", netT("net.blocked.newSession")) as HTMLButtonElement;
  again.type = "button";
  again.onclick = (event) => {
    // The card itself toggles open on click; this is not a toggle.
    event.stopPropagation();
    void continueInNewSession();
  };
  next.appendChild(again);
  box.appendChild(next);
  if (view.domains.length) box.appendChild(domainList(view.domains));
  if (reason) {
    box.appendChild(el("div", "s-net-head", netT("net.blocked.reason")));
    const pre = el("pre", "s-pre d-del");
    pre.textContent = reason.slice(0, 14000);
    box.appendChild(pre);
  }
  return box;
}
