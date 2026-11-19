/**
 * The runtime-environment picker in the composer's session options menu.
 *
 * The only picker was in the Notebook's REPL panel, which is not rendered
 * unless manual cells are on, and it never let an R environment be chosen.
 * Rows say where an environment comes from: one OpenAI4S ships ("Built-in")
 * or one found on this machine ("Local env", `builtin: false`).
 */
import { currentId } from "../../stores/session";
import { hint, openMenu, type MenuItem } from "../sessions/chrome";
import { el } from "../sessions/dom";
import { notebookFetch } from "./cells";
import { nbT } from "./copy";
import { nbSwitchEnv } from "./kernel";
import type { KernelEnvRow } from "./types";

export type EnvChoice = { envs: KernelEnvRow[]; current: string | null; currentR: string | null };

export function envLanguage(row: KernelEnvRow): "python" | "r" {
  return String(row.language || "").toLowerCase() === "r" ? "r" : "python";
}

/** A Python env can host the Python kernel; an R env runs R cells. */
export function envSelectable(row: KernelEnvRow): boolean {
  return row.runnable === true || envLanguage(row) === "r";
}

/** Where an environment comes from. Discovered ones are no longer called built-in. */
export function envSourceLabel(row: KernelEnvRow): string {
  return row.builtin === false ? nbT("nb.env.local") : nbT("nb.env.builtin");
}

export function envRowLabel(row: KernelEnvRow, current: boolean): string {
  const notable = row.notable && row.notable.length ? " — " + row.notable.slice(0, 4).join("/") : "";
  const runnable = envSelectable(row) ? "" : " · " + nbT("nb.env.notRunnable");
  return (current ? "✓  " : "") + row.name + " · " + envSourceLabel(row) + runnable + notable;
}

export async function readSessionEnvironments(frameId: string): Promise<EnvChoice | null> {
  const data = await notebookFetch(`/frames/${encodeURIComponent(frameId)}/environments`);
  if (!data || !Array.isArray(data.environments)) return null;
  return {
    envs: data.environments as KernelEnvRow[],
    current: data.current == null ? null : String(data.current),
    currentR: data.current_r == null ? null : String(data.current_r),
  };
}

function heading(text: string): MenuItem {
  const node = el("div", "ctx-heading", text);
  node.setAttribute("role", "presentation");
  return { node };
}

export function environmentMenuItems(choice: EnvChoice, pick: (name: string) => void): MenuItem[] {
  const items: MenuItem[] = [];
  const groups: Array<["python" | "r", string, string | null]> = [
    ["python", "Python", choice.current],
    ["r", "R", choice.currentR],
  ];
  for (const [language, title, current] of groups) {
    const rows = choice.envs.filter((row) => envLanguage(row) === language);
    if (!rows.length) continue;
    if (items.length) items.push({ sep: true });
    const inUse = current || nbT("nb.env.default");
    items.push(heading(nbT("nb.env.group", title, inUse)));
    for (const row of rows) {
      items.push({
        label: envRowLabel(row, row.name === current),
        icon: "terminal",
        onClick: () => {
          if (!envSelectable(row)) {
            hint(nbT("nb.env.notRunnableHint", row.name), true);
            return;
          }
          if (row.name !== current) pick(row.name);
        },
      });
    }
  }
  return items;
}

/** "Runtime environment (Python / R)" in the session options menu. */
export function environmentMenuItem(anchor: Element): MenuItem {
  return {
    label: nbT("nb.env.menuItem"),
    icon: "terminal",
    onClick: () => void openEnvironmentMenu(anchor),
  };
}

export async function openEnvironmentMenu(anchor: Element): Promise<void> {
  const frameId = currentId.value;
  if (!frameId) return;
  let choice: EnvChoice | null = null;
  try {
    choice = await readSessionEnvironments(frameId);
  } catch (error) {
    hint(nbT("nb.env.unavailable", (error as Error).message || String(error)), true);
    return;
  }
  if (!choice || currentId.value !== frameId) return;
  openMenu(anchor, environmentMenuItems(choice, (name) => void nbSwitchEnv(name)));
}
