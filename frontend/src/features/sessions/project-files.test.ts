import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({ api: vi.fn(), apiErrorText: (error: unknown) => String(error) }));
vi.mock("./dashboard", () => ({ showDashboard: vi.fn(), showWorkspace: vi.fn() }));
vi.mock("./load", () => ({
  loadProjects: vi.fn(async () => undefined), loadSessions: vi.fn(),
  loadSessionsForScope: vi.fn(async () => ({ status: "loaded", rows: [] })),
  normalizeProjectQuery: (q: string) => q, sessionListScope: vi.fn(),
}));
vi.mock("./chrome", () => ({ hint: vi.fn(), reportFailure: vi.fn() }));
vi.mock("../messages/open", () => ({ openConversation: vi.fn(), recoverConversation: vi.fn(async () => undefined) }));

import { api } from "./api";
import { binds } from "./binds";
import { projectFilesCopy as copy, projectHistoryCopy } from "./copy";
import { browseProjectFolder, openProjectFiles, resetProjectFolderPicker } from "./project-files";
import { createProject, openProject, openProjectModal, renderProjMenu, submitProjectModal } from "./projects";
import { loadSessions, loadSessionsForScope } from "./load";
import { _openGen, currentId, folders, project, projects, sessions } from "../../stores/session";
import { recoverConversation } from "../messages/open";
import { hint } from "./chrome";
import { newSession } from "./conversation";
import { _modalMode } from "../../stores/ui";
import { resetStoreFields } from "../../stores/signal-field";

class Node {
  className = "";
  textContent = "";
  value = "";
  type = "";
  title = "";
  placeholder = "";
  disabled = false;
  style: Record<string, string> = {};
  attrs: Record<string, string> = {};
  children: Node[] = [];
  onclick: (() => void) | null = null;
  onkeydown: ((event: { key: string; preventDefault: () => void }) => void) | null = null;
  constructor(public tag = "div") {}
  classList = {
    contains: (cls: string) => this.className.split(" ").includes(cls),
    add: (cls: string) => { if (!this.classList.contains(cls)) this.className += ` ${cls}`; },
    remove: (cls: string) => { this.className = this.className.split(" ").filter((item) => item !== cls).join(" "); },
    toggle: (cls: string, force?: boolean) => {
      if (force ?? !this.classList.contains(cls)) this.classList.add(cls);
      else this.classList.remove(cls);
    },
  };
  set innerHTML(_value: string) { this.children = []; this.textContent = ""; }
  setAttribute(key: string, value: string) { this.attrs[key] = value; }
  removeAttribute(key: string) { delete this.attrs[key]; }
  appendChild(child: Node) { this.children.push(child); return child; }
  focus() {}
  all(): Node[] { return [this, ...this.children.flatMap((child) => child.all())]; }
  texts(): string[] { return this.all().map((node) => node.textContent).filter(Boolean); }
  click(label: string) {
    const node = this.all().find((item) => item.tag === "button" && item.textContent === label);
    expect(node, `button ${label}`).toBeDefined();
    node!.onclick!();
  }
}

let nodes: Record<string, Node>;
function deferred() {
  let resolve!: (value: unknown) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { resolve, reject, promise };
}
function listing(path: string, entries: Array<{ name: string; path: string; kind?: string }> = [], parent: string | null = null) {
  return { path, parent_path: parent, folder_path: "/research", entries, truncated: false };
}

beforeEach(() => {
  resetStoreFields();
  vi.mocked(api).mockReset();
  vi.mocked(loadSessionsForScope).mockReset().mockResolvedValue({ status: "loaded", rows: [] });
  vi.mocked(loadSessions).mockReset();
  vi.mocked(recoverConversation).mockClear();
  vi.mocked(hint).mockClear();
  nodes = Object.fromEntries([
    "#modal", "#modal-body", "#modal-title", "#modal-download", "#proj-modal", "#pm-folder", "#pm-folder-picker",
    "#pm-name", "#pm-desc", "#pm-ctx", "#pm-create", "#pm-delete", "#proj-modal .modal-head span",
    "#proj-menu", "#proj-folder-path", "#proj-current",
  ].map((key) => [key, new Node()]));
  vi.stubGlobal("document", { createElement: (tag: string) => new Node(tag), querySelector: (sel: string) => nodes[sel] ?? null });
  vi.stubGlobal("requestAnimationFrame", (callback: () => void) => callback());
  binds.newSession = vi.fn();
  binds.openConversation = vi.fn((fid, pid) => {
    currentId.value = fid;
    project.value = pid || null;
  });
  resetProjectFolderPicker("");
});

