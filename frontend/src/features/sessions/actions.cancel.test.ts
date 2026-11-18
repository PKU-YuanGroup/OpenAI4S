/**
 * An accepted cancel names the execution it stopped; "Stopping…" is applied
 * only when that is still the execution this client is running.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const scoped = vi.hoisted(() => ({
  result: { ok: true, scope: "running", execution_id: "A" } as Record<string, unknown>,
}));
vi.mock("../timeline/execution-request", () => ({
  scopedExecutionRequest: vi.fn(async () => scoped.result),
}));
vi.mock("../send/ticket", () => ({ resumeWatch: vi.fn() }));

import { currentId } from "../../stores/session";
import { pendingExecutionId, running } from "../../stores/stream";
import { resetStoreFields } from "../../stores/signal-field";
import { backgroundCopy } from "../send/background";
import { queueT } from "../timeline/copy";
import { S } from "../timeline/s";
import { cancelNamedTheRunningTurn, cancelTurn } from "./actions";
import * as chrome from "./chrome";

describe("cancelNamedTheRunningTurn", () => {
  beforeEach(() => resetStoreFields());

  it("refuses a rejected or missing ack", () => {
    expect(cancelNamedTheRunningTurn(null)).toBe(false);
    expect(cancelNamedTheRunningTurn({ ok: false, execution_id: "A" })).toBe(false);
  });

  it("accepts an ack for the execution this client is running", () => {
    pendingExecutionId.value = "A";
    expect(cancelNamedTheRunningTurn({ ok: true, execution_id: "A" })).toBe(true);
  });

  it("ignores an ack for an execution that has already been replaced", () => {
    // cancelled(A) then processing(B) arrived over the socket before the HTTP
    // response: B was never cancelled and must keep its Stop button.
    pendingExecutionId.value = "B";
    expect(cancelNamedTheRunningTurn({ ok: true, execution_id: "A" })).toBe(false);
  });

  it("trusts an ack that names nothing, and one when nothing is pending", () => {
    pendingExecutionId.value = "A";
    expect(cancelNamedTheRunningTurn({ ok: true })).toBe(true);
    pendingExecutionId.value = null;
    expect(cancelNamedTheRunningTurn({ ok: true, execution_id: "A" })).toBe(true);
  });
});

describe("Stop with follow-ups queued", () => {
  const hints: string[] = [];
  beforeEach(() => {
    resetStoreFields();
    hints.length = 0;
    vi.spyOn(chrome, "hint").mockImplementation((text) => {
      hints.push(String(text || ""));
    });
    scoped.result = { ok: true, scope: "running", execution_id: "A" };
  });
  afterEach(() => {
    vi.restoreAllMocks();
    S.executionQueue = null;
  });

  function queuedTicket(id: string) {
    return { execution_id: id, status: "queued", owner: { kind: "agent", id: "job-" + id }, metadata: { preview: id } };
  }

  it("says the queued messages run next, so the Stop does not look ignored", async () => {
    currentId.value = "f-1";
    running.value = true;
    pendingExecutionId.value = "A";
    S.currentId = "f-1";
    S.executionQueue = { owner: null, queue: [queuedTicket("B"), queuedTicket("C")] };
    await cancelTurn();
    expect(hints.at(-1)).toBe(queueT("queueRow.stopping", 2));
  });

  it("says when the Stop also stopped the turn's background cells", async () => {
    currentId.value = "f-1";
    running.value = true;
    pendingExecutionId.value = "A";
    S.currentId = "f-1";
    S.executionQueue = { owner: null, queue: [queuedTicket("B")] };
    scoped.result = { ok: true, scope: "running", execution_id: "A", background_stopped: ["exec-1", "exec-2"] };
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, text: async () => "{\"cells\":[]}" })));
    await cancelTurn();
    const last = hints.at(-1) || "";
    expect(last).toContain(backgroundCopy("stoppedWithTurn", 2));
    expect(last).toContain(queueT("queueRow.stopping", 1));
    vi.unstubAllGlobals();
  });

  it("says only that it is stopping when nothing is queued", async () => {
    currentId.value = "f-1";
    running.value = true;
    pendingExecutionId.value = "A";
    S.currentId = "f-1";
    S.executionQueue = { owner: null, queue: [] };
    await cancelTurn();
    expect(hints.at(-1)).not.toBe(queueT("queueRow.stopping", 0));
    expect(hints.some((text) => text.includes("2"))).toBe(false);
  });
});
