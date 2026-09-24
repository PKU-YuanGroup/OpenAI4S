import { afterEach, describe, expect, it, vi } from "vitest";

const hooks = vi.hoisted(() => ({ state: [] as unknown[], cursor: 0 }));
vi.mock("preact/hooks", () => ({
  useState: (initial: unknown) => {
    const index = hooks.cursor++;
    if (hooks.state.length === index) hooks.state.push(initial);
    return [hooks.state[index], (value: unknown) => { hooks.state[index] = value; }];
  },
  useEffect: () => undefined,
}));

import { filesContentType, resetFilesIndexState } from "../../features/artifacts/state";
import { resetStoreFields } from "../../stores/signal-field";
import { FilesPanel } from "./FilesPanel";

type VNode = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };

function find(tree: unknown, match: (node: VNode) => boolean): VNode | null {
  if (Array.isArray(tree)) {
    for (const child of tree) {
      const hit = find(child, match);
      if (hit) return hit;
    }
    return null;
  }
  if (!tree || typeof tree !== "object") return null;
  const node = tree as VNode;
  if (match(node)) return node;
  return find(node.props?.children, match);
}

/** One render of the panel, as a signal change would trigger it. */
function typeFilter(): VNode | null {
  hooks.cursor = 0;
  return find(FilesPanel(), (node) => node.props?.class === "files-filter-type");
}

describe("Files content-type filter (AUDIT A64)", () => {
  afterEach(() => {
    hooks.state.length = 0;
    resetFilesIndexState();
    resetStoreFields();
  });

  it("keeps the typed text across a re-render until the change applies it", () => {
    (typeFilter()?.props?.onInput as (event: unknown) => void)({ currentTarget: { value: "text/c" } });
    // Anything else re-rendering the panel mid-typing must not undo the keystrokes.
    expect(typeFilter()?.props?.value).toBe("text/c");
    expect(filesContentType.value).toBe("");
    (typeFilter()?.props?.onChange as (event: unknown) => void)({ currentTarget: { value: "text/csv" } });
    expect(filesContentType.value).toBe("text/csv");
    expect(typeFilter()?.props?.value).toBe("text/csv");
  });
});
