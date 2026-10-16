/**
 * The seven canonical events reach the status block through the registry's
 * one-handler-per-type rule: this lane owns five, and the send lane's
 * `candidate_ready` / `auto_run_terminal` handlers keep their own work and
 * pass the event on. Reconnect and reopen are the other two GET triggers.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../sessions/chrome", () => ({
  hint: vi.fn(),
  openMenu: vi.fn(),
  closeMenu: vi.fn(),
  repositionMenu: vi.fn(),
  reportFailure: vi.fn(),
  ensureActivateKeys: vi.fn(),
}));
vi.mock("../sessions/dom", async () => {
  const { fakeEl } = await import("./testing");
  return { $: () => null, el: fakeEl, enableComposer: vi.fn() };
});
vi.mock("../send/candidate", () => ({
  markCandidateReady: vi.fn(),
  applyCandidateResolution: vi.fn(),
  applyFinalReviewStatus: vi.fn(),
}));
vi.mock("../timeline/island", () => ({ scheduleWorkbenchRefresh: vi.fn() }));

import { _openGen, currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { ws as wsSignal } from "../../stores/stream";
import { registerSendHandlers, resetSendHandlers } from "../send/handlers";
import { markCandidateReady } from "../send/candidate";
import { scheduleWorkbenchRefresh } from "../timeline/island";
import { hasWsHandler, onEvent, registerWsHandler, resetWsHandlers } from "../ws/registry";
import { resetAutoModeAudits } from "./audits";
import { AUTO_MODE_HINT_TYPES, installAutoModeHints, registerAutoModeHandlers, resetAutoModeHints } from "./hints";
import { autoModeMenuItem, resetAutoModeMenu } from "./menu";
import { resetAutoModeStatus } from "./status";
import { autoModeBody, json, routeFetch, settle, type FetchCall } from "./testing";
import { CANONICAL_AUTO_EVENTS } from "./types";

let calls: FetchCall[];

function reads(): number {
  return calls.filter((call) => call.path === "/api/v1/frames/f-root/auto-mode").length;
}

class FakeSocket {
  private listeners: Array<() => void> = [];
  addEventListener(type: string, fn: () => void): void {
    if (type === "open") this.listeners.push(fn);
  }
  open(): void {
    this.listeners.forEach((fn) => fn());
  }
}

beforeEach(() => {
  resetStoreFields();
  resetWsHandlers();
  resetSendHandlers();
  resetAutoModeHints();
  resetAutoModeStatus();
  resetAutoModeMenu();
  resetAutoModeAudits();
  vi.mocked(markCandidateReady).mockReset();
  vi.mocked(scheduleWorkbenchRefresh).mockReset();
  calls = routeFetch(() => json(autoModeBody()));
  currentId.value = "f-root";
  vi.useFakeTimers();
});

afterEach(() => {
  resetAutoModeHints();
  resetWsHandlers();
  resetSendHandlers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("registration", () => {
  it("owns exactly the five canonical types no other lane registers", () => {
    registerAutoModeHandlers();
    expect([...AUTO_MODE_HINT_TYPES].sort()).toEqual(
      [...CANONICAL_AUTO_EVENTS].filter((type) => type !== "candidate_ready" && type !== "auto_run_terminal").sort(),
    );
    for (const type of AUTO_MODE_HINT_TYPES) expect(hasWsHandler(type)).toBe(true);
    expect(hasWsHandler("candidate_ready")).toBe(false);
    expect(hasWsHandler("auto_run_terminal")).toBe(false);
  });

  it("composes with the send lane so all seven types have one handler each", () => {
    registerSendHandlers();
    registerAutoModeHandlers();
    for (const type of CANONICAL_AUTO_EVENTS) expect(hasWsHandler(type)).toBe(true);
    expect(() => registerWsHandler("candidate_ready", () => {})).toThrow(/duplicate/);
  });

  it("is idempotent, and a type someone else took is an error, not a silent skip", () => {
    registerAutoModeHandlers();
    expect(() => registerAutoModeHandlers()).not.toThrow();
    resetWsHandlers();
    resetAutoModeHints();
    registerWsHandler("repair_started", () => {});
    expect(() => registerAutoModeHandlers()).toThrow(/duplicate/);
  });
});

describe("events while the block is open", () => {
  beforeEach(() => {
    registerSendHandlers();
    installAutoModeHints();
    autoModeMenuItem();
  });

  it.each([...CANONICAL_AUTO_EVENTS])("%s for this conversation becomes a GET", async (type) => {
    const before = reads();
    onEvent({ type, root_frame_id: "f-root", subject_kind: "result_review" });
    await vi.advanceTimersByTimeAsync(60);
    await settle();
    expect(reads()).toBe(before + 1);
  });

  it("keeps the send lane's candidate card and terminal refresh", async () => {
    onEvent({ type: "candidate_ready", root_frame_id: "f-root", gates_completion: true });
    expect(markCandidateReady).toHaveBeenCalledTimes(1);
    onEvent({ type: "candidate_ready", root_frame_id: "f-root" });
    expect(markCandidateReady).toHaveBeenCalledTimes(1);
    onEvent({ type: "auto_run_terminal", root_frame_id: "f-root" });
    expect(scheduleWorkbenchRefresh).toHaveBeenCalledWith(60);
    await vi.advanceTimersByTimeAsync(60);
    await settle();
    expect(reads()).toBe(1);
  });

  it("still hints when the card work throws", async () => {
    vi.mocked(markCandidateReady).mockImplementation(() => {
      throw new Error("card failed");
    });
    expect(() => onEvent({ type: "candidate_ready", root_frame_id: "f-root", gates_completion: true })).toThrow("card failed");
    await vi.advanceTimersByTimeAsync(60);
    await settle();
    expect(reads()).toBe(1);
  });
});

describe("reconnect and reopen", () => {
  it("re-reads when a later socket opens, not on the first connection", async () => {
    installAutoModeHints();
    autoModeMenuItem();
    const first = new FakeSocket();
    wsSignal.value = first;
    first.open();
    await vi.advanceTimersByTimeAsync(60);
    expect(reads()).toBe(0);
    const second = new FakeSocket();
    wsSignal.value = second;
    second.open();
    await vi.advanceTimersByTimeAsync(60);
    await settle();
    expect(reads()).toBe(1);
  });

  it("re-reads when the open conversation is reopened while the block shows", async () => {
    installAutoModeHints();
    autoModeMenuItem();
    _openGen.value += 1;
    await settle();
    expect(reads()).toBe(1);
  });

  it("does not read for a reopen nobody is looking at", async () => {
    installAutoModeHints();
    _openGen.value += 1;
    await settle();
    expect(reads()).toBe(0);
  });
});
