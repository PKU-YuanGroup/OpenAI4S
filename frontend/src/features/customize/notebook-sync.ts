/**
 * SYNC-27: the manual-cells switch is the daemon's, not this window's.
 *
 * Turned off in one window, another window that was already open kept its
 * REPL and Run button until it was reloaded; the next run was refused. A
 * change made here is announced to this browser's other windows at once
 * (BroadcastChannel), and every window checks the switch again when it comes
 * back to the front, which also covers a change made in another browser or by
 * another user. Only a switch that differs from what the open Notebook shows
 * makes the Notebook re-read its kernel status.
 */
import { api } from "./api";

/**
 * The Notebook module, loaded when a check needs it: Customize boots early,
 * and a static import pulled the whole Notebook into every Customize import.
 */
type NotebookHooks = { notebookOnTurnDone: () => void; replEnabledNow: () => boolean };
const loadNotebook = (): Promise<NotebookHooks> => import("../notebook");

const CHANNEL = "openai4s-notebook-settings";

/** Tell this browser's other windows that the switch changed. */
export function announceNotebookSwitch(): void {
  try {
    const channel = new BroadcastChannel(CHANNEL);
    channel.postMessage("changed");
    channel.close();
  } catch {
    /* no BroadcastChannel: the focus check still catches up */
  }
}

/** Re-read the switch; refresh the Notebook only when it no longer matches. */
export async function recheckNotebookSwitch(
  read: () => Promise<Record<string, unknown>> = () => api("/notebook/settings"),
  notebook: () => Promise<NotebookHooks> = loadNotebook,
): Promise<boolean> {
  try {
    const [settings, nb] = await Promise.all([read(), notebook()]);
    if ((settings.repl_enabled === true) === nb.replEnabledNow()) return false;
    nb.notebookOnTurnDone();
    return true;
  } catch {
    return false;
  }
}

let watching = false;

/** Install once: the focus/visibility check and the channel listener. */
export function watchNotebookSwitch(): void {
  if (watching || typeof window === "undefined" || typeof document === "undefined") return;
  watching = true;
  const check = () => void recheckNotebookSwitch();
  window.addEventListener("focus", check);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") check();
  });
  try {
    const channel = new BroadcastChannel(CHANNEL);
    channel.onmessage = () => {
      void loadNotebook().then((nb) => nb.notebookOnTurnDone(), () => undefined);
    };
  } catch {
    /* the focus check alone */
  }
}
