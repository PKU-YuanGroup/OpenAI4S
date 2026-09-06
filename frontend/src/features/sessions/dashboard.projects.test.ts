/**
 * What the dashboard's project card shows after a repaint that is not a full
 * load, and what opening a session leaves behind in the project store.
 *
 * Both behaviours were found by driving the UI, and neither had a test: the
 * running badge vanished on the first keystroke because a search repaint uses
 * fresh server rows that carry no `running_count`, and opening a session from
 * a dashboard card left the *filtered* page in `projects.value`, which the
 * workspace header and the switcher read as the whole directory.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

type FakeEl = {
  tag: string;
  cls: string | null;
  text: string;
  title: string;
  onclick: (() => void) | null;
  children: FakeEl[];
  classList: { contains: () => boolean; add: () => void; remove: () => void };
  appendChild: (child: FakeEl) => FakeEl;
  childElementCount: number;
  innerHTML: string;
};

function fakeEl(tag = "div", cls: string | null = null, text = ""): FakeEl {
  const node = {
    tag,
    cls,
    text,
    title: "",
    onclick: null,
    children: [] as FakeEl[],
    classList: { contains: () => false, add: () => {}, remove: () => {} },
    appendChild(child: FakeEl) {
      node.children.push(child);
      return child;
    },
    get childElementCount() {
      return node.children.length;
    },
    get innerHTML() {
      return "";
    },
    set innerHTML(_value: string) {
      node.children.length = 0;
    },
  } as FakeEl;
  return node;
}

const dom: Record<string, FakeEl | null> = {};

vi.mock("./api", () => ({ api: vi.fn(), apiErrorText: (e: unknown) => String(e) }));
vi.mock("./chrome", () => ({ ensureActivateKeys: () => {} }));
vi.mock("./dom", () => ({
  $: (sel: string) => (sel in dom ? dom[sel] : null),
  el: (tag: string, cls: string | null, text?: string) => fakeEl(tag, cls, text || ""),
  ago: () => "just now",
  navURL: () => {},
  syncMobileChrome: () => {},
}));

import { api } from "./api";
import {
  loadDashboard,
  refreshDashRunning,
  renderDashProjects,
  showWorkspace,
} from "./dashboard";
import { loadProjects } from "./load";
import { projects, projectsQuery } from "../../stores/session";

/** Every `d-run` badge in the painted card, as its rendered count. */
function badges(node: FakeEl): string[] {
  const out: string[] = [];
  const walk = (n: FakeEl) => {
    if (n.cls === "d-run") out.push(n.children.map((c) => c.text).join(""));
    n.children.forEach(walk);
  };
  walk(node);
  return out;
}

const projectPage = { projects: [{ project_id: "p1", name: "alpha lab" }], total: 1 };

beforeEach(() => {
  vi.mocked(api).mockReset();
  for (const key of Object.keys(dom)) delete dom[key];
  dom["#dashboard"] = fakeEl();
  dom["#workspace"] = fakeEl();
  dom["#dash-projects"] = fakeEl();
  (globalThis as { document?: unknown }).document = {
    hidden: false,
    createDocumentFragment: () => fakeEl("fragment"),
  };
  projects.value = [];
  projectsQuery.value = "";
});

describe("the running badge across repaints", () => {
  it("survives a repaint that reloads only the projects", async () => {
    vi.mocked(api).mockImplementation((path: string) =>
      Promise.resolve(
        path.startsWith("/frames")
          ? { frames: [{ id: "f1", project_id: "p1", running: true }] }
          : projectPage,
      ) as never,
    );

    await loadDashboard();
    expect(badges(dom["#dash-projects"]!)).toEqual(["1"]);

    // What a keystroke does: fresh server rows, no running_count on them.
    await loadProjects({ q: "alpha" });
    renderDashProjects();
    expect(badges(dom["#dash-projects"]!)).toEqual(["1"]);
  });

  it("does not outlive the poll that emptied the running card", async () => {
    vi.mocked(api).mockImplementation((path: string) =>
      Promise.resolve(
        path.startsWith("/frames")
          ? { frames: [{ id: "f1", project_id: "p1", running: true }] }
          : projectPage,
      ) as never,
    );
    await loadDashboard();
    expect(badges(dom["#dash-projects"]!)).toEqual(["1"]);

    // The session finishes; the 4s poll sees it gone.
    vi.mocked(api).mockImplementation((path: string) =>
      Promise.resolve(path.startsWith("/frames") ? { frames: [] } : projectPage) as never,
    );
    await refreshDashRunning();
    renderDashProjects();
    expect(badges(dom["#dash-projects"]!)).toEqual([]);
  });
});

describe("leaving the dashboard for the workspace", () => {
  it("reloads the unfiltered directory the header and switcher read", async () => {
    vi.mocked(api).mockResolvedValue(projectPage as never);
    await loadProjects({ q: "alpha" });
    vi.mocked(api).mockClear();

    showWorkspace();

    const calls = vi.mocked(api).mock.calls;
    expect(calls).toHaveLength(1);
    expect(String(calls[0]?.[0])).not.toContain("q=");
  });

  it("keeps the list it had when that background reload fails", async () => {
    vi.mocked(api).mockResolvedValue(projectPage as never);
    await loadProjects({ q: "alpha" });
    const filtered = projects.value;
    expect(filtered).toHaveLength(1);

    vi.mocked(api).mockRejectedValue(new Error("daemon restarting") as never);
    showWorkspace();
    await new Promise((resolve) => setTimeout(resolve, 0));

    // A failed *replace* empties the store, which is right for the dashboard
    // card and wrong here: an empty switcher is the symptom, not the fix.
    expect(projects.value).toEqual(filtered);
  });
});
