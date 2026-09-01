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
});
