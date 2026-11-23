/**
 * Workbench-wide labels a screen reader or a keyboard user meets: the skip
 * link and landmark names (SYNC-21, SYNC-30). Feature-local because the
 * generated `i18n/en.ts` / `zh.ts` are byte-checked extracts.
 */

import { copyLookup, type CopyTable } from "../../i18n/copy";

const COPY: CopyTable = {
  zh: {
    "a11y.skip": "跳到主要内容",
    "a11y.settingsSections": "设置分区",
  },
  en: {
    "a11y.skip": "Skip to content",
    "a11y.settingsSections": "Settings sections",
  },
};

export const a11yT = copyLookup(COPY);

/**
 * SYNC-30: the skip link's target is the view on screen -- the conversation,
 * else the dashboard -- and it takes focus, so the next Tab starts there
 * instead of at the top of the page again.
 */
export function skipToContent(event: Event): void {
  if (typeof document === "undefined") return;
  const target = ["messages", "dashboard"]
    .map((id) => document.getElementById(id))
    .find((node): node is HTMLElement => !!node && node.getClientRects().length > 0);
  if (!target) return;
  event.preventDefault();
  if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
  target.focus();
}
