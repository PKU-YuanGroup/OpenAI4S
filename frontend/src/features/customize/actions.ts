import { closeCustomizeDom, openCustomizeDom } from "./host";
import { beginCustomizeLoad } from "./load";
import {
  customizeGeneration,
  customizeOpen,
  customizeRefresh,
  customizeTab,
  nestedEditor,
} from "./state";
import { normalizeTab, type CustTab } from "./tabs";

function paintTabChrome(tab: CustTab): void {
  if (typeof document === "undefined") return;
  document.querySelectorAll(".cust-tab").forEach((btn) => {
    const on = (btn as HTMLElement).dataset.tab === tab;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  });
}

export function custTab(tab?: string): void {
  const next = normalizeTab(tab);
  customizeTab.value = next;
  customizeGeneration.value += 1;
  beginCustomizeLoad(customizeGeneration.value);
  nestedEditor.value = null;
  paintTabChrome(next);
}

/**
 * After a write, have `tab` re-read its data in place -- if it is still the
 * tab on screen. Writes used to call `custTab()` once they answered, which
 * switched back to their tab, closed any nested editor and remounted it, so
 * a save that landed after the user had moved on pulled them back and
 * dropped what they were typing.
 */
export function refreshCustTab(tab: string): void {
  const target = normalizeTab(tab);
  if (!customizeOpen.value || customizeTab.value !== target) return;
  const counts = customizeRefresh.value;
  customizeRefresh.value = { ...counts, [target]: (counts[target] || 0) + 1 };
}

export function openCust(tab?: string): void {
  customizeOpen.value = true;
  openCustomizeDom();
  custTab(tab || "general");
}

export function closeCust(): void {
  customizeOpen.value = false;
  nestedEditor.value = null;
  closeCustomizeDom();
}
