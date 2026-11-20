/**
 * SYNC-27: the manual-cells switch is the daemon's; a window that was already
 * open kept its REPL after another window turned the switch off.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { announceNotebookSwitch, recheckNotebookSwitch } from "./notebook-sync";

function notebook(showing: boolean) {
  const hooks = { notebookOnTurnDone: vi.fn(), replEnabledNow: () => showing };
  return { hooks, load: async () => hooks };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the Notebook follows the daemon's manual-cells switch", () => {
  it("re-reads the Notebook when the switch no longer matches what it shows", async () => {
    const nb = notebook(true);
    expect(await recheckNotebookSwitch(async () => ({ repl_enabled: false }), nb.load)).toBe(true);
    expect(nb.hooks.notebookOnTurnDone).toHaveBeenCalledOnce();
  });

  it("leaves a Notebook that already agrees alone", async () => {
    const nb = notebook(false);
    expect(await recheckNotebookSwitch(async () => ({ repl_enabled: false }), nb.load)).toBe(false);
    expect(nb.hooks.notebookOnTurnDone).not.toHaveBeenCalled();
  });

  it("changes nothing when the switch cannot be read", async () => {
    const nb = notebook(true);
    const failed = await recheckNotebookSwitch(async () => {
      throw new Error("offline");
    }, nb.load);
    expect(failed).toBe(false);
    expect(nb.hooks.notebookOnTurnDone).not.toHaveBeenCalled();
  });

  it("tells this browser's other windows at once", () => {
    const posted: unknown[] = [];
    class Channel {
      constructor(public name: string) {}
      postMessage(message: unknown) {
        posted.push([this.name, message]);
      }
      close() {}
    }
    vi.stubGlobal("BroadcastChannel", Channel);
    announceNotebookSwitch();
    expect(posted).toEqual([["openai4s-notebook-settings", "changed"]]);
  });
});
