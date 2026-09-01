import { afterEach, describe, expect, it, vi } from "vitest";

const loadSessionsMock = vi.hoisted(() => vi.fn<() => Promise<void>>());

vi.mock("../sessions/load", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../sessions/load")>();
  return { ...actual, loadSessions: loadSessionsMock };
});

import { isContractStub, isReady } from "../../compat/stub";
import { resetStoreFields } from "../../stores/signal-field";
import {
  _titleName,
  currentId,
  feedback,
  project,
  sessions,
} from "../../stores/session";
import {
  pendingExecutionId,
  pendingRequestId,
  running,
  ws,
} from "../../stores/stream";
import { installMessages, messagesReady } from "./index";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify(body),
  } as Response;
}

function responseBody(path: string): unknown {
  if (path.includes("/messages?")) return { messages: [], has_earlier: false };
  if (path.endsWith("/steps")) return { steps: [] };
  if (path.endsWith("/status")) return { running: false, status: "completed" };
  return {};
}

afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  loadSessionsMock.mockReset();
  resetStoreFields();
});

describe("F-10 window exports", () => {
  it("assigns real implementations; isReady passes and typeof is not the test", () => {
    const target: Record<string, unknown> = {};
    installMessages(target);
    expect(isReady(target.openConversation)).toBe(true);
    expect(isReady(target.down)).toBe(true);
    // The fetch trio belongs to F-13, which also drives the earlier-messages
    // store and its hint. Both lanes ported it; main.tsx imports sessions
    // before messages, so claiming it here would overwrite that copy in
    // silence rather than conflict. Asserted as absent so the split holds.
    expect(target.fetchRecentMessages).toBeUndefined();
    expect(target.fetchOlderMessages).toBeUndefined();
    expect(target.fetchAllMessages).toBeUndefined();
    expect(isReady(target._mdStableCut)).toBe(true);
    expect(isContractStub(target.openConversation)).toBe(false);
    expect(messagesReady(target)).toBe(true);
    // F-05 stubs are functions too — a typeof check would lie. The assigned
    // openConversation is a real async function, not the throwing placeholder.
    expect(typeof target.openConversation).toBe("function");
    expect(isContractStub(target.down)).toBe(false);
  });

  it("restores running-session state through the window-owned opener", async () => {
    vi.useFakeTimers();
    const requested: string[] = [];
    vi.stubGlobal("fetch", async (input: string | URL | Request) => {
      const path = String(input);
      requested.push(path);
      const body = path.endsWith("/status")
        ? { running: true, status: "running" }
        : path.includes("/messages?")
          ? { messages: [], has_earlier: false }
          : path.endsWith("/steps")
            ? { steps: [] }
            : {};
      return {
        ok: true,
        status: 200,
        text: async () => JSON.stringify(body),
      } as Response;
    });
    sessions.value = [
      { id: "frame/9", project_id: "proj/1", name: "Running target" },
    ];
    pendingRequestId.value = "old-request";
    pendingExecutionId.value = "old-execution";
    const target: Record<string, unknown> = {};
    installMessages(target);

    await (target.openConversation as (
      fid: string,
      pid?: string | null,
    ) => Promise<void>)("frame/9", "proj/1");

    expect(currentId.value).toBe("frame/9");
    expect(project.value).toBe("proj/1");
    expect(_titleName.value).toBe("Running target");
    expect(running.value).toBe(true);
    expect(pendingRequestId.value).toBeNull();
    expect(pendingExecutionId.value).toBeNull();
    expect(requested).toEqual(
      expect.arrayContaining([
        "/api/v1/frames/frame%2F9/status",
        "/api/v1/frames/frame%2F9/plan",
      ]),
    );
  });

  it("does not let a late session load overwrite a newer title or feedback", async () => {
    vi.useFakeTimers();
    const loadA = deferred<void>();
    const loadB = deferred<void>();
    loadSessionsMock
      .mockImplementationOnce(() => loadA.promise)
      .mockImplementationOnce(() => loadB.promise);
    const requested: string[] = [];
    vi.stubGlobal("fetch", async (input: string | URL | Request) => {
      const path = String(input);
      requested.push(path);
      if (path.endsWith("/feedback")) {
        return jsonResponse({ feedback: { owner: path.includes("frame-B") ? "B" : "A" } });
      }
      return jsonResponse(responseBody(path));
    });
    sessions.value = [];
    const target: Record<string, unknown> = {};
    installMessages(target);
    const open = target.openConversation as (
      fid: string,
      pid?: string | null,
    ) => Promise<void>;

    const openingA = open("frame-A", "project-A");
    const openingB = open("frame-B", "project-B");
    sessions.value = [
      { id: "frame-B", project_id: "project-B", name: "Newer B" },
    ];
    loadB.resolve();
    await openingB;
    loadA.resolve();
    await openingA;

    expect(currentId.value).toBe("frame-B");
    expect(_titleName.value).toBe("Newer B");
    expect(feedback.value).toEqual({ owner: "B" });
    expect(requested).not.toContain("/api/v1/frames/frame-A/feedback");
  });

  it.each(["status", "plan"] as const)(
    "does not subscribe stale A when its %s request rejects after B opens",
    async (blockedStage) => {
      vi.useFakeTimers();
      sessions.value = [
        { id: "frame-A", project_id: "project-A", name: "A" },
        { id: "frame-B", project_id: "project-B", name: "B" },
      ];
      const gate = deferred<Response>();
      const reached = deferred<void>();
      const requested: string[] = [];
      vi.stubGlobal("fetch", (input: string | URL | Request) => {
        const path = String(input);
        requested.push(path);
        if (path === `/api/v1/frames/frame-A/${blockedStage}`) {
          reached.resolve();
          return gate.promise;
        }
        return Promise.resolve(jsonResponse(responseBody(path)));
      });
      const send = vi.fn();
      ws.value = {
        readyState: 1,
        send,
        onopen: null,
        onclose: null,
        onmessage: null,
      };
      const target: Record<string, unknown> = {};
      installMessages(target);
      const open = target.openConversation as (
        fid: string,
        pid?: string | null,
      ) => Promise<void>;

      const openingA = open("frame-A", "project-A");
      await reached.promise;
      await open("frame-B", "project-B");
      gate.reject(new Error(`stale ${blockedStage} failed`));
      await openingA;

      const viewed = send.mock.calls
        .map(([raw]) => JSON.parse(String(raw)) as { type?: string; root_frame_id?: string })
        .filter((message) => message.type === "view_session")
        .map((message) => message.root_frame_id);
      expect(currentId.value).toBe("frame-B");
      expect(viewed).toEqual(["frame-B"]);
      if (blockedStage === "status") {
        expect(requested).not.toContain("/api/v1/frames/frame-A/plan");
      }
    },
  );

  it("awaits annotation restoration in order and contains lane rejection", async () => {
    vi.useFakeTimers();
    sessions.value = [
      { id: "frame-A", project_id: "project-A", name: "A" },
      { id: "frame-B", project_id: "project-B", name: "B" },
    ];
    vi.stubGlobal("fetch", async (input: string | URL | Request) =>
      jsonResponse(responseBody(String(input))),
    );
    const annotationLoad = deferred<void>();
    const reconcileA = vi.fn();
    vi.stubGlobal("loadAnnotations", vi.fn(() => annotationLoad.promise));
    vi.stubGlobal("reconcileLastAdmission", reconcileA);
    const target: Record<string, unknown> = {};
    installMessages(target);
    const open = target.openConversation as (
      fid: string,
      pid?: string | null,
    ) => Promise<void>;

    await open("frame-A", "project-A");
    expect(reconcileA).not.toHaveBeenCalled();
    annotationLoad.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(reconcileA).toHaveBeenCalledTimes(1);

    const reconcileB = vi.fn();
    vi.stubGlobal("loadAnnotations", vi.fn(async () => {
      throw new Error("annotation restore failed");
    }));
    vi.stubGlobal("reconcileLastAdmission", reconcileB);
    await open("frame-B", "project-B");
    await Promise.resolve();
    await Promise.resolve();
    expect(reconcileB).not.toHaveBeenCalled();
  });
});
