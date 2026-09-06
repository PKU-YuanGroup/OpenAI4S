import { signal } from "@preact/signals";
import type { CustTab } from "./tabs";

/** Whether the Customize modal is visible (`#cust` without `.hidden`). */
export const customizeOpen = signal(false);

/** Active tab id. `custTab("agents")` stores `specialists`. */
export const customizeTab = signal<CustTab>("general");

/**
 * Bumped on every `custTab()` call, including a refresh of the same tab.
 * Tab components use it as a Preact `key` so a refresh remounts (and so any
 * in-flight poll dies with the previous instance).
 */
export const customizeGeneration = signal(0);

export type CustomizeLoadState = "loading" | "ready" | "failed" | "timeout";

/**
 * How far the active tab's first load has got, per generation. The pane
 * annotates the tab from this: a "Loading…" line while it is pending, an
 * error card with Retry when it failed or overran the deadline. The tab body
 * itself stays mounted -- several tabs paint their controls before their
 * data arrives, and a control the user is already using is never torn out.
 */
export const customizeLoad = signal<{
  generation: number;
  state: CustomizeLoadState;
  error: string | null;
}>({ generation: 0, state: "ready", error: null });

export type NestedEditor =
  | { kind: "skill"; name: string | null }
  | { kind: "skill-import" }
  | { kind: "skill-history"; name: string; scope: string; projectId: string | null }
  | { kind: "specialist"; name: string | null }
  | { kind: "connector"; connector: Record<string, unknown> }
  | { kind: "job"; id: string }
  | null;

export const nestedEditor = signal<NestedEditor>(null);
