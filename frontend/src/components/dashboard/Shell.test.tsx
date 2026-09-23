/**
 * What the Shell's first paint shows before any route has run.
 *
 * The first view waits for the locale chunks (features/sessions/boot.ts), and
 * the Shell used to paint the dashboard -- Projects and Recent sessions with
 * empty lists -- while it did. A deep link to a session therefore landed on
 * the wrong screen for the whole wait: seconds on a relay or tunnel link.
 * A path that routes into the workspace now starts with neither view shown
 * and a neutral loading indicator between them.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("preact/hooks", () => ({ useEffect: () => undefined }));
vi.mock("../../features/sessions/boot", () => ({ bindWorkbench: () => Promise.resolve() }));
vi.mock("../../features/artifacts/boot", () => ({ finishArtifactsBoot: () => undefined }));
vi.mock("../../features/messages/components", () => ({ HistoryLoadStatus: () => null }));
vi.mock("./ModelSelect", () => ({ ModelSelect: () => null }));

import { Shell } from "./Shell";

type VNode = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };

function byId(tree: unknown, id: string): VNode | null {
  if (Array.isArray(tree)) {
    for (const child of tree) {
      const hit = byId(child, id);
      if (hit) return hit;
    }
    return null;
  }
  if (!tree || typeof tree !== "object") return null;
  const node = tree as VNode;
  if (node.props?.id === id) return node;
  return byId(node.props?.children, id);
}

function classes(node: VNode | null): string[] {
  return String(node?.props?.class || "").split(/\s+/).filter(Boolean);
}

/** The class lists of every element from the root down to `id`, or null. */
function ancestry(tree: unknown, id: string, trail: string[][] = []): string[][] | null {
  if (Array.isArray(tree)) {
    for (const child of tree) {
      const hit = ancestry(child, id, trail);
      if (hit) return hit;
    }
    return null;
  }
  if (!tree || typeof tree !== "object") return null;
  const node = tree as VNode;
  const here = [...trail, classes(node)];
  if (node.props?.id === id) return here;
  return ancestry(node.props?.children, id, here);
}

function paintAt(pathname: string): unknown {
  vi.stubGlobal("location", { pathname });
  return Shell();
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the Shell before the first route", () => {
  it("does not paint the dashboard under a session deep link", () => {
    const tree = paintAt("/projects/p-1/frames/f-1");
    expect(classes(byId(tree, "dashboard"))).toContain("hidden");
    expect(classes(byId(tree, "workspace"))).toContain("hidden");
    const loading = byId(tree, "route-loading");
    expect(loading).not.toBeNull();
    expect(loading?.props?.hidden).toBeFalsy();
  });

  it("does not paint the dashboard under a project deep link", () => {
    const tree = paintAt("/projects/p-1");
    expect(classes(byId(tree, "dashboard"))).toContain("hidden");
    expect(byId(tree, "route-loading")?.props?.hidden).toBeFalsy();
  });

  it("puts New project in the hero as the one primary action, beside Import", () => {
    const tree = paintAt("/");
    expect(classes(byId(tree, "dash-new-project"))).toContain("solid-btn");
    expect(classes(byId(tree, "dash-import-session"))).toContain("outline-btn");
    for (const id of ["dash-new-project", "dash-import-session", "session-package-input"]) {
      const trail = ancestry(tree, id) || [];
      expect(trail.some((cls) => cls.includes("dash-hero"))).toBe(true);
      expect(trail.some((cls) => cls.includes("dash-head"))).toBe(false);
    }
  });

  it("paints the dashboard at the root, with no loading indicator", () => {
    for (const pathname of ["/", "/settings", "/projectsX/p-1"]) {
      const tree = paintAt(pathname);
      expect(classes(byId(tree, "dashboard"))).not.toContain("hidden");
      expect(classes(byId(tree, "workspace"))).toContain("hidden");
      expect(byId(tree, "route-loading")?.props?.hidden).toBe(true);
    }
  });
});
