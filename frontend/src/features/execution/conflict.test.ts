import { describe, expect, it, vi } from "vitest";
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
