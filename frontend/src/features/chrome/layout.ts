/**
 * Layout density. Port of app.js:10936-10937 applyLayout and 11225 setLayout.
 * localStorage key `os-layout` unchanged: comfortable | compact | wide.
 */

import { t } from "../../i18n/runtime";
import { hint } from "./dom";
import { hostFn, isReady } from "./host";

export const LAYOUT_STORAGE_KEY = "os-layout";
export type LayoutName = "comfortable" | "compact" | "wide";

export function isLayoutName(value: string): value is LayoutName {
  return value === "comfortable" || value === "compact" || value === "wide";
}

export function readStoredLayout(): LayoutName {
  try {
    const saved = localStorage.getItem(LAYOUT_STORAGE_KEY) || "comfortable";
    return isLayoutName(saved) ? saved : "comfortable";
  } catch {
    return "comfortable";
  }
}

/** app.js:10936 — body class only; comfortable is the absence of the others. */
export function applyLayout(name: string): void {
  if (typeof document === "undefined") return;
  document.body.classList.remove("layout-compact", "layout-wide");
  if (name === "compact") document.body.classList.add("layout-compact");
  else if (name === "wide") document.body.classList.add("layout-wide");
}

/** app.js:11225 */
export function setLayout(name: string): void {
  try {
    localStorage.setItem(LAYOUT_STORAGE_KEY, name);
  } catch {
    /* private-mode */
  }
  applyLayout(name);
  const labels: Record<string, string> = {
    comfortable: t("cust.general.layout.comfortable"),
    compact: t("cust.general.layout.compact"),
    wide: t("cust.general.layout.wide"),
  };
  const label = labels[name] || name;
  const hostHint = hostFn("hint");
  if (isReady(hostHint)) hostHint(t("toast.layout", label));
  else hint(t("toast.layout", label));
}