afterEach(() => vi.unstubAllGlobals());

describe("local project folder selection", () => {
  it("browses nested directories and changes the binding only after Use this folder", async () => {
    nodes["#pm-folder"]!.value = "/research & data";
    vi.mocked(api).mockResolvedValueOnce(listing("/research & data", [{ name: "samples", path: "/research & data/samples" }], "/"));
    await browseProjectFolder();
    expect(api).toHaveBeenLastCalledWith("/local-folders?path=%2Fresearch%20%26%20data");
    vi.mocked(api).mockResolvedValueOnce(listing("/research & data/samples", [], "/research & data"));
    nodes["#pm-folder-picker"]!.click("samples/");
    await vi.waitFor(() => expect(nodes["#pm-folder-picker"]!.texts()).toContain("/research & data/samples"));
    expect(nodes["#pm-folder"]!.value).toBe("/research & data");
    nodes["#pm-folder-picker"]!.click(copy("choose"));
    expect(nodes["#pm-folder"]!.value).toBe("/research & data/samples");
    expect(nodes["#pm-folder-picker"]!.classList.contains("hidden")).toBe(true);
  });

  it.each(["late success", "late error"])("does not replace a newer folder selection with a %s", async (kind) => {
    const old = deferred();
    vi.mocked(api).mockReturnValueOnce(old.promise);
    const pending = browseProjectFolder("/old");
    vi.mocked(api).mockResolvedValueOnce(listing("/new"));
    await browseProjectFolder("/new");
    if (kind === "late success") old.resolve(listing("/old"));
    else old.reject(new Error("old folder unavailable"));
    await pending;
    expect(nodes["#pm-folder-picker"]!.texts()).toContain("/new");
    expect(nodes["#pm-folder-picker"]!.texts().join(" ")).not.toContain("/old");
  });

  it("retires a pending picker when another project editor is opened", async () => {
    const old = deferred();
    vi.mocked(api).mockReturnValueOnce(old.promise);
    const pending = browseProjectFolder("/old");
    openProjectModal({ project_id: "new", folder_path: "/new" });
    old.resolve(listing("/old"));
    await pending;
    expect(nodes["#pm-folder"]!.value).toBe("/new");
    expect(nodes["#pm-folder-picker"]!.texts()).toEqual([]);
  });

  it("reports denied access and retries the requested path", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("local folders unavailable"));
    await browseProjectFolder("/denied");
    expect(nodes["#pm-folder-picker"]!.texts().join(" ")).toContain("local folders unavailable");
    vi.mocked(api).mockResolvedValueOnce(listing("/denied"));
    nodes["#pm-folder-picker"]!.click(copy("retry"));
    await vi.waitFor(() => expect(nodes["#pm-folder-picker"]!.texts()).toContain("/denied"));
  });
});

