import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({ api: vi.fn(), apiErrorText: String }));
vi.mock("./dashboard", () => ({ showWorkspace: vi.fn(), showDashboard: vi.fn() }));

import { _msgEarlierLoading, _openGen, _foldersFor, _sessionsLoadingMore, folders, foldersLoading, foldersLoadError, project, sessionPages, sessions, sessionsHasMore, sessionsLoadError } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { api } from "./api";
import { binds } from "./binds";
import { invalidateFolders, loadFolders, loadMoreSessions, loadSessions } from "./load";
import { beginNavigation } from "./navigation";
import { openProject, selectProject } from "./projects";

function deferred() {
  let resolve!: (value: unknown) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<unknown>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const page = (pid: string, id: string, more = false) => ({
  frames: id ? [{ id, project_id: pid }] : [], has_more: more, next_cursor: more ? "next" : null,
});
const ids = () => (sessions.value as { id: string }[]).map((row) => row.id);
const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

beforeEach(() => {
  resetStoreFields();
  vi.mocked(api).mockReset().mockImplementation(async (path) => {
    if (path.includes("/folders")) return { folders: [] };
    if (path.startsWith("/projects?")) return { projects: [] };
    return page(project.value || "", "");
  });
  binds.openConversation = vi.fn();
  binds.newSession = vi.fn();
});

describe("session navigation owns every response", () => {
  it("a sidebar-only project switch releases the earlier-history loading latch", () => {
    // selectProject is the one caller that bumps the generation while keeping
    // the conversation open, so nothing else ever resets this flag: the
    // superseded paging request skips its own finally on a stale generation
    // and "Load earlier" stayed disabled for the rest of the session.
    _msgEarlierLoading.value = true;
    selectProject("p2");
    expect(_msgEarlierLoading.value).toBe(false);
    _msgEarlierLoading.value = true;
    beginNavigation();
    expect(_msgEarlierLoading.value).toBe(false);
  });

  it("opening a loaded session keeps a folder-read failure visible for retry", async () => {
    vi.mocked(api).mockImplementation(async (path) => {
      if (path.includes("/folders")) throw new Error("folders unavailable");
      if (path.startsWith("/frames?")) return page("A", "a");
      return { projects: [] };
    });
    binds.openConversation = vi.fn(() => { beginNavigation(); });
    await openProject("A");
    expect(binds.openConversation).toHaveBeenCalledExactlyOnceWith("a", "A");
    expect(foldersLoadError.value).toBe(true);
    expect(sessionsLoadError.value).toBe(false);
  });
  it.each(["success", "failure"])("same-visit refresh rejects earlier %s without invalidating navigation", async (outcome) => {
    project.value = "A";
    const generation = _openGen.value;
    const old = deferred(); vi.mocked(api).mockReturnValueOnce(old.promise);
    const first = loadSessions();
    vi.mocked(api).mockResolvedValueOnce(page("A", "fresh"));
    await loadSessions();
    if (outcome === "success") old.resolve(page("A", "old"));
    else old.reject(new Error("old read"));
    await first;
    expect(ids()).toEqual(["fresh"]);
    expect(sessionsLoadError.value).toBe(false);
    expect(_openGen.value).toBe(generation);
  });

  it("refresh during project open follows the newer read and opens exactly once", async () => {
    const old = deferred(); const fresh = deferred();
    vi.mocked(api).mockImplementation((path) => {
      if (path.startsWith("/frames?")) return old.promise;
      return Promise.resolve(path.includes("/folders") ? { folders: [] } : { projects: [] });
    });
    const opening = openProject("A"); await settle();
    vi.mocked(api).mockReturnValueOnce(fresh.promise);
    const refresh = loadSessions();
    old.resolve(page("A", "stale")); await settle();
    expect(binds.openConversation).not.toHaveBeenCalled();
    fresh.resolve(page("A", "fresh"));
    await Promise.all([refresh, opening]);
    expect(binds.openConversation).toHaveBeenCalledExactlyOnceWith("fresh", "A");
    expect(binds.newSession).not.toHaveBeenCalled();
  });

  it("a newer successful read completes navigation while the old socket is still pending", async () => {
    const old = deferred();
    vi.mocked(api).mockImplementation((path) => path.startsWith("/frames?") ? old.promise :
      Promise.resolve(path.includes("/folders") ? { folders: [] } : { projects: [] }));
    const opening = openProject("A"); await settle();
    vi.mocked(api).mockResolvedValueOnce(page("A", "fresh"));
    await loadSessions(); await settle();
    expect(binds.openConversation).toHaveBeenCalledExactlyOnceWith("fresh", "A");
    old.resolve(page("A", "old")); await opening;
    expect(binds.openConversation).toHaveBeenCalledTimes(1);
  });

  it("a background refresh inherits an in-flight load-more budget", async () => {
    project.value = "A";
    vi.mocked(api).mockResolvedValueOnce(page("A", "first", true)); await loadSessions();
    const old = deferred(); vi.mocked(api).mockReturnValueOnce(old.promise);
    const more = loadMoreSessions();
    vi.mocked(api).mockResolvedValueOnce(page("A", "first", true)).mockResolvedValueOnce(page("A", "second"));
    await loadSessions();
    expect(ids()).toEqual(["first", "second"]);
    expect(sessionPages.value).toBe(2);
    old.resolve(page("A", "obsolete")); await more;
    expect(ids()).toEqual(["first", "second"]);
  });

  it("a successful empty read creates once, and Home invalidates a parked open", async () => {
    await openProject("A");
    expect(binds.newSession).toHaveBeenCalledExactlyOnceWith("A");
    vi.mocked(binds.newSession).mockClear();
    const old = deferred(); vi.mocked(api).mockReturnValueOnce(old.promise);
    const opening = openProject("A");
    beginNavigation(); // Home keeps the project ID but changes the visit.
    old.resolve({ projects: [] }); await opening;
    expect(binds.newSession).not.toHaveBeenCalled();
  });

  it("folder invalidation rejects the old result and leaves newer loading owned", async () => {
    project.value = "A";
    const old = deferred(); const fresh = deferred();
    vi.mocked(api).mockReturnValueOnce(old.promise);
    const first = loadFolders();
    invalidateFolders();
    vi.mocked(api).mockReturnValueOnce(fresh.promise);
    const second = loadFolders();
    old.reject(new Error("old folders")); await first;
    expect(foldersLoading.value).toBe(true);
    fresh.resolve({ folders: [{ folder_id: "new", name: "New" }] }); await second;
    expect(foldersLoading.value).toBe(false);
    expect(folders.value).toEqual([{ folder_id: "new", name: "New" }]);
  });

  it("load-more failure preserves confirmed page and retry does not skip a page", async () => {
    project.value = "A";
    vi.mocked(api).mockResolvedValueOnce(page("A", "first", true));
    await loadSessions();
    vi.mocked(api).mockResolvedValueOnce(page("A", "first", true)).mockRejectedValueOnce(new Error("page two"));
    await loadMoreSessions();
    expect(ids()).toEqual(["first"]);
    expect(sessionPages.value).toBe(1);
    expect(sessionsHasMore.value).toBe(true);
    expect(sessionsLoadError.value).toBe(true);
    vi.mocked(api).mockResolvedValueOnce(page("A", "first", true)).mockResolvedValueOnce(page("A", "second"));
    await loadMoreSessions();
    expect(ids()).toEqual(["first", "second"]);
    expect(sessionPages.value).toBe(2);
    expect(sessionsLoadError.value).toBe(false);
  });

  it.each(["success", "failure"])("late A %s cannot replace B", async (outcome) => {
    project.value = "A";
    const old = deferred();
    vi.mocked(api).mockReturnValueOnce(old.promise);
    const a = loadSessions();
    project.value = "B"; _openGen.value++;
    vi.mocked(api).mockResolvedValueOnce(page("B", "B-new"));
    await loadSessions();
    if (outcome === "success") old.resolve(page("A", "A-old"));
    else old.reject(new Error("late A failure"));
    await a;
    expect(ids()).toEqual(["B-new"]);
    expect(sessionPages.value).toBe(1);
  });

  it("A→B→A and same-visit refreshes reject earlier A rows", async () => {
    project.value = "A";
    const old = deferred();
    vi.mocked(api).mockReturnValueOnce(old.promise);
    const a = loadSessions();
    selectProject("B"); await settle();
    selectProject("A"); await settle();
    vi.mocked(api).mockResolvedValueOnce(page("A", "latest"));
    await loadSessions();
    old.resolve(page("A", "obsolete")); await a;
    expect(ids()).toEqual(["latest"]);
  });

  it.each(["success", "failure"])("late folder %s cannot replace the new visit", async (outcome) => {
    project.value = "A";
    const old = deferred();
    vi.mocked(api).mockReturnValueOnce(old.promise);
    const a = loadFolders();
    project.value = "B"; _openGen.value++;
    vi.mocked(api).mockResolvedValueOnce({ folders: [{ folder_id: "B-folder", name: "B" }] });
    await loadFolders();
    if (outcome === "success") old.resolve({ folders: [{ folder_id: "A-folder", name: "A" }] });
    else old.reject(new Error("late folders"));
    await a;
    expect(folders.value).toEqual([{ folder_id: "B-folder", name: "B" }]);
    expect(_foldersFor.value).toBe("B");
  });

  it("an obsolete load-more finally cannot clear the new load-more", async () => {
    project.value = "A";
    vi.mocked(api).mockResolvedValueOnce(page("A", "a", true));
    await loadSessions();
    const old = deferred(); vi.mocked(api).mockReturnValueOnce(old.promise);
    const moreA = loadMoreSessions();
    project.value = "B"; _openGen.value++;
    vi.mocked(api).mockResolvedValueOnce(page("B", "b", true));
    await loadSessions();
    const fresh = deferred(); vi.mocked(api).mockReturnValueOnce(fresh.promise);
    const moreB = loadMoreSessions();
    old.reject(new Error("obsolete page")); await moreA;
    expect(_sessionsLoadingMore.value).toBe(true);
    expect(ids()).toEqual(["b"]);
    fresh.resolve(page("B", "b-final")); await moreB;
    expect(_sessionsLoadingMore.value).toBe(false);
    expect(ids()).toEqual(["b-final"]);
  });

  it("a project metadata response cannot navigate after a newer project open", async () => {
    const old = deferred(); vi.mocked(api).mockReturnValueOnce(old.promise);
    const a = openProject("A");
    await openProject("B");
    vi.mocked(binds.newSession).mockClear();
    old.resolve({ projects: [] }); await a;
    expect(project.value).toBe("B");
    expect(binds.openConversation).not.toHaveBeenCalled();
    expect(binds.newSession).not.toHaveBeenCalled();
  });

  it.each(["failure", "malformed"])("a %s sessions read must not create an empty-project session", async (outcome) => {
    vi.mocked(api).mockImplementation(async (path) => {
      if (path.startsWith("/frames?")) {
        if (outcome === "failure") throw new Error("unavailable");
        return { unexpected: [] };
      }
      return path.includes("/folders") ? { folders: [] } : { projects: [] };
    });
    await openProject("A");
    expect(binds.newSession).not.toHaveBeenCalled();
    expect(binds.openConversation).not.toHaveBeenCalled();
  });
});
