/**
 * An accepted cancel names the execution it stopped; "Stopping…" is applied
 * only when that is still the execution this client is running.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { pendingExecutionId } from "../../stores/stream";
import { resetStoreFields } from "../../stores/signal-field";
import { cancelNamedTheRunningTurn } from "./actions";

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
