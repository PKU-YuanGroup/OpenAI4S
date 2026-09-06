/**
 * Customize tab state machine. Port of app.js:11122-11131.
 *
 * Nine visible tabs. `agents` is a hidden alias of `specialists` (same renderer
 * in the original dispatch table). `openCust()` with no argument lands on general.
 */

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
};

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
