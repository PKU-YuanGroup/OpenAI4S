/**
 * Notebook dock components, called as plain functions: the suite runs in node
 * with no DOM, so a test reads the VNode tree Preact would commit. Hooks are
 * stubbed to their initial values.
 *
 * Not named Notebook.test.tsx: on a case-insensitive disk that shares a base
 * name with notebook.test.ts, and tsc then silently leaves one of them out.
 */
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("preact/hooks", () => ({
  useRef: (initial: unknown) => ({ current: initial }),
  useLayoutEffect: () => undefined,
  useEffect: () => undefined,
  useState: (initial: unknown) => [initial, () => undefined],
}));

import { effect } from "@preact/signals";
import { i18nReady, t } from "../../i18n/runtime";
import { cells, liveCells } from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { notebookDisplayEntries } from "./cells";
import { invalidateKernelCache, kernelView } from "./kernel";
import { CellList, CellOutput, NotebookDock, StatusStrip } from "./Notebook";

type VNode = {
  type?: unknown;
  key?: unknown;
  props?: Record<string, unknown> & { children?: unknown };
};

/** Expand plain function components down to host elements and read the text. */
function textOf(node: unknown): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  const vnode = node as VNode;
  if (typeof vnode.type === "function") {
    return textOf((vnode.type as (props: unknown) => unknown)(vnode.props || {}));
  }
  return textOf(vnode.props?.children);
}

/** The keyed cell entries a CellList renders. */
function listed(tree: unknown): VNode[] {
  const children = (tree as VNode).props?.children;
  return (Array.isArray(children) ? children : [children]) as VNode[];
}

// Binary by any heuristic: nothing but control characters, 2048 of them.
const BINARY = "\u0000\u0001\u0002\u0003".repeat(512);

beforeAll(async () => {
  await i18nReady();
});

beforeEach(() => {
  resetStoreFields();
});

describe("Notebook output judged binary", () => {
  it("shows the elision notice with its size instead of an empty block", () => {
    const expected = t("output.binaryElided", "2.0 KB");
    expect(expected).toContain("2.0 KB");
    expect(textOf(CellOutput({ text: BINARY, isError: false, live: false }))).toBe(expected);
    expect(textOf(CellOutput({ text: BINARY, isError: true, live: true }))).toBe(expected);
  });
});

describe("Notebook cell identity across completion", () => {
  it("keeps one component under the cell's key from its first chunk to its record", () => {
    const base = { producing_cell_id: "c1", cell_id: "c1", cell_index: 1, source: "print(1)" };
    liveCells.value = [{ ...base, live: true, status: "running" }];
    const running = listed(CellList({ entries: notebookDisplayEntries() }));
    liveCells.value = [];
    cells.value = [{ ...base, status: "ok", stdout: "1\n" }];
    const finished = listed(CellList({ entries: notebookDisplayEntries() }));
    expect(running).toHaveLength(1);
    expect(finished).toHaveLength(1);
    // A different component type under the same key makes Preact unmount the
    // card: open outputs and revisions collapse and the page jumps.
    expect(finished[0]!.key).toBe(running[0]!.key);
    expect(finished[0]!.type).toBe(running[0]!.type);
  });
});

/** The first host element whose class list contains `name`. */
function byClass(node: unknown, name: string): VNode | null {
  if (Array.isArray(node)) {
    for (const child of node) {
      const hit = byClass(child, name);
      if (hit) return hit;
    }
    return null;
  }
  if (!node || typeof node !== "object") return null;
  const vnode = node as VNode;
  if (String(vnode.props?.class || "").split(/\s+/).includes(name)) return vnode;
  return byClass(vnode.props?.children, name);
}

describe("Notebook kernel status line", () => {
  it("renders the session's last kernel read, and keeps it across an invalidation", () => {
    currentId.value = "frame-1";
    kernelView.value = {
      sid: "frame-1",
      st: { alive: true, env: { python_version: "3.11" } },
      envs: null,
      cur: null,
    };
    const line = (): VNode => byClass(StatusStrip(), "nb-status-line")!;
    const ready = t("nb.status.ready", "Python 3.11");
    expect(textOf(line())).toBe(ready);
    expect(String(line().props?.class)).toContain("ready");
    invalidateKernelCache();
    expect(textOf(line())).toBe(ready);
    currentId.value = "frame-2";
    expect(textOf(line())).toBe("…");
  });

  it("a kernel read that leaves the REPL mode alone does not re-render the whole dock", () => {
    currentId.value = "frame-1";
    kernelView.value = { sid: "frame-1", st: { alive: true }, envs: null, cur: null };
    let renders = 0;
    const dispose = effect(() => {
      renders += 1;
      NotebookDock({ entries: [] });
    });
    try {
      kernelView.value = { sid: "frame-1", st: { alive: true, generation: 2 }, envs: null, cur: null };
      expect(renders).toBe(1);
      // The REPL panel replacing the status strip is the change the dock owns.
      kernelView.value = { sid: "frame-1", st: { alive: true, repl_enabled: true }, envs: null, cur: null };
      expect(renders).toBe(2);
    } finally {
      dispose();
    }
  });
});

describe("Notebook reading gate", () => {
  it("CellList paints the entries it is handed and subscribes to no cell store", () => {
    const entries = [{ producing_cell_id: "shown", cell_index: 1, status: "error" }];
    let paints = 0;
    let painted: VNode[] = [];
    const dispose = effect(() => {
      paints += 1;
      painted = listed(CellList({ entries }));
    });
    try {
      // A retry arriving while the reader is scrolled up must not repaint
      // the list: only a render the gate allows hands CellList new entries.
      cells.value = [{ producing_cell_id: "other", cell_index: 2 }];
      liveCells.value = [{ producing_cell_id: "retry", cell_index: 3, live: true }];
      expect(paints).toBe(1);
      expect(painted.map((node) => node.key)).toEqual(["shown"]);
    } finally {
      dispose();
    }
  });
});
