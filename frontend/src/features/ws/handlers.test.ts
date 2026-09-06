import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { _artBust, _tbl, artifacts } from "../../stores/artifacts";
import { _liveCell, liveCells } from "../../stores/notebook";
import { currentId, project, sessions } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import {
  _replayGap,
  _seqSeen,
  _streamEpoch,
  pendingExecutionId,
  pendingRequestId,
  stream,
} from "../../stores/stream";
import { handleIncomingMessage } from "./connect";
import { isStaleTurnEvent, mine } from "./guards";
import {
  LOAD_ARTIFACTS_DEBOUNCE_MS,
  LOAD_SESSIONS_DEBOUNCE_MS,
  clearWsDebouncers,
  registerBuiltinHandlers,
  setLoadArtifactsImpl,
  setLoadSessionsImpl,
} from "./handlers";
import { onEvent, resetWsHandlers } from "./registry";

describe("WS protocol handlers", () => {
  beforeEach(() => {
    resetStoreFields();
    resetWsHandlers();
    registerBuiltinHandlers();
    vi.useFakeTimers();
  });

  afterEach(() => {
    clearWsDebouncers();
    setLoadSessionsImpl(null);
    setLoadArtifactsImpl(null);
    vi.clearAllTimers();
    vi.useRealTimers();
    resetWsHandlers();
  });

  it("replay_begin epoch mismatch drops every cursor", () => {
    currentId.value = "other";
    _streamEpoch.value = "epoch-a";
    const previous = _seqSeen.value;
    previous.f = 9;
    previous.g = 3;
    onEvent({ type: "replay_begin", epoch: "epoch-b", root_frame_id: "f", gap: true });
    expect(_streamEpoch.value).toBe("epoch-b");
    expect(_seqSeen.value).not.toBe(previous);
    expect(_seqSeen.value).toEqual({});
    expect(_replayGap.value).toBeNull();
  });

  it("replay_begin matching epoch keeps cursors", () => {
    currentId.value = "f";
    _streamEpoch.value = "epoch-a";
    _seqSeen.value.f = 9;
    onEvent({ type: "replay_begin", epoch: "epoch-a", root_frame_id: "f" });
    expect(_seqSeen.value.f).toBe(9);
  });

  it("replay_begin gap on the open session zeros that cursor and flags reload", () => {
    currentId.value = "f";
    _streamEpoch.value = "epoch-a";
    _seqSeen.value.f = 9;
    onEvent({
      type: "replay_begin",
      epoch: "epoch-a",
      root_frame_id: "f",
      gap: true,
    });
    expect(_seqSeen.value.f).toBe(0);
    expect(_replayGap.value).toBe("f");
  });

  it("replay_begin on the open session tears down the live stream", () => {
    currentId.value = "f";
    const wrap = { remove: vi.fn() };
    stream.value = { wrap };
    liveCells.value = [{ id: "c1" }];
    _liveCell.value = { id: "c1" };
    onEvent({ type: "replay_begin", epoch: "epoch-a", root_frame_id: "f" });
    expect(wrap.remove).toHaveBeenCalledTimes(1);
    expect(stream.value).toBeNull();
    expect(liveCells.value).toEqual([]);
    expect(_liveCell.value).toBeNull();
  });

  it("replay_begin wrap.remove throw does not advance the cursor", () => {
    currentId.value = "f";
    stream.value = {
      wrap: {
        remove: () => {
          throw new Error("dom gone");
        },
      },
    };
    expect(() =>
      handleIncomingMessage(
        JSON.stringify({
          type: "replay_begin",
          root_frame_id: "f",
          seq: 4,
          epoch: "e",
        }),
      ),
    ).toThrow("dom gone");
    expect(_seqSeen.value.f).toBeUndefined();
  });

  it("replay_end gap reload clears the flag and calls openConversation", () => {
    currentId.value = "f";
    project.value = "p1";
    _replayGap.value = "f";
    const opened: unknown[] = [];
    (globalThis as Record<string, unknown>).openConversation = (
      id: unknown,
      proj: unknown,
    ) => {
      opened.push([id, proj]);
    };
    try {
      onEvent({ type: "replay_end", root_frame_id: "f" });
      expect(_replayGap.value).toBeNull();
      expect(opened).toEqual([["f", "p1"]]);
    } finally {
      delete (globalThis as Record<string, unknown>).openConversation;
    }
  });

  it("mine is the open session only", () => {
    currentId.value = "f1";
    expect(mine("f1")).toBe(true);
    expect(mine("f2")).toBe(false);
    expect(mine("")).toBe(false);
    expect(mine(undefined)).toBe(false);
    currentId.value = null;
    expect(mine("f1")).toBe(false);
  });

  it("isStaleTurnEvent filters on execution id, then request id", () => {
    pendingExecutionId.value = "e1";
    expect(isStaleTurnEvent({ execution_id: "e2" })).toBe(true);
    expect(isStaleTurnEvent({ execution_id: "e1" })).toBe(false);
    expect(isStaleTurnEvent({})).toBe(false);
    pendingExecutionId.value = null;
    pendingRequestId.value = "r1";
    expect(isStaleTurnEvent({ request_id: "r2" })).toBe(true);
    expect(isStaleTurnEvent({ request_id: "r1" })).toBe(false);
    pendingRequestId.value = null;
    expect(isStaleTurnEvent({})).toBe(false);
    pendingExecutionId.value = "e1";
    expect(isStaleTurnEvent({ request_id: "r-other" })).toBe(false);
  });

  it("frame_update patches the session row in place and debounce-loads", () => {
    const load = vi.fn();
    setLoadSessionsImpl(load);
    const row: Record<string, unknown> = { id: "a", running: false, name: "old" };
    const list = [row];
    sessions.value = list;

    onEvent({ type: "frame_update", frame_id: "a", status: "processing" });
    expect(sessions.value).toBe(list);
    expect(row.running).toBe(true);
    expect(load).not.toHaveBeenCalled();

    onEvent({
      type: "frame_update",
      frame_id: "a",
      status: "titled",
      task_summary: "New title",
    });
    expect(row.task_summary).toBe("New title");

    vi.advanceTimersByTime(LOAD_SESSIONS_DEBOUNCE_MS - 1);
    expect(load).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(load).toHaveBeenCalledTimes(1);

    onEvent({ type: "frame_update", frame_id: "a", status: "completed" });
    expect(row.running).toBe(false);
    vi.advanceTimersByTime(LOAD_SESSIONS_DEBOUNCE_MS);
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("artifact_created upserts from nested, flat, and bare payloads", () => {
    const load = vi.fn();
    setLoadArtifactsImpl(load);
    currentId.value = "f";
    const row: Record<string, unknown> = { id: "art1", filename: "old.png" };
    const list = [row];
    artifacts.value = list;
    _tbl.value = { "old.png:1": "cached", other: "keep" };

    onEvent({
      type: "artifact_created",
      artifact: { id: "art1", filename: "dir/old.png", version_id: "v2" },
    });
    expect(artifacts.value).toBe(list);
    expect(row.filename).toBe("dir/old.png");
    expect(_artBust.value.art1).toBe("v2");
    expect(_tbl.value["old.png:1"]).toBeUndefined();
    expect(_tbl.value.other).toBe("keep");

    onEvent({
      type: "artifact_created",
      artifact_id: "art2",
      filename: "plan.json",
    });
    expect(list).toHaveLength(2);
    expect((list[1] as { id: string }).id).toBe("art2");

    const n = list.length;
    onEvent({ type: "artifact_created", root_frame_id: "f" });
    expect(list).toHaveLength(n);

    expect(load).not.toHaveBeenCalled();
    vi.advanceTimersByTime(LOAD_ARTIFACTS_DEBOUNCE_MS);
    expect(load).toHaveBeenCalledTimes(1);
    expect(load).toHaveBeenCalledWith("f");
  });
});