describe("read-only project files", () => {
  it("opens subfolders, encodes file paths, and renders HTML source only as text", async () => {
    vi.mocked(api).mockResolvedValueOnce(listing("", [{ name: "data", path: "data", kind: "directory" }]));
    await openProjectFiles("project/one", "My research");
    vi.mocked(api).mockResolvedValueOnce(listing("data", [{ name: "<sample>.html", path: "data/<sample>.html", kind: "file" }], ""));
    nodes["#modal-body"]!.click("data/");
    await vi.waitFor(() => expect(nodes["#modal-body"]!.texts()).toContain("<sample>.html"));
    vi.mocked(api).mockResolvedValueOnce({ path: "data/<sample>.html", content: "<script>alert(1)</script>", size: 280000, truncated: true });
    nodes["#modal-body"]!.click("<sample>.html");
    await vi.waitFor(() => expect(nodes["#modal-body"]!.all().some((node) => node.tag === "pre")).toBe(true));
    expect(api).toHaveBeenLastCalledWith("/projects/project%2Fone/file?path=data%2F%3Csample%3E.html");
    expect(nodes["#modal-body"]!.all().find((node) => node.tag === "pre")!.textContent).toBe("<script>alert(1)</script>");
    expect(nodes["#modal-body"]!.texts()).toContain(copy("previewLimited"));
    expect(nodes["#modal-body"]!.all().some((node) => node.tag === "script")).toBe(false);
  });

  it("does not let a late preview replace a newer directory read", async () => {
    vi.mocked(api).mockResolvedValueOnce(listing("", [{ name: "file.csv", path: "file.csv", kind: "file" }]));
    await openProjectFiles("p", "P");
    const old = deferred();
    vi.mocked(api).mockReturnValueOnce(old.promise);
    nodes["#modal-body"]!.click("file.csv");
    vi.mocked(api).mockResolvedValueOnce(listing("", [{ name: "new.csv", path: "new.csv", kind: "file" }]));
    nodes["#modal-body"]!.click(copy("refresh"));
    await vi.waitFor(() => expect(nodes["#modal-body"]!.texts()).toContain("new.csv"));
    old.resolve({ path: "file.csv", content: "old preview", size: 10, truncated: false });
    await Promise.resolve();
    expect(nodes["#modal-body"]!.texts()).not.toContain("old preview");
  });

  it("discards a response after the modal is reused", async () => {
    const old = deferred();
    vi.mocked(api).mockReturnValueOnce(old.promise);
    const opening = openProjectFiles("p", "P");
    _modalMode.value = "different-modal";
    nodes["#modal-body"]!.innerHTML = "";
    old.resolve(listing("", [{ name: "private.txt", path: "private.txt", kind: "file" }]));
    await opening;
    expect(nodes["#modal-body"]!.texts()).toEqual([]);
  });

  it("keeps binary-file failures recoverable through Back to folder", async () => {
    vi.mocked(api).mockResolvedValueOnce(listing("", [{ name: "data.h5", path: "data.h5", kind: "file" }]));
    await openProjectFiles("p", "P");
    vi.mocked(api).mockRejectedValueOnce(new Error("binary files have no text preview"));
    nodes["#modal-body"]!.click("data.h5");
    await vi.waitFor(() => expect(nodes["#modal-body"]!.texts().join(" ")).toContain("binary files have no text preview"));
    vi.mocked(api).mockResolvedValueOnce(listing(""));
    nodes["#modal-body"]!.click(copy("back"));
    await vi.waitFor(() => expect(nodes["#modal-body"]!.texts()).toContain(copy("empty")));
  });
});

