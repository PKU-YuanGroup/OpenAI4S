/**
 * Bounded first load for a Settings tab. Port of app.js `custTab` /
 * `custLoadFailure` (CUST_LOAD_TIMEOUT_MS): a request that never answers
 * used to leave the workbench tab on its initial state forever -- and that
 * state reads as an answer ("0 skills", "API key missing") rather than as a
 * pending load.
 *
 * Tabs report through the two marks below once their first fetch settles;
 * the pane owns the deadline and the Retry control. A mark from a stale
 * instance is ignored: only the load of the current generation can move.
 */

import { t } from "../../i18n";
import { customizeGeneration, customizeLoad } from "./state";

export const CUST_LOAD_TIMEOUT_MS = 30000;

/** A new tab generation starts its load; called by `custTab()`. */
export function beginCustomizeLoad(generation: number): void {
  customizeLoad.value = { generation, state: "loading", error: null };
}

function current(): boolean {
  const load = customizeLoad.value;
  return load.generation === customizeGeneration.value && load.state === "loading";
}

/** The active tab's first fetch answered (its own error handling aside). */
export function markCustomizeLoaded(): void {
  if (!current()) return;
  customizeLoad.value = { ...customizeLoad.value, state: "ready" };
}

/** The active tab's first fetch failed with `error` (already user-facing). */
export function markCustomizeFailed(error: string): void {
  if (!current()) return;
  customizeLoad.value = { ...customizeLoad.value, state: "failed", error };
}

/** The deadline fired for `generation` while its load was still pending. */
export function markCustomizeTimedOut(generation: number): void {
  if (generation !== customizeLoad.value.generation || !current()) return;
  customizeLoad.value = {
    ...customizeLoad.value,
    state: "timeout",
    error: t("cust.load.timeout"),
  };
}
