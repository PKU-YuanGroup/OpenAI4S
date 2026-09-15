/**
 * newSession publishes the new id BEFORE openConversation runs, so that a file
 * selected during the sidebar refresh binds to the new conversation. That
 * publish made openConversation see the new frame as its own predecessor and
 * skip releasing the previous one; and on the shared path newSession resolved
 * before the conversation had been opened at all.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const wsMock = vi.hoisted(() => ({ sub: vi.fn(), unsub: vi.fn() }));
const notebookMock = vi.hoisted(() => ({ resetNotebookCellCaches: vi.fn() }));
const recoveryMock = vi.hoisted(() => ({ recoverConversation: vi.fn(async () => {}), callLane: vi.fn(), hint: vi.fn() }));
const openMock = vi.hoisted(() => ({
  openConversation: vi.fn(async (fid: string) => {
    openMock.opened.push(fid);
  }),
  opened: [] as string[],
}));
const loadMock = vi.hoisted(() => ({
  loadSessions: vi.fn(async () => {}),
  loadProjects: vi.fn(async () => {}),
  loadSessionsForNavigation: vi.fn(async () => ({ status: "loaded" })),
}));

vi.mock("../ws/connect", () => wsMock);
vi.mock("../notebook/chrome", () => notebookMock);
vi.mock("../messages/open", () => ({ openConversation: openMock.openConversation, recoverConversation: recoveryMock.recoverConversation }));
vi.mock("./lane", () => ({ callLane: recoveryMock.callLane }));
vi.mock("./load", () => loadMock);
vi.mock("./dashboard", () => ({ showDashboard: vi.fn(), showWorkspace: vi.fn() }));
vi.mock("./projects", () => ({ renderProjMenu: vi.fn() }));
vi.mock("./chrome", () => ({ hint: recoveryMock.hint }));

import { _msgEarlierLoading, _openGen, currentId, project } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { UPLOAD_STATE } from "../chrome/upload";
import { newSession, routeInitialView } from "./conversation";

function stubDom(): void {
  const workspace = { classList: { contains: () => false } };
  vi.stubGlobal("document", {
    querySelector: (sel: string) => (sel === "#workspace" ? workspace : null),
    getElementById: () => null,
  });
  // The shared creation adopts its frame through the host lane (window),
  // which the session and messages boots install at runtime.
  vi.stubGlobal("window", {
    loadSessions: loadMock.loadSessions,
    openConversation: openMock.openConversation,
  });
  vi.stubGlobal("fetch", () =>
    Promise.resolve({
      ok: true,
      status: 200,
      text: () => Promise.resolve(JSON.stringify({ id: "frame_B" })),
    }),
  );
}

describe("newSession", () => {
  beforeEach(() => {
    resetStoreFields();
    UPLOAD_STATE.creations.clear();
    wsMock.sub.mockClear();
    wsMock.unsub.mockClear();
    notebookMock.resetNotebookCellCaches.mockClear();
    loadMock.loadProjects.mockReset().mockResolvedValue(undefined);
    loadMock.loadSessions.mockReset().mockResolvedValue(undefined);
    loadMock.loadSessionsForNavigation.mockReset().mockResolvedValue({ status: "loaded" });
    recoveryMock.recoverConversation.mockReset().mockResolvedValue(undefined);
    recoveryMock.callLane.mockReset();
    recoveryMock.hint.mockReset();
    openMock.openConversation.mockReset().mockImplementation(async (fid) => {
      _openGen.value++;
      openMock.opened.push(fid);
    });
    openMock.opened.length = 0;
    stubDom();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("releases the previous conversation before publishing the new id", async () => {
    currentId.value = "frame_A";
    project.value = "proj_1";
    await newSession();
    expect(wsMock.unsub).toHaveBeenCalledWith("frame_A");
    expect(notebookMock.resetNotebookCellCaches).toHaveBeenCalledWith("frame_A", "frame_B");
    // ...and the release precedes the publish, so openConversation's own
    // `previousFid !== fid` test is not what this relies on.
    const unsubOrder = wsMock.unsub.mock.invocationCallOrder[0] ?? Number.POSITIVE_INFINITY;
    const subOrder = wsMock.sub.mock.invocationCallOrder[0] ?? Number.NEGATIVE_INFINITY;
    expect(unsubOrder).toBeLessThan(subOrder);
    expect(currentId.value).toBe("frame_B");
    expect(openMock.opened).toEqual(["frame_B"]);
  });

  it("on the shared path resolves only after the conversation has opened", async () => {
    currentId.value = null;
    project.value = "proj_1";
    await newSession("proj_1");
    // The shared creation adopted the frame itself; newSession waited for it.
    expect(openMock.opened).toEqual(["frame_B"]);
    expect(currentId.value).toBe("frame_B");
    expect(wsMock.unsub).not.toHaveBeenCalled();
  });

  it("a late accepted creation cannot adopt after A→B→A", async () => {
    currentId.value = "previous"; project.value = "A";
    let answer!: (value: unknown) => void;
    vi.stubGlobal("fetch", () => new Promise((resolve) => { answer = resolve; }));
    const creation = newSession();
    project.value = "B"; _openGen.value++;
    project.value = "A"; _openGen.value++;
    answer({ ok: true, status: 200, text: async () => JSON.stringify({ id: "accepted" }) });
    await creation;
    expect(currentId.value).toBe("previous");
    expect(openMock.openConversation).not.toHaveBeenCalled();
    expect(wsMock.unsub).not.toHaveBeenCalled();
  });

  it("adoption parked on directory read cannot reopen after another visit", async () => {
    currentId.value = "previous"; project.value = "A";
    let finish!: () => void;
    loadMock.loadSessions.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const creation = newSession();
    for (let i = 0; i < 12; i++) await Promise.resolve();
    expect(currentId.value).toBe("frame_B");
    _openGen.value += 2; // Same project and published frame, different visit.
    finish(); await creation;
    expect(openMock.openConversation).not.toHaveBeenCalled();
  });

  it("shared adoption still awaits opened when the opener advances navigation", async () => {
    project.value = "A";
    let finish!: () => void;
    openMock.openConversation.mockImplementationOnce(async () => {
      _openGen.value++;
      await new Promise<void>((resolve) => { finish = resolve; });
    });
    let settled = false;
    const creation = newSession("A").then(() => { settled = true; });
    for (let i = 0; i < 12; i++) await Promise.resolve();
    expect(openMock.openConversation).toHaveBeenCalledOnce();
    expect(settled).toBe(false);
    finish(); await creation;
    expect(currentId.value).toBe("frame_B");
  });

  it.each(["first read", "second creation"])("a later New-session intent wins when %s finishes first", async (order) => {
    currentId.value = "previous"; project.value = "A";
    const answers: Array<(value: unknown) => void> = [];
    vi.stubGlobal("fetch", () => new Promise((resolve) => { answers.push(resolve); }));
    let finishFirstRead!: () => void;
    loadMock.loadSessions.mockReturnValueOnce(new Promise((resolve) => { finishFirstRead = resolve; }));
    const response = (id: string) => ({ ok: true, status: 200, text: async () => JSON.stringify({ id }) });
    const first = newSession();
    answers[0]!(response("first"));
    for (let i = 0; i < 12; i++) await Promise.resolve();
    expect(currentId.value).toBe("first");
    const second = newSession();
    expect(answers).toHaveLength(2); // Both accepted New clicks are distinct.
    if (order === "first read") {
      finishFirstRead(); await first;
      answers[1]!(response("second")); await second;
    } else {
      answers[1]!(response("second")); await second;
      finishFirstRead(); await first;
    }
    expect(currentId.value).toBe("second");
    expect(openMock.opened).toEqual(["second"]);
  });

  it("a failed fresh intent recovers the retained frame using only reads", async () => {
    currentId.value = "previous"; project.value = "B";
    _msgEarlierLoading.value = true;
    const before = _openGen.value;
    recoveryMock.recoverConversation.mockImplementationOnce(async () => { recoveryMock.hint(""); });
    const fetch = vi.fn(async () => ({ ok: false, status: 503, text: async () => JSON.stringify({ error: "unavailable" }) }));
    vi.stubGlobal("fetch", fetch);
    await newSession();
    expect(fetch).toHaveBeenCalledOnce();
    expect(currentId.value).toBe("previous");
    expect(_openGen.value).toBeGreaterThan(before);
    expect(_msgEarlierLoading.value).toBe(false);
    expect(loadMock.loadSessionsForNavigation).toHaveBeenCalledWith({ projectId: "B", generation: before + 1 });
    expect(recoveryMock.recoverConversation).toHaveBeenCalledWith("previous", before + 1);
    expect(recoveryMock.callLane).toHaveBeenCalledWith("loadArtifacts", "previous");
    expect(recoveryMock.hint).toHaveBeenLastCalledWith(expect.stringContaining("unavailable"), true);
    expect(openMock.opened).toEqual([]);
    expect(wsMock.unsub).not.toHaveBeenCalled();
  });

  it("a deep link parked on metadata cannot reopen after Home", async () => {
    vi.stubGlobal("location", { pathname: "/projects/A/frames/old" });
    let finish!: () => void;
    loadMock.loadProjects.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const routing = routeInitialView();
    _openGen.value++; currentId.value = null;
    finish(); await routing;
    expect(openMock.openConversation).not.toHaveBeenCalled();
    expect(loadMock.loadSessions).not.toHaveBeenCalled();
  });

  it("a failed intent's delayed recovery cannot post its error into a newer visit", async () => {
    currentId.value = "previous"; project.value = "A";
    let finish!: () => void;
    recoveryMock.recoverConversation.mockImplementationOnce(() => new Promise<void>((resolve) => { finish = resolve; }));
    vi.stubGlobal("fetch", async () => ({ ok: false, status: 503, text: async () => JSON.stringify({ error: "old failure" }) }));
    const creation = newSession();
    for (let i = 0; i < 12; i++) await Promise.resolve();
    expect(recoveryMock.recoverConversation).toHaveBeenCalledOnce();
    _openGen.value++; project.value = "B"; currentId.value = "newer";
    recoveryMock.hint.mockClear();
    finish(); await creation;
    expect(recoveryMock.hint).not.toHaveBeenCalled();
    expect(currentId.value).toBe("newer");
  });
});
