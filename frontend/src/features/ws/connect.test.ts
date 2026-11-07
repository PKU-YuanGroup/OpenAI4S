import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { _seqSeen, _streamEpoch, ws as wsSignal } from "../../stores/stream";
import {
  API,
  WS_PING_MS,
  WS_RECONNECT_MS,
  clearWsPing,
  connectWS,
  setWebSocketImpl,
  wsUrl,
  type WsSocket,
} from "./connect";
import { registerBuiltinHandlers } from "./handlers";
import { registerWsHandler, resetWsHandlers } from "./registry";

class FakeWebSocket implements WsSocket {
  static instances: FakeWebSocket[] = [];
  url: string;
  readyState = 0;
  sent: string[] = [];
  onopen: ((ev?: unknown) => void) | null = null;
  onclose: ((ev?: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  open(): void {
    this.readyState = 1;
    this.onopen?.({});
  }

  drop(): void {
    this.readyState = 3;
    this.onclose?.({});
  }

  emit(data: string): void {
    this.onmessage?.({ data });
  }
}

describe("connectWS", () => {
  beforeEach(() => {
    resetStoreFields();
    resetWsHandlers();
    FakeWebSocket.instances = [];
    setWebSocketImpl(FakeWebSocket);
    vi.stubGlobal("location", { protocol: "http:", host: "example.test:8760" });
    vi.useFakeTimers();
  });

  afterEach(() => {
    clearWsPing();
    setWebSocketImpl(undefined);
    vi.clearAllTimers();
    vi.unstubAllGlobals();
    vi.useRealTimers();
    resetWsHandlers();
  });

  it("builds the same /api/v1/ws URL as app.js:5158", () => {
    expect(wsUrl({ protocol: "http:", host: "h" })).toBe("ws://h" + API + "/ws");
    expect(wsUrl({ protocol: "https:", host: "h" })).toBe("wss://h" + API + "/ws");
  });

  it("subscribes on open with since_seq and epoch", () => {
    currentId.value = "fid";
    _seqSeen.value.fid = 7;
    _streamEpoch.value = "ep";
    connectWS();
    const socket = FakeWebSocket.instances[0];
    expect(socket).toBeDefined();
    expect(socket!.url).toBe("ws://example.test:8760/api/v1/ws");
    expect(wsSignal.value).toBe(socket);
    socket!.open();
    expect(JSON.parse(socket!.sent[0]!)).toEqual({
      type: "view_session",
      root_frame_id: "fid",
      since_seq: 7,
      epoch: "ep",
    });
  });

  it("pings every 25s while open", () => {
    connectWS();
    const socket = FakeWebSocket.instances[0]!;
    socket.open();
    vi.advanceTimersByTime(WS_PING_MS - 1);
    expect(socket.sent).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(socket.sent).toEqual(['{"type":"ping"}']);
  });

  it("reconnects 1500ms after close", () => {
    connectWS();
    FakeWebSocket.instances[0]!.drop();
    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(WS_RECONNECT_MS - 1);
    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("onmessage applies onEvent before advancing _seqSeen", () => {
    const order: string[] = [];
    registerWsHandler("ok", () => {
      order.push(`handler:${String(_seqSeen.value.fid)}`);
    });
    connectWS();
    const socket = FakeWebSocket.instances[0]!;
    socket.open();
    socket.emit(JSON.stringify({ type: "ok", root_frame_id: "fid", seq: 2 }));
    order.push(`cursor:${String(_seqSeen.value.fid)}`);
    expect(order).toEqual(["handler:undefined", "cursor:2"]);
  });

  it("drops duplicate and older data events before applying side effects", () => {
    const applied: string[] = [];
    registerWsHandler("frame_update", (m) => applied.push(String(m.status)));
    connectWS();
    const socket = FakeWebSocket.instances[0]!;
    for (const [seq, status] of [[100, "processing"], [102, "failed"], [102, "failed"], [101, "completed"], [100, "processing"]]) {
      socket.emit(JSON.stringify({ type: "frame_update", root_frame_id: "fid", seq, status }));
    }
    expect(applied).toEqual(["processing", "failed"]);
    expect(_seqSeen.value.fid).toBe(102);
  });

  it("keeps cursors per root and lets a new daemon epoch reset them", () => {
    const applied: string[] = [];
    registerBuiltinHandlers();
    registerWsHandler("ok", (m) => applied.push(`${String(m.root_frame_id)}:${String(m.seq)}`));
    _streamEpoch.value = "old";
    _seqSeen.value.fid = 20;
    connectWS();
    const socket = FakeWebSocket.instances[0]!;
    socket.emit(JSON.stringify({ type: "ok", root_frame_id: "other", seq: 1 }));
    socket.emit(JSON.stringify({ type: "ok", root_frame_id: "fid", seq: 20 }));
    // What a restarted daemon sends for a cursor from the old epoch: control
    // frames without `seq`, then its own stream numbered from 1 again.
    socket.emit(JSON.stringify({ type: "replay_begin", root_frame_id: "fid", from_seq: 20, to_seq: 20, epoch: "new", gap: true }));
    socket.emit(JSON.stringify({ type: "replay_end", root_frame_id: "fid", to_seq: 20 }));
    socket.emit(JSON.stringify({ type: "ok", root_frame_id: "fid", seq: 2 }));
    expect(applied).toEqual(["other:1", "fid:2"]);
    expect(_streamEpoch.value).toBe("new");
    expect(_seqSeen.value.fid).toBe(2);
  });

  it("applies a pending approval re-sent with the seq of its first broadcast", () => {
    const applied: string[] = [];
    registerWsHandler("await_permission", (m) => applied.push(String(m.decision_id)));
    registerWsHandler("text_chunk", () => {});
    connectWS();
    const socket = FakeWebSocket.instances[0]!;
    // The broker keeps the payload the hub stamped, and `view_session` re-sends it.
    const card = { type: "await_permission", frame_id: "fid", root_frame_id: "fid", decision_id: "perm-1", seq: 2 };
    socket.emit(JSON.stringify(card));
    socket.emit(JSON.stringify({ type: "text_chunk", root_frame_id: "fid", seq: 3 }));
    socket.emit(JSON.stringify(card));
    expect(applied).toEqual(["perm-1", "perm-1"]);
    expect(_seqSeen.value.fid).toBe(3);
  });

  it("never records a non-finite seq that would drop every later event", () => {
    const applied: string[] = [];
    registerWsHandler("ok", (m) => applied.push(String(m.seq)));
    connectWS();
    const socket = FakeWebSocket.instances[0]!;
    socket.emit('{"type":"ok","root_frame_id":"fid","seq":1e999}');
    socket.emit(JSON.stringify({ type: "ok", root_frame_id: "fid", seq: 5 }));
    expect(applied).toEqual(["Infinity", "5"]);
    expect(_seqSeen.value.fid).toBe(5);
  });
});
