import { closeCustomizeDom, openCustomizeDom } from "./host";
import { customizeGeneration, customizeOpen, customizeTab, nestedEditor } from "./state";
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
  nestedEditor.value = null;
  paintTabChrome(next);
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
