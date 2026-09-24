import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("preact/hooks", () => ({
  useEffect: vi.fn(),
  useRef: (initial: unknown) => ({ current: initial }),
  useState: (initial: unknown) => [typeof initial === "function" ? (initial as () => unknown)() : initial, vi.fn()],
}));

import { customizeOpen, nestedEditor } from "../../features/customize/state";
import { Customize } from "./Customize";
import { NestedEditor } from "./NestedEditor";

type Handler = (event: { target: unknown; currentTarget: unknown }) => void;
type Node = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };

function find(node: unknown, match: (node: Node) => boolean): Node | null {
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = find(child, match);
      if (found) return found;
    }
    return null;
  }
  if (!node || typeof node !== "object") return null;
  const current = node as Node;
  if (match(current)) return current;
  return find(current.props?.children, match);
}

/** Press on `from`, let go over the backdrop: the click's target is the backdrop. */
function dragOnto(backdrop: Node, from: unknown, element: unknown) {
  (backdrop.props!.onPointerDown as Handler)({ target: from, currentTarget: element });
  (backdrop.props!.onClick as Handler)({ target: element, currentTarget: element });
}

afterEach(() => {
  customizeOpen.value = false;
  nestedEditor.value = null;
});

describe("Customize backdrops", () => {
  it("keeps Customize open when a selection drag ends on the backdrop", () => {
    customizeOpen.value = true;
    const backdrop = find(Customize(), (node) => node.props?.id === "cust")!;
    const element = { id: "cust" };
    const field = { id: "" };

    dragOnto(backdrop, field, element);
    expect(customizeOpen.value).toBe(true);

    dragOnto(backdrop, element, element);
    expect(customizeOpen.value).toBe(false);
  });

  it("keeps a nested editor open when a selection drag ends on its backdrop", () => {
    nestedEditor.value = { kind: "job", id: "job-1" };
    const backdrop = find(NestedEditor(), (node) => node.props?.class === "cust-nested")!;
    const element = { id: "" };
    const field = { id: "" };

    dragOnto(backdrop, field, element);
    expect(nestedEditor.value).toEqual({ kind: "job", id: "job-1" });

    dragOnto(backdrop, element, element);
    expect(nestedEditor.value).toBeNull();
  });
});
