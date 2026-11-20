import { effect } from "@preact/signals";
import { afterEach, describe, expect, it, vi } from "vitest";
import { currentId } from "../../stores/session";
import { workbenchErrors } from "../../stores/timeline";
import { historyT } from "../messages/copy";
import { setExecutionFetch } from "./api";
import {
  applyForkPresentation,
  branchDisplayName,
  checkpointLabel,
  forkFromMessage,
  kernelGapText,
  kernelOutcome,
  missingNames,
  recoveryActionWords,
  restoreBranchKernel,
  setMessageForkRefresh,
  switchedText,
} from "./branch";
import { branchT } from "./copy";
import {
  FORK_NO_CHECKPOINT_MESSAGE,
  forkErrorDisplay,
  forkOnce,
  httpStatusOf,
  isForkNoCheckpoint,
  presentForkError,
  shouldRetryFork,
} from "./conflict";

function conflict409(message = FORK_NO_CHECKPOINT_MESSAGE): {
  status: number;
  code: string;
  message: string;
  error: string;
} {
  return { status: 409, code: "conflict", message, error: message };
}

describe("fork 409 presentation (CursorCheckpointUnavailable)", () => {
  it("presents the server sentence for fork-without-checkpoint and never retries", () => {
    const presented = presentForkError(conflict409());
    expect(presented.kind).toBe("conflict");
    expect(presented.noCheckpoint).toBe(true);
    expect(presented.httpStatus).toBe(409);
    expect(presented.code).toBe("conflict");
    expect(presented.retry).toBe(false);
    expect(presented.masked).toBe(false);
    expect(shouldRetryFork(presented)).toBe(false);
    expect(forkErrorDisplay(presented)).toBe(FORK_NO_CHECKPOINT_MESSAGE);
    expect(forkErrorDisplay(presented)).not.toMatch(/try again/i);
    expect(forkErrorDisplay(presented)).not.toBe("");
  });

  it("recognises the 409 from notebookFetch, which only keeps the message", () => {
    const presented = presentForkError(new Error(FORK_NO_CHECKPOINT_MESSAGE));
    expect(presented.kind).toBe("conflict");
    expect(presented.noCheckpoint).toBe(true);
    expect(presented.retry).toBe(false);
    expect(forkErrorDisplay(presented)).toContain("no exact cursor checkpoint");
    expect(isForkNoCheckpoint(new Error(FORK_NO_CHECKPOINT_MESSAGE))).toBe(true);
  });

  it("does not treat a recovery domain status string as HTTP 409", () => {
    expect(httpStatusOf({ status: "failed", message: "partial restore" })).toBeNull();
    const presented = presentForkError({ status: "failed", message: "partial restore" });
    expect(presented.kind).toBe("error");
    expect(presented.noCheckpoint).toBe(false);
    expect(presented.retry).toBe(false);
  });

  it("other HTTP 409s are still conflicts: no retry, message not rewritten", () => {
    const presented = presentForkError({
      status: 409,
      code: "conflict",
      message: "session deletion is already in progress",
      error: "session deletion is already in progress",
    });
    expect(presented.kind).toBe("conflict");
    expect(presented.noCheckpoint).toBe(false);
    expect(presented.retry).toBe(false);
    expect(forkErrorDisplay(presented)).toBe("session deletion is already in progress");
  });

  it("forkOnce invokes the POST exactly once on 409", async () => {
    const post = vi.fn(async () => {
      throw conflict409();
    });
    const attempt = await forkOnce(post);
    expect(post).toHaveBeenCalledTimes(1);
    expect(attempt.ok).toBe(false);
    if (attempt.ok) throw new Error("expected failure");
    expect(attempt.attempts).toBe(1);
    expect(attempt.presentation.noCheckpoint).toBe(true);
    expect(attempt.presentation.retry).toBe(false);
    expect(shouldRetryFork(attempt.presentation)).toBe(false);
  });

  it("forkOnce does not retry a non-409 either", async () => {
    const post = vi.fn(async () => {
      throw { status: 404, message: "session not found", error: "session not found" };
    });
    const attempt = await forkOnce(post);
    expect(post).toHaveBeenCalledTimes(1);
    expect(attempt.ok).toBe(false);
    if (attempt.ok) throw new Error("expected failure");
    expect(attempt.presentation.kind).toBe("error");
    expect(attempt.presentation.retry).toBe(false);
  });

  it("does not map a 409 into a successful fork", async () => {
    const attempt = await forkOnce(async () => {
      throw conflict409();
    });
    expect(attempt.ok).toBe(false);
    expect("result" in attempt).toBe(false);
  });

  it("keeps a successful POST as ok without inventing a conflict", async () => {
    const attempt = await forkOnce(async () => ({ branch_id: "b1" }));
    expect(attempt).toEqual({ ok: true, result: { branch_id: "b1" }, attempts: 1 });
  });
});

