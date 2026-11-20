import { effect } from "@preact/signals";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { variableInspector } from "../../stores/notebook";
import { resetStoreFields } from "../../stores/signal-field";
import { currentId } from "../../stores/session";
import { setExecutionFetch } from "./api";
import {
  isCodeVariable,
  refreshLoadedInspector,
  refreshVariableInspector,
  renderVariableInspector,
  setPaintInspector,
} from "./inspector";
import { nbT } from "../notebook/copy";

type Inspector = { loading: string | null; results: Record<string, { variables?: Array<{ name?: string }> }> };

beforeEach(() => {
  resetStoreFields();
  currentId.value = "frame-v";
});
afterEach(() => setExecutionFetch(null));

describe("variable inspector state", () => {
  it("publishes each step as a new object, so a subscriber sees loading and results", async () => {
    setExecutionFetch(async () =>
      new Response(
        JSON.stringify({
          root_frame_id: "frame-v",
          branch_id: "frame-v",
          language: "python",
          state: "active",
          available: true,
          state_revision: 3,
          variables: [{ name: "x", type: "int", preview: 1 }],
        }),
      ),
    );
    const seen: Inspector[] = [];
    const stop = effect(() => {
      seen.push(variableInspector.value as Inspector);
    });
    await refreshVariableInspector();
    stop();
    expect(seen.map((state) => state.loading)).toEqual([null, "python", "python", null]);
    expect(seen[seen.length - 1]!.results.python!.variables![0]!.name).toBe("x");
  });
});

// NB-3: the inspector listed every imported module and helper function, each
// with a visible "fingerprint cd316ded…" pill.
describe("variable inspector list", () => {
  class FakeNode {
    children: FakeNode[] = [];
    className = "";
    textContent = "";
    title = "";
    value = "";
    disabled = false;
    attrs: Record<string, string> = {};
    onclick: (() => void) | null = null;
    onchange: (() => void) | null = null;
    constructor(readonly tag: string) {}
    appendChild(child: FakeNode): FakeNode { this.children.push(child); return child; }
    setAttribute(name: string, value: string): void { this.attrs[name] = value; }
    all(): FakeNode[] { return [this, ...this.children.flatMap((child) => child.all())]; }
  }
  const paintOnce = () => renderVariableInspector() as unknown as FakeNode;
  const names = (panel: FakeNode) =>
    panel.all().filter((node) => node.className === "nb-variable-name").map((node) => node.textContent);

  it("lists data variables, keeps code names behind a toggle and the fingerprint in a tooltip", () => {
    vi.stubGlobal("document", { createElement: (tag: string) => new FakeNode(tag) });
    try {
      variableInspector.value = {
        language: "python", loading: null, error: "", request: 1,
        results: {
          python: {
            available: true, state: "active", state_revision: 3,
            variables: [
              { name: "df", type: "DataFrame" },
              { name: "fit", type: "function" },
              { name: "np", type: "module" },
              { name: "n", type: "int", preview: 48, fingerprint: "cd316ded0123456789" },
            ],
          },
        },
      };
      let panel = paintOnce();
      expect(names(panel)).toEqual(["df", "n"]);
      const texts = panel.all().map((node) => node.textContent).join(" ");
      expect(texts).not.toContain("cd316ded");
      const row = panel.all().find((node) => node.className === "nb-variable-row" && node.title)!;
      expect(row.title).toContain("cd316ded");
      const toggle = panel.all().find((node) => node.className.includes("nb-variables-code"))!;
      expect(toggle.textContent).toBe(nbT("nb.variables.showCode", 2));
      // The toggle repaints through the registered painter; the choice persists.
      setPaintInspector(() => { panel = paintOnce(); });
      toggle.onclick!();
      expect(names(panel)).toEqual(["df", "fit", "np", "n"]);
      panel.all().find((node) => node.className.includes("nb-variables-code"))!.onclick!();
      expect(names(panel)).toEqual(["df", "n"]);
      expect(isCodeVariable({ type: "closure" })).toBe(true);
      expect(isCodeVariable({ type: "ndarray" })).toBe(false);
    } finally {
      setPaintInspector(null);
      vi.unstubAllGlobals();
    }
  });
});

// M-77: the list had to be refreshed by hand after every run.
describe("variable inspector after a run", () => {
  it("reads a list the reader loaded again, and leaves an unloaded one alone", async () => {
    const reads: string[] = [];
    setExecutionFetch(async (url) => {
      reads.push(String(url));
      return new Response(
        JSON.stringify({
          root_frame_id: "frame-v",
          branch_id: "frame-v",
          language: "python",
          state: "active",
          available: true,
          state_revision: 4,
          variables: [{ name: "df", type: "DataFrame" }],
        }),
      );
    });
    refreshLoadedInspector();
    await Promise.resolve();
    expect(reads).toEqual([]);

    await refreshVariableInspector();
    expect(reads).toHaveLength(1);
    refreshLoadedInspector();
    await vi.waitFor(() => expect(reads).toHaveLength(2));
    expect(reads[1]).toBe("/api/v1/frames/frame-v/kernel/variables?language=python");
  });
});
