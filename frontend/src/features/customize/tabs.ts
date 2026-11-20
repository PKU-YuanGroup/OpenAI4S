/**
 * Customize tab state machine. Port of app.js:11122-11131.
 *
 * Eleven tabs. `agents` is a hidden alias of `specialists` (same renderer
 * in the original dispatch table). `openCust()` with no argument lands on general.
 * `experiments` and `diagnostics` were sections at the bottom of General,
 * which mixed appearance with a third-party judgment layer and raw daemon
 * posture (M-22); they are an operator's pages, not shown to a team member.
 */
import { copyLookup } from "../../i18n/copy";

export const CUST_TABS = [
  "general",
  "skills",
  "specialists",
  "connectors",
  "compute",
  "permissions",
  "network",
  "memory",
  "models",
  "experiments",
  "diagnostics",
] as const;

export type CustTab = (typeof CUST_TABS)[number];

export const CUST_TAB_ALIASES: Record<string, CustTab> = {
  agents: "specialists",
};

export const CUST_TAB_I18N: Record<CustTab, string> = {
  general: "cust.general.title",
  skills: "palette.group.skills",
  specialists: "cust.tab.specialists",
  connectors: "cust.tab.connectors",
  compute: "cust.compute.title",
  permissions: "cust.perm.title",
  network: "cust.network.title",
  memory: "cust.memory.title",
  models: "cust.tab.models",
  experiments: "cust.tab.experiments",
  diagnostics: "cust.tab.diagnostics",
};

/** The two tab names the generated dictionaries do not carry. */
const tabT = copyLookup({
  zh: { "cust.tab.experiments": "实验功能", "cust.tab.diagnostics": "诊断与更新" },
  en: { "cust.tab.experiments": "Experimental", "cust.tab.diagnostics": "Diagnostics" },
});

/** A tab's name in the interface language. */
export function custTabLabel(tab: CustTab): string {
  return tabT(CUST_TAB_I18N[tab]);
}

/** Pages about running this server: an admin's (or a single user's), not a team member's. */
export const OPERATOR_TABS: ReadonlySet<CustTab> = new Set<CustTab>(["experiments", "diagnostics"]);

const TAB_SET: ReadonlySet<string> = new Set(CUST_TABS);

export function isCustTab(value: string): value is CustTab {
  return TAB_SET.has(value);
}

/** `tab || "general"`, then the agents alias, else general. */
export function normalizeTab(tab: unknown): CustTab {
  const raw = typeof tab === "string" && tab ? tab : "general";
  if (isCustTab(raw)) return raw;
  const aliased = CUST_TAB_ALIASES[raw];
  return aliased ?? "general";
}
