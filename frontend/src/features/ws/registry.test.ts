import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { resetStoreFields } from "../../stores/signal-field";
import { _seqSeen } from "../../stores/stream";
import { handleIncomingMessage } from "./connect";
import { installWs } from "./index";
import {
  hasWsHandler,
  onEvent,
  registerWsHandler,
  resetWsHandlers,
} from "./registry";

describe("WS handler registry", () => {
  beforeEach(() => {
    resetStoreFields();
    resetWsHandlers();
  });

  afterEach(() => {
    resetWsHandlers();
  });

  it("throws on duplicate register of the same type", () => {
    registerWsHandler("text_chunk", () => {});
    expect(() => registerWsHandler("text_chunk", () => {})).toThrow(
      /duplicate WS handler for type: text_chunk/,
    );
    expect(hasWsHandler("text_chunk")).toBe(true);
  });

  it("allows the same function on two type names", () => {
    const shared = (): void => {};
    registerWsHandler("action_timeline", shared);
    registerWsHandler("action-timeline", shared);
    expect(hasWsHandler("action_timeline")).toBe(true);
    expect(hasWsHandler("action-timeline")).toBe(true);
  });

  it("does not advance the cursor when the handler throws", () => {
    registerWsHandler("boom", () => {
      throw new Error("handler exploded");
    });
    const seen = _seqSeen.value;
    expect(() =>
      handleIncomingMessage(
        JSON.stringify({ type: "boom", root_frame_id: "fid", seq: 4 }),
      ),
    ).toThrow("handler exploded");
    expect(_seqSeen.value).toBe(seen);
    expect(seen.fid).toBeUndefined();
  });

  it("advances the cursor only after onEvent returns", () => {
    const order: string[] = [];
    registerWsHandler("ok", (m) => {
      order.push(`handler:${String(m.seq)}`);
      expect(_seqSeen.value.fid).toBeUndefined();
    });
    handleIncomingMessage(
      JSON.stringify({ type: "ok", root_frame_id: "fid", seq: 3 }),
    );
    order.push(`cursor:${String(_seqSeen.value.fid)}`);
    expect(order).toEqual(["handler:3", "cursor:3"]);
    expect(_seqSeen.value.fid).toBe(3);
  });

  it("does not record a seq that is not greater than the cursor", () => {
    registerWsHandler("ok", () => {});
    _seqSeen.value.fid = 5;
    handleIncomingMessage(
      JSON.stringify({ type: "ok", root_frame_id: "fid", seq: 5 }),
    );
    expect(_seqSeen.value.fid).toBe(5);
    handleIncomingMessage(
      JSON.stringify({ type: "ok", root_frame_id: "fid", seq: 4 }),
    );
    expect(_seqSeen.value.fid).toBe(5);
    handleIncomingMessage(
      JSON.stringify({ type: "ok", root_frame_id: "fid", seq: 6 }),
    );
    expect(_seqSeen.value.fid).toBe(6);
  });

  it("keys the cursor on root_frame_id, not frame_id", () => {
    registerWsHandler("ok", () => {});
    handleIncomingMessage(
      JSON.stringify({ type: "ok", frame_id: "fid", seq: 8 }),
    );
    expect(_seqSeen.value.fid).toBeUndefined();
  });

  it("swallows a JSON parse failure and leaves the cursor", () => {
    _seqSeen.value.fid = 1;
    handleIncomingMessage("not-json{");
    expect(_seqSeen.value.fid).toBe(1);
  });

  it("advances the cursor for an unhandled type (onEvent is a no-op)", () => {
    handleIncomingMessage(
      JSON.stringify({ type: "pong", root_frame_id: "fid", seq: 2 }),
    );
    expect(_seqSeen.value.fid).toBe(2);
  });

  it("does not advance when onEvent itself is called (E2E path)", () => {
    registerWsHandler("ok", () => {});
    onEvent({ type: "ok", root_frame_id: "fid", seq: 9 });
    expect(_seqSeen.value.fid).toBeUndefined();
  });

  it("installWs writes the real onEvent onto the target", () => {
    const target: Record<string, unknown> = {};
    installWs(target);
    expect(target.onEvent).toBe(onEvent);
    expect(hasWsHandler("replay_begin")).toBe(true);
    expect(() => registerWsHandler("replay_begin", () => {})).toThrow(/duplicate/);
  });
});
