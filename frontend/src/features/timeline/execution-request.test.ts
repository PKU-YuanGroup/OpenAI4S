/**
 * The workbench had every part of the scoped-interrupt mechanism except the
 * function that uses them. Both cancel call sites went through
 * `callLane("scopedExecutionRequest", ...)`, a name nothing ever assigned, and
 * `callLane` answers a missing name with `undefined` rather than throwing --
 * so Stop resolved to undefined, no request was ever sent, and nothing said so.
 *
 * These tests assert the two properties that made it worth porting rather than
 * reinventing: an interrupt names an EXACT execution, and with no resolvable
 * owner it refuses instead of sending an unscoped one.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { pendingReplIdentity } from "../../stores/notebook";
import { resetStoreFields } from "../../stores/signal-field";
import { exactExecutionIdentity, scopedExecutionRequest } from "./execution-request";
import { S } from "./s";

type Call = { path: string; method?: string; body?: Record<string, unknown> };

let calls: Call[] = [];
let routes: Record<string, unknown> = {};

function stubFetch(): void {
  vi.stubGlobal("fetch", (url: string, opts?: { method?: string; body?: string }) => {
    const path = String(url).replace(/^.*\/api\/v1/, "");
    calls.push({
      path,
      method: opts?.method,
      body: opts?.body ? JSON.parse(opts.body) : undefined,
    });
    // `api()` reads r.text() and parses it itself, so a json()-only stub
    // throws inside the client rather than exercising the route.
    if (!(path in routes)) {
      return Promise.resolve({ ok: false, status: 404, text: () => Promise.resolve("{}") });
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      text: () => Promise.resolve(JSON.stringify(routes[path])),
    });
  });
}

beforeEach(() => {
  calls = [];
  routes = {};
  resetStoreFields();
  S.currentId = null;
  S.executionQueue = null;
  S.executionIdentity = null;
  pendingReplIdentity.value = null;
  vi.stubGlobal("document", { querySelector: () => null, getElementById: () => null });
  vi.stubGlobal("window", {});
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const OWNER = { kind: "agent", id: "job-1" };

describe("scopedExecutionRequest", () => {
  it("names the exact execution in the interrupt body", async () => {
    S.currentId = "f1";
    S.executionQueue = { owner: { execution_id: "exec-9", owner: OWNER }, queue: [] };
    routes["/frames/f1/cancel"] = { ok: true, scope: "running" };

    const result = await scopedExecutionRequest("f1", "cancel", "composer cancel");

    const posted = calls.find((c) => c.method === "POST");
    expect(posted?.path).toBe("/frames/f1/cancel");
    expect(posted?.body).toEqual({
      execution_id: "exec-9",
      owner: OWNER,
      owner_id: "job-1",
      reason: "composer cancel",
    });
    expect(result).toBeTruthy();
  });

  it("refuses rather than sending an interrupt that names nothing", async () => {
    // No cache, and the queue routes 404: there is no owner to aim at.
    const result = await scopedExecutionRequest("f-unknown", "cancel", "composer cancel");

    expect(result).toEqual({ ok: false, reason: "no_exact_owner" });
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("fetches the queue for a frame that is not the open one", async () => {
    // The cached paths are guarded on `frameId === S.currentId`; answering for
    // another frame out of this frame's cache would name the wrong execution.
    S.currentId = "f1";
    S.executionQueue = { owner: { execution_id: "exec-open", owner: OWNER }, queue: [] };
    routes["/frames/f2/execution-queue"] = {
      owner: { execution_id: "exec-other", owner: { kind: "agent", id: "job-2" } },
      queue: [],
    };
    routes["/frames/f2/cancel"] = { ok: true, scope: "running" };

    await scopedExecutionRequest("f2", "cancel", "session menu cancel");

    const posted = calls.find((c) => c.method === "POST");
    expect(posted?.path).toBe("/frames/f2/cancel");
    expect(posted?.body?.execution_id).toBe("exec-other");
    // and the open frame's cache is untouched by another frame's lookup
    expect(S.executionQueue.owner.execution_id).toBe("exec-open");
  });
});

describe("exactExecutionIdentity", () => {
  it("prefers a pending REPL identity for the open frame", async () => {
    S.currentId = "f1";
    pendingReplIdentity.value = {
      execution_id: "exec-repl",
      owner: { kind: "user_repl", id: "repl-1" },
      frame_id: "f1",
    };

    const identity = await exactExecutionIdentity("f1", "user_repl");

    expect(identity?.execution_id).toBe("exec-repl");
    expect(calls.length).toBe(0);
  });

  it("ignores a pending REPL identity belonging to another frame", async () => {
    S.currentId = "f1";
    pendingReplIdentity.value = {
      execution_id: "exec-repl",
      owner: { kind: "user_repl", id: "repl-1" },
      frame_id: "f-other",
    };

    const identity = await exactExecutionIdentity("f1", "user_repl");

    expect(identity).toBeNull();
  });
});
