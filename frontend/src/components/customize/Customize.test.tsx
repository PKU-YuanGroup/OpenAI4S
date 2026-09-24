import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("preact/hooks", () => ({
  useEffect: vi.fn(),
  useRef: (initial: unknown) => ({ current: initial }),
  useState: (initial: unknown) => [typeof initial === "function" ? (initial as () => unknown)() : initial, vi.fn()],
}));

import { effect } from "@preact/signals";
import {
  customizeLoad,
  customizeOpen,
  customizeTab,
  nestedEditor,
} from "../../features/customize/state";
import { currentId, projects, sessions } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { Customize } from "./Customize";
import { MemoryTab } from "./MemoryTab";
import { NestedEditor } from "./NestedEditor";
import { SkillsTab } from "./SkillsTab";

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
  customizeTab.value = "general";
  nestedEditor.value = null;
  resetStoreFields();
});

/** How often `render` runs again when the signals it read change. */
function renders(render: () => unknown): { count: () => number; stop: () => void } {
  let count = 0;
  const stop = effect(() => {
    count += 1;
    render();
  });
  return { count: () => count, stop };
}

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

describe("Customize render subscriptions", () => {
  it("the modal does not re-render for a settling load or a nested editor", () => {
    customizeOpen.value = true;
    const modal = renders(() => Customize());
    customizeLoad.value = { generation: customizeLoad.value.generation + 1, state: "ready", error: null };
    nestedEditor.value = { kind: "job", id: "job-1" };
    expect(modal.count()).toBe(1);
    customizeTab.value = "memory";
    expect(modal.count()).toBe(2);
    modal.stop();
  });

  it.each([
    ["Skills", () => SkillsTab()],
    ["Memory", () => MemoryTab()],
  ])("the %s tab re-renders when its project changes, not on every session-list update", (_name, tab) => {
    currentId.value = "s-1";
    sessions.value = [{ id: "s-1", project_id: "p-1" }];
    const view = renders(tab);
    sessions.value = [{ id: "s-1", project_id: "p-1" }, { id: "s-2", project_id: "p-9" }];
    projects.value = [{ project_id: "p-1", name: "Cells" }];
    expect(view.count()).toBe(1);
    sessions.value = [{ id: "s-1", project_id: "p-2" }];
    expect(view.count()).toBe(2);
    view.stop();
  });
});
