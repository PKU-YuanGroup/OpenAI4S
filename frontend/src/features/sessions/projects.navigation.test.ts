import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({ api: vi.fn(), apiErrorText: String }));
vi.mock("./dashboard", () => ({ showDashboard: vi.fn(), showWorkspace: vi.fn() }));

import { _openGen, currentId, folders, project, sessions } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { api } from "./api";
import { binds } from "./binds";
import { loadSessions } from "./load";
import { openProject, selectProject } from "./projects";

function deferred() {
  let resolve!: (value: unknown) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise((answer, fail) => { resolve = answer; reject = fail; });
  return { promise, resolve, reject };
}

function response(path: string): unknown {
  if (path.startsWith("/projects?")) return { projects: [{ project_id: "A" }, { project_id: "B" }] };
  if (path.startsWith("/frames?")) {
    const id = new URLSearchParams(path.split("?")[1]).get("project_id");
    return { frames: [{ id: `session-${id}`, project_id: id }] };
  }
  if (path.endsWith("/folders")) return { folders: [{ folder_id: `folder-${path.split("/")[2]}` }] };
  throw new Error(`unexpected request: ${path}`);
}

beforeEach(() => {
  resetStoreFields();
  vi.mocked(api).mockReset().mockImplementation(async (path) => response(path));
  binds.openConversation = vi.fn((fid: string, pid?: string | null) => {
    // The child takes its own navigation generation when ownership transfers.
    _openGen.value += 1;
    currentId.value = fid;
    project.value = pid || null;
  });
  binds.newSession = vi.fn();
});

