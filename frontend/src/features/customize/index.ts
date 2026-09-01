/**
 * F-19 Customize window exports. `openCust` / `custTab` / `telemetryRow` are
 * in tests/webui-contract.md; F-05 reserves them with throwing stubs. This
 * module assigns the real implementations, the same way F-06's bootWs()
 * assigns onEvent.
 */
import { h, render } from "preact";
import { onLanguageChange } from "../../i18n";
import { Customize } from "../../components/customize/Customize";
import { custTab, openCust } from "./actions";
import { customizeOpen, customizeTab } from "./state";
import { telemetryRow } from "./telemetry";

export { api, ApiError, apiErrorText, API } from "./api";
export { CUST_TABS, CUST_TAB_ALIASES, normalizeTab, isCustTab, CUST_TAB_I18N } from "./tabs";
export type { CustTab } from "./tabs";
export {
  createTimerLease,
  disposeTimerLease,
  scheduleTimeout,
  scheduleInterval,
  isLeaseLive,
  liveLeaseCount,
  pendingTimerCount,
  resetTimerLeases,
} from "./timers";
export type { TimerLease } from "./timers";
export { customizeOpen, customizeTab, customizeGeneration, nestedEditor } from "./state";
export type { NestedEditor } from "./state";
export { openCust, custTab, closeCust } from "./actions";
export { telemetryRow } from "./telemetry";
export { startVolcengineKeyPolling } from "./volcengine";
export {
  VOLC_KEY_POLL_FIRST_MS,
  VOLC_KEY_POLL_EVERY_MS,
  VOLC_KEY_POLL_MAX,
} from "./volcengine";

export type WindowBag = Record<string, unknown>;

export function installCustomize(target: WindowBag = globalThis as unknown as WindowBag): void {
  target.openCust = openCust;
  target.custTab = custTab;
  target.telemetryRow = telemetryRow;
}

function bindShellButtons(): void {
  if (typeof document === "undefined") return;
  const dash = document.getElementById("dash-settings");
  if (dash && !dash.dataset.custBound) {
    dash.dataset.custBound = "1";
    dash.addEventListener("click", () => openCust("general"));
  }
  const side = document.getElementById("customize-btn");
  if (side && !side.dataset.custBound) {
    side.dataset.custBound = "1";
    side.addEventListener("click", () => openCust());
  }
  const gear = document.getElementById("settings-gear");
  if (gear && !gear.dataset.custBound) {
    gear.dataset.custBound = "1";
    gear.addEventListener("click", () => openCust());
  }
}

function mountCustomize(): void {
  if (typeof document === "undefined") return;
  if (import.meta.env.MODE === "test") return;
  let host = document.getElementById("cust-root");
  if (!host) {
    host = document.createElement("div");
    host.id = "cust-root";
    document.body.appendChild(host);
  }
  render(h(Customize, {}), host);
}

/** F-19 boot: window.openCust / custTab / telemetryRow, then mount the modal. */
export function bootCustomize(target: WindowBag = globalThis as unknown as WindowBag): void {
  installCustomize(target);
  mountCustomize();
  bindShellButtons();
  onLanguageChange(() => {
    if (customizeOpen.value) custTab(customizeTab.value);
  });
}
