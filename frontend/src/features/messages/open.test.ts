import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreFields } from "../../stores/signal-field";
import * as session from "../../stores/session";
import { _replayGap, running, stream, _seqSeen, _resumeTimer } from "../../stores/stream";
import { apiGet, fetchRecentMessages, fetchOlderMessages } from "./fetch";
import { openConversation, recoverConversation, alignHistoryAfterTurn } from "./open";
import { loadEarlierMessages } from "../sessions/messages";

const paint = vi.hoisted(() => ({ empty: vi.fn(), batches: vi.fn(), pageRows: vi.fn() }));
vi.mock("../sessions/transcript", async (original) => ({
  ...await original<typeof import("../sessions/transcript")>(),
  renderStored: (row: unknown) => { paint.pageRows(row); return null; },
  insertMessageByTime: vi.fn(),
}));
vi.mock("./list", async (original) => ({
  ...await original<typeof import("./list")>(),
  renderEmptySession: paint.empty,
  scheduleFramedRender: (items: unknown[], opts: { onDone?: () => void }) => {
    paint.batches(items);
    opts.onDone?.();
  },
}));

function response(body: unknown, status = 200): Response {
  return { ok: status < 400, status, text: async () => JSON.stringify(body) } as Response;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function body(path: string): unknown {
  if (path.includes("/messages?")) return { messages: [{ role: "assistant", content: "confirmed", seq: 1 }], has_earlier: false };
  if (path.endsWith("/steps")) return { steps: [] };
  if (path.endsWith("/status")) return { running: false, status: "completed" };
  return {};
}
function server(override?: (path: string) => Response | Promise<Response> | undefined) {
  const fetcher = vi.fn((input: unknown) => {
    const path = String(input);
    return Promise.resolve(override?.(path) ?? response(body(path)));
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
beforeEach(() => {
  resetStoreFields();
  session.sessions.value = [{ id: "f", name: "F" }, { id: "g", name: "G" }];
  paint.empty.mockReset(); paint.batches.mockReset(); paint.pageRows.mockReset();
});
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("history reads", () => {
  it.each([undefined, {}, { messages: null }, { messages: {} }, { messages: [null] }, { messages: [{}] }, { messages: [{ role: "user", content: [3] }] }, { messages: [], has_earlier: "false" }])("rejects malformed messages instead of inventing an empty history: %j", async (value) => {
    server(() => response(value));
    await expect(fetchRecentMessages("f")).rejects.toThrow();
    await expect(fetchOlderMessages("f", 2)).rejects.toThrow();
  });
  it("retains HTTP status, error code and support request identity", async () => {
    server(() => response({ error: "unavailable", code: "temporary_failure", request_id: "read-1" }, 503));
    await expect(apiGet("/frames/f/messages")).rejects.toMatchObject({ status: 503, code: "temporary_failure", requestId: "read-1" });
  });
  it.each([401, 403, 503])("never paints the empty welcome after HTTP %s", async (status) => {
    server((path) => path.includes("/messages?") ? response({ error: "read failed" }, status) : undefined);
    const result = await openConversation("f");
    expect(result).toMatchObject({ messagesLoaded: false, stepsLoaded: true, runStateLoaded: true, superseded: false });
    expect(paint.empty).not.toHaveBeenCalled();
  });
  it("shows valid empty history only after successful reads", async () => {
    server((path) => path.includes("/messages?") ? response({ messages: [] }) : undefined);
    expect(await openConversation("f")).toMatchObject({ messagesLoaded: true, stepsLoaded: true, runStateLoaded: true });
    expect(paint.empty).toHaveBeenCalledTimes(1);
  });
  it("keeps messages visible when steps fail and retries only GET reads", async () => {
    let failSteps = true;
    const fetcher = server((path) => path.endsWith("/steps") && failSteps ? response({ error: "steps unavailable" }, 503) : undefined);
    expect(await openConversation("f")).toMatchObject({ messagesLoaded: true, stepsLoaded: false, runStateLoaded: true });
    expect(paint.batches.mock.calls.flat(2)).toEqual(expect.arrayContaining([expect.objectContaining({ kind: "msg" })]));
    failSteps = false;
    expect(await openConversation("f")).toMatchObject({ messagesLoaded: true, stepsLoaded: true, runStateLoaded: true });
    for (const [, init] of fetcher.mock.calls as unknown as Array<[unknown, RequestInit?]>) expect(init?.method ?? "GET").toBe("GET");
  });
  const storedStep = { step_id: "step-1", seq: 0, kind: "search", created_at: 1,
    status: "done", title: null, summary: null, input: null, output: null };
  it.each([
    {},
    { ...storedStep, step_id: undefined }, { ...storedStep, step_id: 7 },
    { ...storedStep, kind: undefined }, { ...storedStep, kind: [] },
    { ...storedStep, seq: undefined }, { ...storedStep, seq: "0" }, { ...storedStep, seq: 0.5 },
    { ...storedStep, created_at: undefined }, { ...storedStep, created_at: "yesterday" },
    { ...storedStep, created_at: Infinity }, { ...storedStep, status: {} },
    { ...storedStep, title: [] }, { ...storedStep, summary: false },
  ])("retains messages and the replay gap after a malformed step: %j", async (malformed) => {
    server((path) => path.endsWith("/steps") ? response({ steps: [storedStep] }) : undefined);
    await openConversation("f");
    const heldSteps = session.historyContent.value?.steps;
    _replayGap.value = "f";
    paint.batches.mockClear();
    const fetcher = server((path) => path.endsWith("/steps") ? response({ steps: [storedStep, malformed] }) : undefined);
    expect(await recoverConversation("f")).toMatchObject({
      messagesLoaded: true, stepsLoaded: false, runStateLoaded: true, superseded: false,
    });
    expect(session.historyLoad.value?.status).toBe("partial");
    expect(session.historyLoad.value?.errors.steps).toMatch(/Invalid step record/);
    expect(session.historyContent.value?.messages).toEqual([{ role: "assistant", content: "confirmed", seq: 1 }]);
    expect(session.historyContent.value?.steps).toBe(heldSteps);
    expect(paint.batches.mock.calls.flat(2)).toEqual(expect.arrayContaining([
      expect.objectContaining({ kind: "msg", v: expect.objectContaining({ content: "confirmed" }) }),
    ]));
    expect(paint.empty).not.toHaveBeenCalled();
    expect(_replayGap.value).toBe("f");
    expect(fetcher).toHaveBeenCalledTimes(3);
    for (const [, init] of fetcher.mock.calls as unknown as Array<[unknown, RequestInit?]>) expect(init?.method ?? "GET").toBe("GET");
    const retry = server((path) => path.endsWith("/steps") ? response({ steps: [storedStep] }) : undefined);
    expect(await recoverConversation("f")).toMatchObject({ messagesLoaded: true, stepsLoaded: true, runStateLoaded: true });
    expect(_replayGap.value).toBeNull();
    expect(retry).toHaveBeenCalledTimes(3);
    for (const [, init] of retry.mock.calls as unknown as Array<[unknown, RequestInit?]>) expect(init?.method ?? "GET").toBe("GET");
  });
  it.each([
    storedStep,
    { step_id: "old-step", seq: 0, kind: "code", created_at: 0 },
    { ...storedStep, status: null },
    { ...storedStep, kind: "future-extension", status: "paused", input: "legacy raw payload" },
  ])("preserves compatible stored step fields without inventing a kind/status whitelist: %j", async (step) => {
    server((path) => path.endsWith("/steps") ? response({ steps: [step] }) : undefined);
    _replayGap.value = "f";
    expect(await openConversation("f")).toMatchObject({ messagesLoaded: true, stepsLoaded: true, runStateLoaded: true });
    expect(session.historyContent.value?.steps).toEqual([step]);
    expect(_replayGap.value).toBeNull();
  });
  it.each(["f", "g"])("late response cannot replace a newer open of %s", async (target) => {
    const gate = deferred<Response>();
    let pending = true;
    server((path) => path.includes("/frames/f/messages?") && pending ? (pending = false, gate.promise) : undefined);
    const older = openConversation("f");
    await vi.waitFor(() => expect(pending).toBe(false));
    const newer = await openConversation(target);
    gate.resolve(response({ messages: [] }));
    expect(await older).toMatchObject({ superseded: true });
    expect(newer).toMatchObject({ superseded: false });
    expect(session.currentId.value).toBe(target);
    expect(paint.empty).not.toHaveBeenCalled();
  });
  it("does not call a malformed running state a complete recovery", async () => {
    server((path) => path.endsWith("/status") ? response({ running: "false" }) : undefined);
    expect(await openConversation("f")).toMatchObject({ runStateLoaded: false });
  });
  it("retains confirmed history on a same-session network failure", async () => {
    server();
    await openConversation("f");
    const confirmed = session.historyContent.value;
    paint.batches.mockClear();
    server((path) => path.includes("/messages?") ? Promise.reject(new TypeError("offline")) : undefined);
    expect(await openConversation("f")).toMatchObject({ messagesLoaded: false });
    expect(session.historyContent.value).toBe(confirmed);
    expect(session.historyLoad.value?.status).toBe("partial");
    expect(paint.batches).not.toHaveBeenCalled();
    expect(paint.empty).not.toHaveBeenCalled();
  });
  it("keeps a late failure out of a newer same-session generation", async () => {
    const gate = deferred<Response>();
    let wait = true;
    server((path) => path.endsWith("/steps") && wait ? (wait = false, gate.promise) : undefined);
    const first = openConversation("f");
    await vi.waitFor(() => expect(wait).toBe(false));
    await openConversation("f");
    const state = session.historyLoad.value;
    gate.reject(new Error("old failure"));
    expect(await first).toMatchObject({ superseded: true });
    expect(session.historyLoad.value).toBe(state);
    expect(state?.status).toBe("loaded");
  });
  it("coalesces retry GETs within the current generation", async () => {
    server(); await openConversation("f");
    const gate = deferred<Response>();
    const fetcher = server((path) => path.endsWith("/steps") ? gate.promise : undefined);
    const one = recoverConversation("f");
    const two = recoverConversation("f");
    expect(two).toBe(one);
    expect(fetcher).toHaveBeenCalledTimes(3);
    gate.resolve(response({ steps: [] }));
    expect(await one).toMatchObject({ messagesLoaded: true, stepsLoaded: true, runStateLoaded: true });
    expect(fetcher.mock.calls.map(([path]) => String(path))).toEqual(expect.arrayContaining([
      expect.stringContaining("/messages?"), expect.stringContaining("/steps"), expect.stringContaining("/status"),
    ]));
  });
  it("holds a racing anonymous REST tail until a stopped, quiet GET", async () => {
    server(); await openConversation("f");
    const confirmed = session.historyContent.value;
    const gate = deferred<Response>();
    server((path) => path.includes("/messages?") ? gate.promise : undefined);
    _replayGap.value = "f";
    const reading = recoverConversation("f");
    const live = { wrap: { remove: vi.fn() }, text: "new text" };
    stream.value = live;
    session.noteHistoryMutation();
    _seqSeen.value.f = 8;
    gate.resolve(response({ messages: [{ content: "new text", seq: 2 }] }));
    expect(await reading).toMatchObject({ messagesLoaded: false });
    expect(stream.value).toBe(live);
    expect(session.historyContent.value).toBe(confirmed);
    expect(session.historyLoad.value?.deferred).toBe(true);
    expect(_replayGap.value).toBe("f");
    stream.value = null; running.value = false; session.noteHistoryMutation();
    server((path) => path.includes("/messages?") ? response({ messages: [
      { role: "assistant", content: "same text", seq: 1 },
      { role: "assistant", content: "same text", seq: 2 },
      { role: "assistant", content: "same text", seq: 2 },
    ] }) : undefined);
    expect(await recoverConversation("f")).toMatchObject({ messagesLoaded: true, stepsLoaded: true, runStateLoaded: true });
    expect(_replayGap.value).toBeNull();
    expect(session.historyContent.value?.messages.map((row) => row.seq)).toEqual([1, 2]);
  });
  it("does not erase an unconfirmed submission even when status says stopped", async () => {
    server(); await openConversation("f");
    session.historyUnconfirmed.value = 1;
    expect(await recoverConversation("f")).toMatchObject({ messagesLoaded: false });
    expect(session.historyLoad.value?.deferred).toBe(true);
    session.historyUnconfirmed.value = 0;
    expect(await recoverConversation("f")).toMatchObject({ messagesLoaded: true });
  });
  it("does not unlock a running task when run-state reading fails", async () => {
    server(); await openConversation("f");
    running.value = true;
    server((path) => path.endsWith("/status") ? response({ error: "unavailable" }, 503) : undefined);
    expect(await recoverConversation("f")).toMatchObject({ runStateLoaded: false });
    expect(running.value).toBe(true);
  });

  it("retains loaded earlier pages beyond the latest 300 rows on reload", async () => {
    const rows = Array.from({ length: 600 }, (_, n) => ({ role: "assistant", content: `row ${n + 1}`, seq: n + 1 }));
    server((path) => path.includes("/messages?") ? response({ messages: rows.slice(300), next_before_seq: 301, has_earlier: true }) : undefined);
    await openConversation("f");
    const bar = { querySelector: () => null, remove: vi.fn() };
    const host = { firstChild: bar, scrollHeight: 20, scrollTop: 10 };
    vi.stubGlobal("document", {
      querySelector: () => host,
      getElementById: () => bar,
      createDocumentFragment: () => ({}),
    });
    server(() => response({ messages: rows.slice(0, 300), has_earlier: false, next_before_seq: null }));
    await loadEarlierMessages();
    expect(paint.pageRows).toHaveBeenCalledTimes(300);
    expect(session.historyContent.value?.messages).toHaveLength(600);
    vi.stubGlobal("document", undefined);
    const newest = [...rows.slice(302), { role: "assistant", content: "row 601", seq: 601 }];
    newest[0] = { role: "assistant", content: "updated 303", seq: 303 };
    server((path) => path.includes("/messages?") ? response({ messages: newest, next_before_seq: 303, has_earlier: true }) : undefined);
    await openConversation("f");
    expect(session.historyContent.value?.messages).toHaveLength(601);
    expect(session.historyContent.value?.messages[0]?.seq).toBe(1);
    expect(session.historyContent.value?.messages.find((row) => row.seq === 303)?.content).toBe("updated 303");
    expect(session.msgHasEarlier.value).toBe(false);
  });
  it("projects rendering failure and a retry without an unhandled rejection", async () => {
    server(); await openConversation("f");
    const held = session.historyContent.value;
    paint.batches.mockImplementationOnce(() => { throw new Error("cannot paint"); });
    expect(await recoverConversation("f")).toMatchObject({ messagesLoaded: false });
    expect(session.historyLoad.value?.status).toBe("partial");
    expect(session.historyLoad.value?.errors.messages).toBeTruthy();
    expect(session.historyContent.value).toBe(held);
    expect(await recoverConversation("f")).toMatchObject({ messagesLoaded: true });
  });
  it("a terminal received during a read schedules one fresh alignment", async () => {
    server(); await openConversation("f");
    const gate = deferred<Response>();
    let first = true;
    const fetcher = server((path) => path.includes("/messages?") && first ? (first = false, gate.promise) : undefined);
    const reading = recoverConversation("f");
    session.noteHistoryMutation();
    running.value = false;
    alignHistoryAfterTurn("f");
    gate.resolve(response({ messages: [] }));
    expect(await reading).toMatchObject({ messagesLoaded: false });
    await vi.waitFor(() => expect(session.historyLoad.value?.status).toBe("loaded"));
    expect(fetcher).toHaveBeenCalledTimes(6);
  });

  it("an old admission cannot settle a submission after navigating away and back", () => {
    const old = session.beginHistorySubmission();
    expect(session.historyUnconfirmed.value).toBe(1);
    session.resetHistorySubmissions();
    const current = session.beginHistorySubmission();
    old();
    expect(session.historyUnconfirmed.value).toBe(1);
    current(); current();
    expect(session.historyUnconfirmed.value).toBe(0);
  });

});

it.each(["success", "failure"])("a newer open of the same session retires an older-page %s loading lease", async (outcome) => {
  const newest = { messages: [{ role: "assistant", content: "newest", seq: 301 }], next_before_seq: 301, has_earlier: true };
  server((path) => path.includes("/messages?") ? response(newest) : undefined);
  await openConversation("f");
  const bar = { querySelector: () => null, remove: vi.fn() };
  const host = { firstChild: bar, scrollHeight: 20, scrollTop: 10 };
  vi.stubGlobal("document", {
    querySelector: () => host,
    getElementById: () => bar,
    createDocumentFragment: () => ({}),
  });
  const gate = deferred<Response>();
  server(() => gate.promise);
  const older = loadEarlierMessages();
  expect(session._msgEarlierLoading.value).toBe(true);
  vi.stubGlobal("document", undefined);
  server((path) => path.includes("/messages?") ? response(newest) : undefined);
  await openConversation("f");
  if (outcome === "success") gate.resolve(response({ messages: [{ role: "assistant", content: "older", seq: 300 }], has_earlier: false }));
  else gate.reject(new Error("old page failed"));
  await older;
  expect(session._msgEarlierLoading.value).toBe(false);
  expect(session.msgHasEarlier.value).toBe(true);
  expect(session.historyContent.value?.messages.map((row) => row.seq)).toEqual([301]);
});


describe("recovery after a missed terminal event", () => {
  it("retires a quiet orphaned stream only after stopped status and fresh history", async () => {
    server(); await openConversation("f");
    const old = session.historyContent.value;
    const live = { wrap: { remove: vi.fn() }, text: "unfinished stream" };
    stream.value = live; running.value = true; _replayGap.value = "f";
    const status = deferred<Response>();
    const page = deferred<Response>();
    let stopped = false;
    const ordered: string[] = [];
    const fetcher = server((path) => {
      if (path.endsWith("/status")) return status.promise;
      if (path.includes("/messages?")) { ordered.push(stopped ? "after stopped" : "before stopped"); return page.promise; }
    });
    const reading = recoverConversation("f");
    expect(stream.value).toBe(live);
    expect(session.historyContent.value).toBe(old);
    stopped = true; status.resolve(response({ running: false, status: "completed" }));
    await vi.waitFor(() => expect(ordered.length).toBe(1));
    page.resolve(response({ messages: [
      { role: "assistant", content: "confirmed", seq: 1 },
      { role: "assistant", content: "completed stream", seq: 2 },
    ], has_earlier: false }));
    expect(await reading).toMatchObject({ messagesLoaded: true, stepsLoaded: true, runStateLoaded: true });
    expect(ordered).toEqual(["after stopped"]);
    expect(stream.value).toBeNull();
    expect(running.value).toBe(false);
    expect(_replayGap.value).toBeNull();
    expect(session.historyContent.value?.messages.map((row) => row.seq)).toEqual([1, 2]);
    for (const [, init] of fetcher.mock.calls as unknown as Array<[unknown, RequestInit?]>) expect(init?.method ?? "GET").toBe("GET");
  });
  it.each(["running", "status failure", "messages failure"])("retains its stream after %s", async (mode) => {
    server(); await openConversation("f");
    const live = { text: "still visible" };
    stream.value = live; running.value = true; _replayGap.value = "f";
    server((path) => {
      if (path.endsWith("/status") && mode === "running") return response({ running: true, status: "processing" });
      if (path.endsWith("/status") && mode === "status failure") return response({ error: "status unavailable" }, 503);
      if (path.includes("/messages?") && mode === "messages failure") return response({ error: "messages unavailable" }, 503);
    });
    expect(await recoverConversation("f")).toMatchObject({ messagesLoaded: false });
    expect(stream.value).toBe(live);
    expect(_replayGap.value).toBe("f");
  });
  it("does not retire a stream changed after a stopped response", async () => {
    server(); await openConversation("f");
    const live = { text: "old" };
    stream.value = live; running.value = true; _replayGap.value = "f";
    const page = deferred<Response>();
    let requested = false;
    server((path) => path.includes("/messages?") ? (requested = true, page.promise) : undefined);
    const reading = recoverConversation("f");
    await vi.waitFor(() => expect(requested).toBe(true));
    live.text = "a new chunk"; session.noteHistoryMutation();
    page.resolve(response({ messages: [{ role: "assistant", content: "old", seq: 1 }], has_earlier: false }));
    expect(await reading).toMatchObject({ messagesLoaded: false });
    expect(stream.value).toBe(live);
    expect(running.value).toBe(true);
    expect(_replayGap.value).toBe("f");
  });
});

describe("branch history replacement", () => {
  it.each([false, true])("does not preserve another branch's earlier rows when has_earlier is %s", async (hasEarlier) => {
    server(); await openConversation("f");
    server((path) => path.includes("/messages?") ? response({
      messages: [{ role: "assistant", content: "branch B", seq: 400 }],
      has_earlier: hasEarlier, next_before_seq: 400,
    }) : undefined);
    expect(await openConversation("f", undefined, { resetHistory: true })).toMatchObject({ messagesLoaded: true });
    expect(session.historyContent.value?.messages.map((row) => row.seq)).toEqual([400]);
    expect(session.msgHasEarlier.value).toBe(hasEarlier);
    expect(session.msgCursor.value).toBe(400);
  });
  it("does not show previous-branch confirmed rows as current when the replacement GET fails", async () => {
    server(); await openConversation("f");
    expect(session.historyContent.value).not.toBeNull();
    server((path) => path.includes("/messages?") ? response({ error: "unavailable" }, 503) : undefined);
    expect(await openConversation("f", undefined, { resetHistory: true })).toMatchObject({ messagesLoaded: false });
    expect(session.historyContent.value).toBeNull();
    expect(session.historyLoad.value?.status).toBe("error");
  });
});


it("keeps the missed-terminal watchdog active while the status still says running", async () => {
  vi.useFakeTimers();
  server(); await openConversation("f");
  const live = { text: "waiting for terminal" };
  stream.value = live; running.value = true; _replayGap.value = "f";
  let reads = 0;
  server((path) => path.endsWith("/status") ? response({ running: ++reads === 1, status: reads === 1 ? "processing" : "completed" }) : undefined);
  vi.stubGlobal("openConversation", openConversation);
  expect(await recoverConversation("f")).toMatchObject({ messagesLoaded: false, runStateLoaded: true });
  expect(_resumeTimer.value).not.toBeNull();
  await vi.advanceTimersByTimeAsync(2000);
  expect(stream.value).toBeNull();
  expect(running.value).toBe(false);
  expect(session.historyLoad.value?.status).toBe("loaded");
  expect(_replayGap.value).toBeNull();
});


it.each(["open", "retry"])("removes only stale welcome decoration when %s history fails", async (entry) => {
  server((path) => path.includes("/messages?") ? response({ messages: [], has_earlier: false }) : undefined);
  await openConversation("f");
  expect(paint.empty).toHaveBeenCalledTimes(1);
  const cached = session.historyContent.value;
  const welcome = { remove: vi.fn() };
  const confirmed = { remove: vi.fn() };
  const host = {
    innerHTML: "confirmed content", children: [welcome, confirmed],
    querySelector: vi.fn((selector: string) => selector === ":scope > .empty-session" ? welcome : null),
  };
  vi.stubGlobal("document", {
    querySelector: (selector: string) => selector === "#messages" ? host : null,
    getElementById: (id: string) => id === "messages" ? host : id === "jump-pill" ? {} : null,
  });
  const pending = deferred<Response>();
  server((path) => path.includes("/messages?") ? pending.promise : undefined);
  const reading = entry === "open" ? openConversation("f") : recoverConversation("f");
  expect(welcome.remove).toHaveBeenCalled();
  expect(confirmed.remove).not.toHaveBeenCalled();
  expect(host.innerHTML).toBe("confirmed content");
  pending.resolve(response({ error: "temporarily unavailable" }, 503));
  expect(await reading).toMatchObject({ messagesLoaded: false });
  expect(paint.empty).toHaveBeenCalledTimes(1);
  expect(session.historyContent.value).toBe(cached);
});