describe("project navigation owns every pending list read", () => {
  it.each(["B", "A"])("drops an earlier project click after a later navigation to %s, including the same project", async (latest) => {
    const old = deferred();
    vi.mocked(api).mockReturnValueOnce(old.promise);
    const openingA = openProject("A");
    await openProject(latest);
    expect(currentId.value).toBe(`session-${latest}`);

    old.resolve(response("/projects?limit=100"));
    await openingA;
    expect(project.value).toBe(latest);
    expect(currentId.value).toBe(`session-${latest}`);
    expect(binds.openConversation).toHaveBeenCalledTimes(1);
    expect(binds.newSession).not.toHaveBeenCalled();
  });

  it.each(["frames", "folders", "frames error", "folders error"])("drops A's late %s without replacing B's lists or reopening A", async (kind) => {
    const old = deferred();
    const heldPath = kind.startsWith("frames") ? "/frames?limit=100&project_id=A" : "/projects/A/folders";
    let held = false;
    vi.mocked(api).mockImplementation(async (path) => {
      if ((kind.startsWith("frames") && path.startsWith("/frames?") && path.includes("project_id=A")) || path === heldPath) {
        held = true;
        return old.promise;
      }
      return response(path);
    });
    const openingA = openProject("A");
    await vi.waitFor(() => expect(held).toBe(true));
    await openProject("B");
    const expectedSessions = sessions.value;
    const expectedFolders = folders.value;

    if (kind.endsWith("error")) old.reject(new Error("old navigation failed"));
    else old.resolve(response(heldPath));
    await openingA;
    expect(project.value).toBe("B");
    expect(currentId.value).toBe("session-B");
    expect(sessions.value).toEqual(expectedSessions);
    expect(folders.value).toEqual(expectedFolders);
    expect(binds.openConversation).toHaveBeenCalledTimes(1);
    expect(binds.newSession).not.toHaveBeenCalled();
  });


  it.each([
    { choices: ["B"] },
    { choices: ["B", "A"] },
  ])("hands the view back to the open conversation when menu filtering supersedes a pending project open: $choices", async ({ choices }) => {
    project.value = "A";
    currentId.value = "still-loading-session";
    // The generation that conversation's in-flight reads were issued under.
    const owned = _openGen.value;
    const old = deferred();
    vi.mocked(api).mockReturnValueOnce(old.promise);
    const opening = openProject("A");
    // Entry retires it: from here those reads stand down -- "load earlier" keeps
    // its loading flag, a history still loading never paints. Capturing the
    // generation only after this line is how the ownerless view went unseen.
    expect(_openGen.value).not.toBe(owned);
    for (const choice of choices) selectProject(choice);
    await vi.waitFor(() => expect(sessions.value).toEqual([{ id: `session-${choices.at(-1)}`, project_id: choices.at(-1) }]));
    expect(currentId.value).toBe("still-loading-session");
    expect(binds.openConversation).not.toHaveBeenCalled();
    old.resolve(response("/projects?limit=100"));
    await opening;
    // The menu still only filtered the sidebar -- same conversation, the menu's
    // project -- but the view has an owner again: one reload of that conversation,
    // which is what re-issues the reads the entry bump retired.
    expect(project.value).toBe(choices.at(-1));
    expect(currentId.value).toBe("still-loading-session");
    expect(binds.openConversation).toHaveBeenCalledTimes(1);
    expect(binds.openConversation).toHaveBeenCalledWith("still-loading-session", choices.at(-1));
    expect(binds.newSession).not.toHaveBeenCalled();
  });

  it("opens the menu's project when a dashboard open stands down with the workspace already revealed", async () => {
    // From a dashboard row there is no conversation to hand the view back to,
    // and by the second stand-down showWorkspace() has run: the workspace was
    // left visible over the previous transcript with no session open.
    const old = deferred();
    let held = false;
    vi.mocked(api).mockImplementation(async (path) => {
      if (path.startsWith("/frames?") && path.includes("project_id=A")) {
        held = true;
        return old.promise;
      }
      return response(path);
    });
    const opening = openProject("A");
    await vi.waitFor(() => expect(held).toBe(true));
    selectProject("B");
    old.resolve(response("/frames?limit=100&project_id=A"));
    await opening;
    expect(project.value).toBe("B");
    expect(currentId.value).toBe("session-B");
    expect(binds.openConversation).toHaveBeenCalledTimes(1);
    expect(binds.openConversation).toHaveBeenCalledWith("session-B", "B");
    expect(binds.newSession).not.toHaveBeenCalled();
  });

  it.each([
    // What openConversation does to the view: a new generation, a new owner.
    { overtaker: "a conversation open", overtake: () => { _openGen.value += 1; currentId.value = "another-session"; } },
    // What showDashboard does: a new generation and no conversation at all.
    { overtaker: "going Home", overtake: () => { _openGen.value += 1; currentId.value = null; } },
  ])("keeps a same-project list refresh that $overtaker overtakes", async ({ overtake }) => {
    // A rename, a delete, a finished turn: each ends in a list refresh. Opening
    // another row (or Home) while it is in flight bumps the view generation, and
    // openConversation does not reload a non-empty list -- so a refresh dropped
    // on that bump left the deleted row, the old title, the stale spinner.
    project.value = "A";
    currentId.value = "session-before";
    sessions.value = [{ id: "before-the-refresh", project_id: "A" }];
    const held = deferred();
    vi.mocked(api).mockImplementation(async (path) => (path.startsWith("/frames?") ? held.promise : response(path)));
    const refresh = loadSessions();
    overtake();
    held.resolve(response("/frames?limit=100&project_id=A"));
    await refresh;
    expect(sessions.value).toEqual([{ id: "session-A", project_id: "A" }]);
    expect(folders.value).toEqual([{ folder_id: "folder-A" }]);
  });

  it("does not reopen a project after the user has gone Home", async () => {
    const old = deferred();
    vi.mocked(api).mockReturnValueOnce(old.promise);
    const opening = openProject("A");
    // showDashboard advances this generation and clears the current session.
    _openGen.value += 1;
    currentId.value = null;
    old.resolve(response("/projects?limit=100"));
    await opening;
    expect(currentId.value).toBeNull();
    expect(binds.openConversation).not.toHaveBeenCalled();
    expect(binds.newSession).not.toHaveBeenCalled();
  });

  it("hands a current navigation to its conversation without invalidating that child's generation", async () => {
    await openProject("A");
    expect(binds.openConversation).toHaveBeenCalledWith("session-A", "A");
    expect(currentId.value).toBe("session-A");
    expect(project.value).toBe("A");
    expect(folders.value).toEqual([{ folder_id: "folder-A" }]);
  });
});
