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

export type NestedEditor =
  | { kind: "skill"; name: string | null }
  | { kind: "skill-import" }
  | { kind: "skill-history"; name: string; scope: string; projectId: string | null }
  | { kind: "specialist"; name: string | null }
  | { kind: "connector"; connector: Record<string, unknown> }
  | { kind: "job"; id: string }
  | null;

export const nestedEditor = signal<NestedEditor>(null);
