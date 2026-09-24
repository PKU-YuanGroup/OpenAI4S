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

import { i18nReady, t } from "../../i18n/runtime";
import { cells, liveCells } from "../../stores/notebook";
import { resetStoreFields } from "../../stores/signal-field";
import { CellList, CellOutput } from "./Notebook";

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
    const running = listed(CellList());
    liveCells.value = [];
    cells.value = [{ ...base, status: "ok", stdout: "1\n" }];
    const finished = listed(CellList());
    expect(running).toHaveLength(1);
    expect(finished).toHaveLength(1);
    // A different component type under the same key makes Preact unmount the
    // card: open outputs and revisions collapse and the page jumps.
    expect(finished[0]!.key).toBe(running[0]!.key);
    expect(finished[0]!.type).toBe(running[0]!.type);
  });
});
