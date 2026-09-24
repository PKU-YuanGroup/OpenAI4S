/**
 * Fire-and-forget actions report a failure instead of leaving an unhandled
 * rejection: a session row's menu, the project menu's import and download
 * entries (all opened through a dynamic import), and the dashboard reload a
 * sessions refresh starts.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({ api: vi.fn(), apiErrorText: String }));
vi.mock("./chrome", () => ({
  hint: vi.fn(), openMenu: vi.fn(), ensureActivateKeys: vi.fn(), reportFailure: vi.fn(),
}));
const menus = vi.hoisted(() => ({
  sessionMenu: vi.fn(), chooseSessionPackage: vi.fn(), downloadArtifactBundle: vi.fn(),
}));
vi.mock("./actions", () => menus);

import { project } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { api } from "./api";
import { binds } from "./binds";
import { reportFailure } from "./chrome";
import { loadSessions, sessionRow } from "./load";
import { renderProjMenu } from "./projects";

class FakeNode {
  className = "";
  textContent = "";
  title = "";
  type = "";
  tabIndex = -1;
  firstChild = null;
  dataset: Record<string, string> = {};
  style: Record<string, string> = {};
  children: FakeNode[] = [];
  classList = { add: () => {}, remove: () => {}, contains: () => false, toggle: () => {} };
  onclick: ((event?: unknown) => void) | null = null;
  onkeydown: ((event?: unknown) => void) | null = null;
  set innerHTML(_value: string) { this.children = []; }
  appendChild(child: FakeNode) { this.children.push(child); return child; }
  setAttribute() {}
  removeAttribute() {}
  find(match: (node: FakeNode) => boolean): FakeNode | null {
    for (const child of this.children) {
      if (match(child)) return child;
      const deeper = child.find(match);
      if (deeper) return deeper;
    }
    return null;
  }
}

const refused = new Error("module refused");
let nodes: Record<string, FakeNode>;

beforeEach(() => {
  resetStoreFields();
  vi.mocked(reportFailure).mockReset();
  vi.mocked(api).mockReset();
  for (const fn of Object.values(menus)) fn.mockReset().mockImplementation(() => { throw refused; });
  nodes = {};
  vi.stubGlobal("document", {
    createElement: () => new FakeNode(),
    querySelector: (sel: string) => nodes[sel] ?? null,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a failed fire-and-forget action is reported", () => {
  it("the session row's menu", async () => {
    const row = sessionRow({ id: "f", name: "Assay" }) as unknown as FakeNode;
    const menu = row.find((node) => node.className === "s-menu")!;
    menu.onclick!({ stopPropagation: () => {} });
    await vi.waitFor(() => expect(reportFailure).toHaveBeenCalledWith(refused));
  });

  it.each(["import", "download"])("the project menu's %s entry", async (entry) => {
    project.value = "P";
    nodes["#proj-menu"] = new FakeNode();
    renderProjMenu();
    // After the settings/research entries: import, then download.
    const items = nodes["#proj-menu"]!.children.filter((node) => node.className === "proj-item");
    const target = items[entry === "import" ? 1 : 2]!;
    target.onclick!();
    await vi.waitFor(() => expect(reportFailure).toHaveBeenCalledWith(refused));
    expect(entry === "import" ? menus.chooseSessionPackage : menus.downloadArtifactBundle).toHaveBeenCalled();
  });

  it("the dashboard reload a sessions refresh starts", async () => {
    nodes["#dashboard"] = new FakeNode();
    vi.mocked(api).mockImplementation(async (path: string) =>
      path.includes("/folders") ? { folders: [] } : { frames: [], has_more: false });
    const failed = new Error("dashboard repaint failed");
    binds.loadDashboard = vi.fn(() => Promise.reject(failed));
    expect(await loadSessions()).toMatchObject({ status: "loaded" });
    await vi.waitFor(() => expect(reportFailure).toHaveBeenCalledWith(failed));
  });
});