describe("project folder persistence", () => {
  it("opens local history from the bound project's menu", async () => {
    project.value = "A";
    projects.value = [{ project_id: "A", name: "Research", folder_path: "/research" }];
    vi.mocked(api).mockResolvedValueOnce({ directory: "/research/.openai4s", status: "pending", last_saved_at: null, sessions: [] });
    renderProjMenu();
    const entry = nodes["#proj-menu"]!.children.find((node) => node.className === "proj-item" && node.texts().includes(projectHistoryCopy("menu")));
    expect(entry).toBeDefined();
    entry!.onclick!();
    await vi.waitFor(() => expect(nodes["#modal-body"]!.texts()).toContain("/research/.openai4s"));
    expect(api).toHaveBeenCalledWith("/projects/A/history", undefined);
  });

  it("keeps A's conversation and folder when opening empty B fails to POST its first frame", async () => {
    currentId.value = "session-A";
    project.value = "A";
    projects.value = [
      { project_id: "A", name: "Project A", folder_path: "/folder-A" },
      { project_id: "B", name: "Project B", folder_path: "/folder-B" },
    ];
    binds.newSession = newSession;
    const fetch = vi.fn(async () => ({ ok: false, status: 503, text: async () => JSON.stringify({ error: "creation unavailable" }) }));
    vi.stubGlobal("fetch", fetch);
    await openProject("B");
    expect(fetch).toHaveBeenCalledExactlyOnceWith("/api/v1/frames", expect.objectContaining({ method: "POST" }));
    expect(currentId.value).toBe("session-A");
    expect(project.value).toBe("A");
    expect(nodes["#proj-folder-path"]!.textContent).toBe("/folder-A");
    expect(nodes["#proj-current"]!.textContent).toBe("Project A");
    expect(recoverConversation).toHaveBeenCalled();
  });

  it("passes the original chat's project into empty-project creation for failure recovery", async () => {
    currentId.value = "session-A";
    project.value = "A";
    await openProject("B");
    expect(binds.newSession).toHaveBeenCalledExactlyOnceWith("B", {
      restoreOnFailure: { frameId: "session-A", projectId: "A" },
    });
  });

  it.each([false, true])("restores A's chat and folder after the chosen project fails, including a superseded intermediate choice: %s", async (superseded) => {
    currentId.value = "session-A";
    project.value = "A";
    projects.value = [
      { project_id: "A", name: "Project A", folder_path: "/folder-A" },
      { project_id: "B", name: "Project B", folder_path: "/folder-B" },
      { project_id: "C", name: "Project C", folder_path: "/folder-C" },
    ];
    const old = deferred();
    let pending: Promise<void> | undefined;
    if (superseded) {
      vi.mocked(loadSessionsForScope).mockReturnValueOnce(old.promise as never);
      pending = openProject("B");
      await vi.waitFor(() => expect(project.value).toBe("B"));
    }
    const target = superseded ? "C" : "B";
    vi.mocked(loadSessionsForScope).mockResolvedValueOnce({ status: "error", rows: [] });
    vi.mocked(loadSessions).mockImplementation(async () => {
      expect(project.value).toBe("A");
      sessions.value = [{ id: "session-A", project_id: "A" }];
      folders.value = [{ folder_id: "folder-A" }];
      return { status: "loaded", rows: [{ id: "session-A", project_id: "A" }] };
    });
    renderProjMenu();
    nodes["#proj-menu"]!.children.find((node) => node.className === "proj-item" && node.texts().includes(`Project ${target}`))!.onclick!();
    await vi.waitFor(() => expect(recoverConversation).toHaveBeenCalled());
    expect(currentId.value).toBe("session-A");
    expect(project.value).toBe("A");
    expect(nodes["#proj-folder-path"]!.textContent).toBe("/folder-A");
    expect(nodes["#proj-current"]!.textContent).toBe("Project A");
    expect(folders.value).toEqual([{ folder_id: "folder-A" }]);
    expect(sessions.value).toEqual([{ id: "session-A", project_id: "A" }]);
    expect(hint).toHaveBeenCalledWith(copy("projectOpenFailed", `Project ${target}`), true);
    expect(binds.openConversation).not.toHaveBeenCalled();
    expect(binds.newSession).not.toHaveBeenCalled();
    if (pending) {
      old.resolve({ status: "loaded", rows: [{ id: "session-B", project_id: "B" }] });
      await pending;
      expect(project.value).toBe("A");
      expect(currentId.value).toBe("session-A");
    }
  });

  it("switches the conversation as well as the folder when another project is chosen from the menu", async () => {
    currentId.value = "session-A";
    project.value = "A";
    projects.value = [
      { project_id: "A", name: "Project A", folder_path: "/folder-A" },
      { project_id: "B", name: "Project B", folder_path: "/folder-B" },
    ];
    vi.mocked(loadSessionsForScope).mockResolvedValueOnce({ status: "loaded", rows: [{ id: "session-B", project_id: "B" }] } as never);
    renderProjMenu();
    const menuItem = nodes["#proj-menu"]!.children.find((node) => node.className === "proj-item" && node.texts().includes("Project B"));
    expect(menuItem).toBeDefined();
    menuItem!.onclick!();
    await vi.waitFor(() => expect(currentId.value).toBe("session-B"));
    expect(project.value).toBe("B");
    expect(binds.openConversation).toHaveBeenCalledWith("session-B", "B");
    expect(nodes["#proj-folder-path"]!.textContent).toBe("/folder-B");
    expect(binds.newSession).not.toHaveBeenCalled();
  });

  it("preserves the three-argument creation contract and accepts an optional folder", async () => {
    vi.mocked(api).mockImplementation(async () => { _openGen.value += 1; return { project_id: "p" }; });
    await createProject("Research", "Desc", "Context");
    expect(JSON.parse(String(vi.mocked(api).mock.calls[0]![1]!.body))).toEqual({ name: "Research", description: "Desc", context: "Context" });
    await createProject("Research", "Desc", "Context", " /research ");
    expect(JSON.parse(String(vi.mocked(api).mock.calls[1]![1]!.body)).folder_path).toBe("/research");
  });

  it.each(["/research", ""])("saves a selected folder or explicit detach: %s", async (folderPath) => {
    openProjectModal({ project_id: "p", name: "Research", folder_path: "/previous" });
    expect(nodes["#pm-folder"]!.value).toBe("/previous");
    nodes["#pm-folder"]!.value = folderPath;
    vi.mocked(api).mockResolvedValueOnce({ project_id: "p" });
    await submitProjectModal();
    const [url, request] = vi.mocked(api).mock.calls[0]!;
    expect(url).toBe("/projects/p");
    expect(request!.method).toBe("PATCH");
    expect(JSON.parse(String(request!.body)).folder_path).toBe(folderPath);
  });
});
