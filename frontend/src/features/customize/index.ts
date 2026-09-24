/**
 * F-19 Customize window exports. `openCust` / `custTab` / `telemetryRow` are
 * in tests/webui-contract.md; F-05 reserves them with throwing stubs. This
 * module assigns the real implementations, the same way F-06's bootWs()
 * assigns onEvent.
 */
import { LANG, onLanguageChange } from "../../i18n";
import { custTab, openCust } from "./actions";
import { loadModels } from "./models";
import { customizeOpen, customizeTab } from "./state";
import { telemetryRow } from "./telemetry";
import { hint } from "./host";

export { api, ApiError, apiErrorText, API } from "./api";
export { CUST_TABS, CUST_TAB_ALIASES, normalizeTab, isCustTab, CUST_TAB_I18N } from "./tabs";
export type { CustTab } from "./tabs";
export {
  createTimerLease,
  disposeTimerLease,
  scheduleTimeout,
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
  target.openCust = (tab?: string): void => {
    void openCustomize(tab).catch(settingsLoadFailed);
  };
  target.custTab = (tab: string): void => {
    void ensureCustomizeMounted().then(() => custTab(tab), settingsLoadFailed);
  };
  target.telemetryRow = telemetryRow;
}

let mounting: Promise<void> | null = null;

/**
 * Load and mount the Settings UI once. It is a chunk of its own: most
 * sessions never open Settings, and it was the largest single part of the
 * first-load bundle. A failed load is forgotten, so the next open retries.
 */
export function ensureCustomizeMounted(
  load: () => Promise<{ mountCustomize: () => void }> = () => import("./mount"),
): Promise<void> {
  if (!mounting) {
    const pending = load().then((ui) => ui.mountCustomize());
    mounting = pending;
    pending.catch(() => {
      if (mounting === pending) mounting = null;
    });
  }
  return mounting;
}

/** Tests: forget that the Settings UI was loaded. */
export function resetCustomizeMount(): void {
  mounting = null;
}

/** Open Settings on `tab` once its UI is mounted (`openCust` needs `#cust`). */
export function openCustomize(tab?: string): Promise<void> {
  return ensureCustomizeMounted().then(() => openCust(tab));
}

function settingsLoadFailed(): void {
  hint(LANG === "en" ? "Could not load Settings. Check the connection and try again." : "无法加载设置，请检查网络后重试。", true);
}

/** F-19 boot: window.openCust / custTab / telemetryRow. The modal mounts on first open. */
export function bootCustomize(target: WindowBag = globalThis as unknown as WindowBag): void {
  installCustomize(target);
  // Stores only: `#model-select` renders from them once the shell mounts, so
  // this does not have to wait for the composer to exist.
  void loadModels();
  onLanguageChange(() => {
    if (customizeOpen.value) custTab(customizeTab.value);
  });
}
