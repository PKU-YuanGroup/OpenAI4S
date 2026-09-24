/**
 * What the dashboard's project card shows after a repaint that is not a full
 * load, and what the project directory holds.
 *
 * The running badge vanished on the first keystroke because a search repaint
 * uses fresh server rows that carry no `running_count`. And a search used to
 * replace `projects.value`, which the workspace header, the switcher and the
 * session and attention labels read as the whole directory, so every project
 * outside the filter lost its name while the box held a query.
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
import { projectSearch, projects, projectsQuery } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";

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
  resetStoreFields();
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
    projectsQuery.value = "alpha";
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

describe("the project directory and the dashboard search", () => {
  const directoryPage = {
    projects: [
      { project_id: "p1", name: "alpha lab" },
      { project_id: "p2", name: "beta lab" },
    ],
    total: 2,
  };

  it("a search has pages of its own and never replaces the directory", async () => {
    vi.mocked(api).mockImplementation((path: string) =>
      Promise.resolve(path.includes("q=") ? projectPage : directoryPage) as never,
    );
    await loadProjects();
    await loadProjects({ q: "alpha" });
    expect((projectSearch.value as Array<{ project_id: string }>).map((p) => p.project_id)).toEqual(["p1"]);
    expect(projects.value).toEqual(directoryPage.projects);
  });

  it("names a session's project in Recent while the search filters that project out", async () => {
    dom["#dash-sessions"] = fakeEl();
    projectsQuery.value = "alpha";
    vi.mocked(api).mockImplementation((path: string) =>
      Promise.resolve(
        path.startsWith("/frames")
          ? { frames: [{ id: "f2", project_id: "p2", name: "Spectra", message_count: 3 }] }
          : path.includes("q=") ? projectPage : directoryPage,
      ) as never,
    );
    await loadDashboard();
    const names: string[] = [];
    const walk = (n: FakeEl) => {
      if (n.cls === "d-sub") names.push(n.text);
      n.children.forEach(walk);
    };
    walk(dom["#dash-sessions"]!);
    expect(names).toEqual(["beta lab"]);
  });

  it("keeps the directory when a refresh of it fails, and leaving the dashboard does not reload it", async () => {
    vi.mocked(api).mockResolvedValue(directoryPage as never);
    await loadProjects();
    vi.mocked(api).mockRejectedValue(new Error("daemon restarting") as never);
    await loadProjects();
    expect(projects.value).toEqual(directoryPage.projects);

    vi.mocked(api).mockClear();
    showWorkspace();
    expect(vi.mocked(api)).not.toHaveBeenCalled();
  });
});
