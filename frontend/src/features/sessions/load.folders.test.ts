/**
 * A folder rename or delete the server refuses -- usually `404`, a folder
 * another tab already deleted -- is reported, and both lists are still re-read
 * so the stale row leaves the sidebar. The refusal used to be swallowed before
 * the re-read, so the row stayed and the menu item silently did nothing.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({ api: vi.fn(), apiErrorText: String }));
vi.mock("./chrome", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./chrome")>()),
  openMenu: vi.fn(),
  reportFailure: vi.fn(),
}));

import { t } from "../../i18n";
import { _sessionScope, project } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { api } from "./api";
import { openMenu, reportFailure } from "./chrome";
import { loadSessions } from "./load";

class FakeNode {
  tagName = "DIV";
  children: FakeNode[] = [];
  className = "";
  textContent = "";
  tabIndex = -1;
  firstChild = null;
  style: Record<string, string> = {};
  dataset: Record<string, string> = {};
  onclick: ((event: unknown) => void) | null = null;
  set innerHTML(_value: string) { this.children = []; }
  appendChild(child: FakeNode) { this.children.push(child); return child; }
  setAttribute() {}
  getAttribute() { return null; }
  addEventListener() {}
}

function find(node: FakeNode, className: string, inside: string): FakeNode | null {
  if (node.className === inside) {
    const hit = node.children.find((child) => child.className === className);
    if (hit) return hit;
  }
  for (const child of node.children) {
    const hit = find(child, className, inside);
    if (hit) return hit;
  }
  return null;
}

const refused = Object.assign(new Error("folder not found"), { status: 404 });
let list: FakeNode;

beforeEach(() => {
  resetStoreFields();
  list = new FakeNode();
  vi.stubGlobal("document", {
    querySelector: (selector: string) => (selector === "#session-list" ? list : null),
    createElement: () => new FakeNode(),
    createDocumentFragment: () => new FakeNode(),
  });
  vi.stubGlobal("prompt", () => "Renamed");
  vi.stubGlobal("confirm", () => true);
  vi.mocked(openMenu).mockReset();
  vi.mocked(reportFailure).mockReset();
  vi.mocked(api).mockReset().mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "/folders/d1" && init?.method) throw refused;
    return path.includes("/folders")
      ? { folders: [{ folder_id: "d1", name: "Assays" }] }
      : { frames: [], has_more: false };
  });
  project.value = "P";
  _sessionScope.value = "P";
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function chooseFolderMenuItem(label: string): Promise<void> {
  await loadSessions();
  const menu = find(list, "s-menu", "folder-head");
  expect(menu).not.toBeNull();
  menu!.onclick!({ stopPropagation() {} });
  const items = vi.mocked(openMenu).mock.calls.at(-1)![1];
  const item = items.find((entry) => entry.label === label);
  expect(item).toBeDefined();
  vi.mocked(api).mockClear();
  await item!.onClick!();
}

describe("a refused folder write", () => {
  it.each([
    ["rename", "folder.menu.rename", "PATCH"],
    ["delete", "folder.menu.delete", "DELETE"],
  ])("%s is reported and the folder list is still re-read", async (_name, key, method) => {
    await chooseFolderMenuItem(t(key));
    const calls = vi.mocked(api).mock.calls.map(([path, init]) => [path, init?.method ?? "GET"]);
    expect(calls[0]).toEqual(["/folders/d1", method]);
    expect(reportFailure).toHaveBeenCalledWith(refused);
    expect(calls).toContainEqual(["/projects/P/folders", "GET"]);
  });
});
