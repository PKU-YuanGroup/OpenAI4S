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

import { filesContentType, filesKind, resetFilesIndexState } from "../../features/artifacts/state";
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

describe("Files type filter (ART-12, was AUDIT A64)", () => {
  afterEach(() => {
    hooks.state.length = 0;
    resetFilesIndexState();
    resetStoreFields();
  });

  it("is a choice of human kinds that applies on change, not a MIME text box", () => {
    const select = typeFilter();
    expect(select?.type).toBe("select");
    expect(select?.props?.value).toBe("");
    (select?.props?.onChange as (event: unknown) => void)({ currentTarget: { value: "table" } });
    expect(filesKind.value).toBe("table");
    expect(filesContentType.value).toBe("");
    expect(typeFilter()?.props?.value).toBe("table");
    (typeFilter()?.props?.onChange as (event: unknown) => void)({ currentTarget: { value: "image/png" } });
    // Anything outside the closed set means every kind.
    expect(filesKind.value).toBe("");
  });
});
