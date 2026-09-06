import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { pendingExecutionId, stream } from "../../stores/stream";
import { registerBuiltinHandlers } from "../ws/handlers";
import { onEvent, resetWsHandlers } from "../ws/registry";
import { registerMessageHandlers } from "./handlers";

describe("text_reset / text_chunk guards", () => {
  beforeEach(() => {
    resetStoreFields();
    resetWsHandlers();
    registerBuiltinHandlers();
    registerMessageHandlers();
  });

  afterEach(() => {
    resetWsHandlers();
  });

  it("ignores text_chunk when the event is not the open session", () => {
    currentId.value = "other";
    onEvent({ type: "text_chunk", root_frame_id: "f", chunk: "hi" });
    expect(stream.value).toBeNull();
  });

  it("ignores a stale-turn text_chunk", () => {
    currentId.value = "f";
    pendingExecutionId.value = "e1";
    onEvent({
      type: "text_chunk",
      root_frame_id: "f",
      execution_id: "e2",
      chunk: "nope",
    });
    expect(stream.value).toBeNull();
  });

  it("text_reset on another session does not start a stream", () => {
    currentId.value = "other";
    onEvent({ type: "text_reset", root_frame_id: "f" });
    expect(stream.value).toBeNull();
  });

  it("does not throw when registering twice (install is idempotent)", () => {
    expect(() => registerMessageHandlers()).not.toThrow();
  });
});