describe("forkFromMessage", () => {
  const hints: string[] = [];

  afterEach(() => {
    hints.length = 0;
    vi.unstubAllGlobals();
    setExecutionFetch(null);
    setMessageForkRefresh(null);
    currentId.value = null;
    workbenchErrors.value = Object.create(null);
  });

  function jsonResponse(body: unknown, status: number): Response {
    return new Response(JSON.stringify(body), { status });
  }

  it("posts exactly {from_message_id} once, then refreshes and names the branch", async () => {
    currentId.value = "frame/1";
    const refresh = vi.fn();
    setMessageForkRefresh(refresh);
    const posts: Array<{ url: string; body: unknown }> = [];
    vi.stubGlobal("hint", (message: string) => {
      hints.push(message);
    });
    setExecutionFetch(async (url, init) => {
      posts.push({ url: String(url), body: JSON.parse(String(init?.body || "{}")) });
      return jsonResponse({ branch_id: "br-1", name: "from question" }, 200);
    });
    const result = await forkFromMessage("frame/1", "msg-9");
    expect(posts).toEqual([{
      url: "/api/v1/frames/frame%2F1/branches/fork",
      body: { from_message_id: "msg-9" },
    }]);
    expect(result).toEqual({ ok: true, branch_id: "br-1", name: "from question" });
    expect(hints).toEqual([historyT("history.forkMessage.created", "from question")]);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("presents a 409 once and does not fork again or fall back to another source", async () => {
    currentId.value = "frame-1";
    const refresh = vi.fn();
    setMessageForkRefresh(refresh);
    const posts: unknown[] = [];
    setExecutionFetch(async (_url, init) => {
      posts.push(JSON.parse(String(init?.body || "{}")));
      return jsonResponse({ error: FORK_NO_CHECKPOINT_MESSAGE, code: "conflict" }, 409);
    });
    const result = await forkFromMessage("frame-1", "msg-9");
    expect(posts).toEqual([{ from_message_id: "msg-9" }]);
    expect(result).toMatchObject({
      ok: false,
      presentation: {
        kind: "conflict",
        noCheckpoint: true,
        httpStatus: 409,
        retry: false,
        message: FORK_NO_CHECKPOINT_MESSAGE,
      },
    });
    expect(workbenchErrors.value.branchAction).toBe(FORK_NO_CHECKPOINT_MESSAGE);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("uses the history fallback when the failure has no sentence", async () => {
    currentId.value = "frame-1";
    setExecutionFetch(async () => {
      throw new Error("");
    });
    const result = await forkFromMessage("frame-1", "msg-9");
    expect(result).toMatchObject({
      ok: false,
      presentation: { message: historyT("history.forkMessage.failed"), retry: false },
    });
    expect(workbenchErrors.value.branchAction).toBe(historyT("history.forkMessage.failed"));
  });

  it("a second call while the first is in flight does not post", async () => {
    currentId.value = "frame-1";
    const refresh = vi.fn();
    setMessageForkRefresh(refresh);
    const posts: unknown[] = [];
    let release: (response: Response) => void = () => undefined;
    setExecutionFetch((_url, init) => {
      posts.push(JSON.parse(String(init?.body || "{}")));
      return new Promise<Response>((resolve) => {
        release = resolve;
      });
    });
    const first = forkFromMessage("frame-1", "msg-9");
    const second = forkFromMessage("frame-1", "msg-9");
    expect(posts).toEqual([{ from_message_id: "msg-9" }]);
    expect(await second).toBeNull();
    release(jsonResponse({ branch_id: "br-2" }, 200));
    expect(await first).toMatchObject({ ok: true, branch_id: "br-2", name: "br-2" });
    expect(posts).toHaveLength(1);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("returns the created branch but does not hint or refresh after the user leaves", async () => {
    currentId.value = "frame-1";
    const refresh = vi.fn();
    setMessageForkRefresh(refresh);
    const held = { recoveryAction: "keep" };
    workbenchErrors.value = held;
    vi.stubGlobal("hint", (message: string) => {
      hints.push(message);
    });
    let release: (response: Response) => void = () => undefined;
    setExecutionFetch(() => new Promise<Response>((resolve) => {
      release = resolve;
    }));
    const pending = forkFromMessage("frame-1", "msg-9");
    currentId.value = "frame-2";
    release(jsonResponse({ branch_id: "br-1", name: "from question" }, 200));
    await expect(pending).resolves.toEqual({ ok: true, branch_id: "br-1", name: "from question" });
    expect(hints).toEqual([]);
    expect(refresh).not.toHaveBeenCalled();
    expect(workbenchErrors.value).toBe(held);
    setExecutionFetch(async () => jsonResponse({ branch_id: "br-3", name: "again" }, 200));
    currentId.value = "frame-1";
    await expect(forkFromMessage("frame-1", "msg-9")).resolves.toMatchObject({
      ok: true,
      branch_id: "br-3",
    });
  });

  it("returns a 409 but does not present it after the user leaves", async () => {
    currentId.value = "frame-1";
    const refresh = vi.fn();
    setMessageForkRefresh(refresh);
    const held = { recoveryAction: "keep" };
    workbenchErrors.value = held;
    vi.stubGlobal("hint", (message: string) => {
      hints.push(message);
    });
    let release: (response: Response) => void = () => undefined;
    setExecutionFetch(() => new Promise<Response>((resolve) => {
      release = resolve;
    }));
    const pending = forkFromMessage("frame-1", "msg-9");
    currentId.value = "other";
    release(jsonResponse({ error: FORK_NO_CHECKPOINT_MESSAGE, code: "conflict" }, 409));
    await expect(pending).resolves.toMatchObject({
      ok: false,
      presentation: { noCheckpoint: true, message: FORK_NO_CHECKPOINT_MESSAGE },
    });
    expect(hints).toEqual([]);
    expect(refresh).not.toHaveBeenCalled();
    expect(workbenchErrors.value).toBe(held);
    currentId.value = "frame-1";
    setExecutionFetch(async () => jsonResponse({ branch_id: "br-4" }, 200));
    await expect(forkFromMessage("frame-1", "msg-9")).resolves.toMatchObject({ ok: true });
  });

  it("still returns the created branch when the injected refresh throws", async () => {
    currentId.value = "frame-1";
    let calls = 0;
    setMessageForkRefresh(() => {
      calls += 1;
      throw new Error("refresh down");
    });
    vi.stubGlobal("hint", (message: string) => {
      hints.push(message);
    });
    setExecutionFetch(async () => jsonResponse({ branch_id: "br-5", name: "kept" }, 200));
    await expect(forkFromMessage("frame-1", "msg-9")).resolves.toEqual({
      ok: true,
      branch_id: "br-5",
      name: "kept",
    });
    expect(calls).toBe(1);
    expect(hints).toEqual([historyT("history.forkMessage.created", "kept")]);
  });
});

describe("applyForkPresentation", () => {
  it("publishes a new workbenchErrors object, so a subscriber sees the banner", () => {
    workbenchErrors.value = { recoveryAction: "earlier" };
    const seen: Array<Record<string, unknown>> = [];
    const stop = effect(() => {
      seen.push(workbenchErrors.value);
    });
    applyForkPresentation(presentForkError(conflict409()));
    stop();
    expect(seen).toHaveLength(2);
    expect(seen[1]).toEqual({ recoveryAction: "earlier", branchAction: FORK_NO_CHECKPOINT_MESSAGE });
  });
});

// M-73: what a branch switch or a Restore left the kernel with, read from the
// server's report rather than shown as "error: … inspect Recovery".
describe("kernel outcome after a branch switch", () => {
  afterEach(() => {
    setExecutionFetch(null);
  });

  it("names the variables an activation did not bring back", () => {
    const outcome = kernelOutcome({
      status: "partial",
      dimensions: { namespace: { issues: [{ type: "non_replayable" }, { type: "missing_symbols", names: ["df", "fit"] }] } },
      recovery: { results: [{ issues: [{ type: "missing_symbols", names: ["fit", "cox"] }] }] },
    });
    expect(outcome).toEqual({ status: "partial", missing: ["df", "fit", "cox"], message: "" });
    expect(switchedText("Q2", outcome)).toBe(branchT("branch.switchedGap", "Q2", missingNames(outcome)));
    expect(switchedText("Q2", kernelOutcome({ status: "active" }))).toBe(branchT("branch.switched", "Q2"));
    expect(branchDisplayName({ branch_id: "br-0123456789abcdef" })).toBe("br-01234…");
    expect(branchDisplayName({ branch_id: "br-1", name: "Q2" })).toBe("Q2");
  });

  it("reads a partial Restore answered with 409 as an outcome, and a refusal as a failure", async () => {
    const posts: Array<{ url: string; body: unknown }> = [];
    let reply: Response = new Response("{}");
    setExecutionFetch(async (url, init) => {
      posts.push({ url: String(url), body: JSON.parse(String(init?.body || "{}")) });
      return reply;
    });
    reply = new Response(
      JSON.stringify({ ok: false, status: "partial", results: [{ issues: [{ type: "missing_symbols", names: ["x1"] }] }] }),
      { status: 409 },
    );
    const partial = await restoreBranchKernel("frame-1", "br-1");
    expect(posts).toEqual([
      { url: "/api/v1/frames/frame-1/recovery/actions/restore", body: { branch_id: "br-1", confirm: false } },
    ]);
    expect(partial).toEqual({ status: "partial", missing: ["x1"], message: "" });
    expect(kernelGapText(partial)).toBe(branchT("branch.kernelGap", "x1"));

    reply = new Response(JSON.stringify({ error: "recovery already running" }), { status: 409 });
    const refused = await restoreBranchKernel("frame-1", "br-1");
    expect(refused).toEqual({ status: "failed", missing: [], message: "recovery already running" });
    expect(kernelGapText(refused)).toBe(branchT("branch.restoreFailed", "recovery already running"));
  });
});

// M-64: internal recovery reasons and checkpoint ids, said in words.
describe("recovery and checkpoint words", () => {
  it("groups the server's reasons by what the reader can do", () => {
    const why = (reason: string) => recoveryActionWords({ id: "restore", enabled: false, reason });
    expect(why("checkpoint has an invalid python bootstrap manifest")).toBe(branchT("recovery.why.checkpoint"));
    expect(why("checkpoint workspace tree is missing or corrupt")).toBe(branchT("recovery.why.checkpoint"));
    expect(why("no checkpoint exists")).toBe(branchT("recovery.why.noCheckpoint"));
    expect(why("kernel is already active")).toBe(branchT("recovery.why.active"));
    expect(why("recovery already running")).toBe(branchT("recovery.why.busy"));
    expect(why("latest recovery is not partial or failed")).toBe(branchT("recovery.why.notRetryable"));
    expect(why("workspace revert must be restored or reconciled first")).toBe(branchT("recovery.why.revertFirst"));
    expect(why("something new")).toBe(branchT("recovery.why.other"));
    expect(recoveryActionWords({ id: "restart_fresh", enabled: true })).toBe(branchT("recovery.what.restart_fresh"));
  });

  it("labels a checkpoint by when and why it was taken", () => {
    const at = new Date(2026, 10, 20, 10, 32, 5).getTime();
    expect(checkpointLabel({ checkpoint_id: "cp-1", reason: "cursor_message", created_at: at })).toMatch(
      /10:32:05/,
    );
    expect(checkpointLabel({ checkpoint_id: "cp-1", reason: "cursor_message", created_at: at })).not.toContain("cp-1");
    expect(checkpointLabel({ reason: "cursor_cell", created_at: at })).toBe(
      branchT("branch.cp.cell", new Date(at).toLocaleTimeString("en-US", { hour12: false })),
    );
    expect(checkpointLabel({ reason: "before cleanup" })).toBe("before cleanup");
  });
});
