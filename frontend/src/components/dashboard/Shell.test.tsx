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
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("preact/hooks", () => ({ useEffect: () => undefined }));
vi.mock("../../features/sessions/boot", () => ({ bindWorkbench: () => Promise.resolve() }));
vi.mock("../../features/artifacts/boot", () => ({ finishArtifactsBoot: () => undefined }));
vi.mock("../../features/messages/components", () => ({ HistoryLoadStatus: () => null }));
vi.mock("./ModelSelect", () => ({ ModelKeyNotice: () => null, ModelSelect: () => null }));

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

describe("the Shell repainted after a language switch", () => {
  it("offers an unbound project-folder input and an inline directory picker", () => {
    const tree = paintAt("/");
    const input = byId(tree, "pm-folder");
    expect(input?.props).not.toHaveProperty("value");
    expect(input?.props?.["aria-describedby"]).toBe("pm-folder-help pm-history-help");
    expect(byId(tree, "pm-history-help")).not.toBeNull();
    expect(byId(tree, "pm-browse")?.props?.onClick).toBeTypeOf("function");
    expect(classes(byId(tree, "pm-folder-picker"))).toContain("hidden");
  });
  it("does not bind the session title the router writes", () => {
    // A bound `value` is compared with the live input on every repaint, so a
    // language switch put "会话" back over the session's name.
    const title = byId(paintAt("/projects/p-1/frames/f-1"), "conv-title");
    expect(title?.props).not.toHaveProperty("value");
    expect(title?.props?.defaultValue).toBe("会话");
  });
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

/** Every node carrying `className`, with the class lists of its ancestors. */
function withClass(
  tree: unknown,
  className: string,
  trail: string[][] = [],
  found: { node: VNode; trail: string[][] }[] = [],
): { node: VNode; trail: string[][] }[] {
  if (Array.isArray(tree)) {
    tree.forEach((child) => withClass(child, className, trail, found));
    return found;
  }
  if (!tree || typeof tree !== "object") return found;
  const node = tree as VNode;
  if (classes(node).includes(className)) found.push({ node, trail });
  withClass(node.props?.children, className, [...trail, classes(node)], found);
  return found;
}

/**
 * The islands were ported with their `$("#…")` lookups intact, and each one
 * returns quietly when its node is missing. The Shell rendered none of these
 * four, so the features behind them were unreachable in the default workbench
 * while every island test -- which builds its own node -- stayed green.
 */
describe("the nodes the ported islands look up", () => {
  it("puts the pinned-comment chip inside the composer card, above the textarea", () => {
    const tree = paintAt("/");
    expect(classes(byId(tree, "annot-bar"))).toEqual(["annot-bar", "hidden"]);
    const card = withClass(tree, "nb-card")[0]?.node;
    const children = (card?.props?.children as VNode[]).filter(
      (child) => child && typeof child === "object" && child.props,
    );
    expect(children.map((child) => child.props?.id)).toEqual(
      expect.arrayContaining(["annot-bar", "composer"]),
    );
    const ids = children.map((child) => child.props?.id);
    expect(ids.indexOf("annot-bar")).toBeLessThan(ids.indexOf("composer"));
  });

  it("puts the queued follow-up strip above the composer card, outside it", () => {
    // Under the card, a queued row's ✕ landed exactly where Stop had been a
    // second earlier, and showing the strip pushed the card up by its height.
    const tree = paintAt("/");
    expect(classes(byId(tree, "queue-strip"))).toEqual(["queue-strip", "hidden"]);
    const trail = ancestry(tree, "queue-strip") || [];
    expect(trail.some((cls) => cls.includes("composer-wrap"))).toBe(true);
    expect(trail.some((cls) => cls.includes("nb-card"))).toBe(false);
    const wrap = withClass(tree, "composer-wrap")[0]?.node;
    const order = ([] as VNode[])
      .concat(wrap?.props?.children as VNode[])
      .filter((child) => child && typeof child === "object" && child.props)
      .map((child) => String(child.props?.id || child.props?.class || ""));
    expect(order.indexOf("queue-strip")).toBeGreaterThanOrEqual(0);
    expect(order.indexOf("queue-strip")).toBeLessThan(order.indexOf("notebook"));
  });

  it("keeps Send in the composer beside a separate Stop with a filled-square icon", () => {
    // Stop used to replace Send while a turn ran (one slot, CSS-swapped), so a
    // mouse user looking for Send to queue a follow-up hit Stop instead.
    const tree = paintAt("/");
    const right = withClass(tree, "nb-right")[0]?.node;
    const ids = ([] as VNode[])
      .concat(right?.props?.children as VNode[])
      .filter((child) => child && typeof child === "object" && child.props)
      .map((child) => child.props?.id);
    expect(ids).toContain("cancel-btn");
    expect(ids.at(-1)).toBe("send-btn");
    const stop = byId(tree, "cancel-btn");
    expect(stop?.props?.["data-icon"]).toBe("stop-square");
    expect(classes(stop)).toContain("hidden");
    expect(classes(byId(tree, "send-btn"))).not.toContain("hidden");
    // The swap itself lived in the shared stylesheet: no rule may hide Send
    // because Stop is showing.
    const css = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "../../../../openai4s/server/webui/style.css"),
      "utf8",
    );
    expect(css).not.toMatch(/#cancel-btn:not\(\.hidden\)\)\s*\.nb-send\s*\{[^}]*display:\s*none/);
  });

  it("offers voice input among the composer tools, leaving send last", () => {
    const tree = paintAt("/");
    const mic = byId(tree, "mic-btn");
    expect(mic?.props?.["data-icon"]).toBe("mic");
    expect(mic?.props?.["data-i18n-title"]).toBe("composer.voice");
    const trail = ancestry(tree, "mic-btn") || [];
    expect(trail.some((cls) => cls.includes("nb-left"))).toBe(true);
    const right = withClass(tree, "nb-right")[0]?.node;
    const last = ([] as VNode[]).concat(right?.props?.children as VNode[]).at(-1);
    expect(last?.props?.id).toBe("send-btn");
  });

  it("paints the readiness banner on the dashboard and above the conversation", () => {
    const tree = paintAt("/");
    const banners = withClass(tree, "environment-readiness-banner");
    expect(banners).toHaveLength(2);
    for (const { node } of banners) {
      expect(classes(node)).toContain("hidden");
      const slots = JSON.stringify(node.props?.children);
      expect(slots).toContain("data-environment-readiness-title");
      expect(slots).toContain("data-environment-readiness-summary");
      expect(slots).toContain("data-open-environment-readiness");
    }
    const homes = banners.map(({ trail }) =>
      trail.some((cls) => cls.includes("dashboard")) ? "dashboard" : "workspace",
    );
    expect(homes.sort()).toEqual(["dashboard", "workspace"]);
  });

  it("opens Customize on Compute from the readiness banner", () => {
    const opened: unknown[] = [];
    vi.stubGlobal("openCust", (tab: unknown) => opened.push(tab));
    const tree = paintAt("/");
    for (const { node } of withClass(tree, "environment-readiness-banner")) {
      const button = ([] as VNode[])
        .concat(node.props?.children as VNode[])
        .find((child) => child?.props && "data-open-environment-readiness" in child.props);
      (button?.props?.onClick as () => void)();
    }
    expect(opened).toEqual(["compute", "compute"]);
  });
});
