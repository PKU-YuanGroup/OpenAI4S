/**
 * A loaded Skill's network limit, as the session timeline shows it.
 *
 * A `host_only` Skill manifest holds every later Host request in the session
 * to its declared domains. The server puts that on two kinds of step:
 *
 * - its own notice card (`kind: "network"`, `output.notice: "skill_network"`),
 *   emitted once per manifest by whichever call bound it;
 * - the Skill's load card (`output.session_network`).
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
  },
  en: {
    "net.notice.title": "Loaded {0}: this session's network is limited to {1}",
    "net.notice.meta": "{0}",
    "net.notice.explain":
      "After loading {0}, this session's network requests (web search, web fetch, downloads and URLs in host.bash commands) can reach only the domains below. A request to any other domain is refused before it is sent, and approving it cannot widen the limit.",
    "net.notice.skills": "Limited by: {0}",
    "net.domains": "Allowed domains ({0})",
    "net.load.meta": "loaded · network limited to {0}",
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
  return { skill, skills: skills.length ? skills : [skill], domains };
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
  return { skill, skills: skills.length ? skills : [skill], domains };
}

/** The localized card title, when the step is a notice. */
export function stepNetworkLabel(step: StepLike): string | null {
  const view = noticeView(step);
  if (!view) return null;
  return netT("net.notice.title", view.skill, domainCount(view.domains.length));
}

/** The localized one-line status, when the step is about the Skill limit. */
export function stepNetworkMeta(step: StepLike): string | null {
  const notice = noticeView(step);
  if (notice) return netT("net.notice.meta", domainCount(notice.domains.length));
  const load = loadView(step);
  if (load) return netT("net.load.meta", domainCount(load.domains.length));
  return null;
}

/** True when the card should carry the network-limit accent. */
export function marksNetworkLimit(step: StepLike): boolean {
  return !!(noticeView(step) || loadView(step));
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
  box.appendChild(domainList(view.domains));
  return box;
}
