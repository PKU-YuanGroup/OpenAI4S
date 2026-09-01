/**
 * Layout density. Port of app.js:10936 + 11225 (setLayout lives next to General).
 * Persistence key `os-layout` is unchanged.
 */
import { t } from "../../i18n";
import { hint } from "./host";

export const LAYOUT_STORAGE_KEY = "os-layout";
export type LayoutName = "comfortable" | "compact" | "wide";

function isLayoutName(value: string): value is LayoutName {
  return value === "comfortable" || value === "compact" || value === "wide";
}

export function getLayout(): LayoutName {
  try {
    const saved = localStorage.getItem(LAYOUT_STORAGE_KEY);
    if (saved !== null && isLayoutName(saved)) return saved;
  } catch {
    /* private-mode */
  }
  return "comfortable";
}

export function applyLayout(name: string): void {
  if (typeof document === "undefined") return;
  document.body.classList.remove("layout-compact", "layout-wide");
  if (name === "compact") document.body.classList.add("layout-compact");
  else if (name === "wide") document.body.classList.add("layout-wide");
}

export function setLayout(name: string): void {
  const next: LayoutName = isLayoutName(name) ? name : "comfortable";
  try {
    localStorage.setItem(LAYOUT_STORAGE_KEY, next);
  } catch {
    /* private-mode */
  }
  applyLayout(next);
  const label =
    next === "comfortable"
      ? t("cust.general.layout.comfortable")
      : next === "compact"
        ? t("cust.general.layout.compact")
        : t("cust.general.layout.wide");
  hint(t("toast.layout", label));
}
