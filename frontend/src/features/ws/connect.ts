import { currentId } from "../../stores/session";
import { _seqSeen, _streamEpoch, ws as wsSignal } from "../../stores/stream";
import { onEvent } from "./registry";
import type { WsMessage } from "./types";

export const API = "/api/v1";
export const WS_PING_MS = 25000;
export const WS_RECONNECT_MS = 1500;

export type WsSocket = {
  readyState: number;
  send: (data: string) => void;
  onopen: ((ev?: unknown) => void) | null;
  onclose: ((ev?: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
};

export type WsConstructor = new (url: string) => WsSocket;

type ConnectWS = (() => void) & { _p?: ReturnType<typeof setInterval> };

let wsCtor: WsConstructor | undefined;

export function setWebSocketImpl(ctor: WsConstructor | undefined): void {
  wsCtor = ctor;
}

function pageLocation(): { protocol: string; host: string } {
  if (typeof location !== "undefined") return location;
  return { protocol: "http:", host: "127.0.0.1:8760" };
}

export function wsUrl(loc: { protocol: string; host: string } = pageLocation()): string {
  return (loc.protocol === "https:" ? "wss:" : "ws:") + "//" + loc.host + API + "/ws";
}

function resolveWsCtor(): WsConstructor {
  if (wsCtor) return wsCtor;
  if (typeof WebSocket === "function") {
    return WebSocket as unknown as WsConstructor;
  }
  throw new Error("WebSocket is not available");
}

function conn(on: boolean): void {
  try {
    const d = document.querySelector("#conn-dot");
    if (d) d.className = "dot " + (on ? "on" : "off");
  } catch {
    /* unit tests have no document */
  }
}

/** app.js:5181 — subscribe with since_seq + epoch so the server can resume. */
export function sub(f: string): void {
  try {
    const socket = wsSignal.value as WsSocket | null;
    if (socket && socket.readyState === 1) {
      socket.send(
        JSON.stringify({
          type: "view_session",
          root_frame_id: f,
          since_seq: _seqSeen.value[f] || 0,
          epoch: _streamEpoch.value || undefined,
        }),
      );
    }
  } catch {
    /* closed / mid-handshake */
  }
}

/** app.js:5182 */
export function unsub(f: string): void {
  try {
    const socket = wsSignal.value as WsSocket | null;
    if (socket && socket.readyState === 1 && f) {
      socket.send(JSON.stringify({ type: "unview_session", root_frame_id: f }));
    }
  } catch {
    /* closed / mid-handshake */
  }
}

/**
 * Messages that are handed over as state rather than read as stream
 * positions, so a `seq` on one never marks it as already applied. Replay
 * control frames are unsequenced by contract. A pending approval card is
 * re-sent on every `view_session` with the `seq` its first broadcast stamped
 * into the broker's stored payload, which is always at or below the cursor;
 * the card renderer is idempotent per `decision_id`.
 */
const UNSEQUENCED_TYPES = new Set(["replay_begin", "replay_end", "await_permission"]);

/**
 * Apply one parsed event, then record the cursor. Port of app.js:5162-5169.
 *
 * A sequenced event at or below its root's cursor was already applied, so it
 * is dropped before dispatch: a reconnect replay must not let an older event
 * overwrite an outcome the client has already processed.
 *
 * Record the cursor only AFTER onEvent has applied it: advancing first
 * would let a handler that throws leave the client claiming an event it
 * never rendered, and the resume would then skip it for good.
 */
export function handleIncomingMessage(data: unknown): void {
  let m: WsMessage;
  try {
    m = JSON.parse(data as string) as WsMessage;
  } catch {
    return;
  }
  const rid = m && m.root_frame_id;
  const raw = m && m.seq;
  // One definition of a usable position for the gate and the writer alike: a
  // non-finite seq recorded as the cursor would drop every later event.
  const sq = typeof raw === "number" && Number.isFinite(raw) && raw > 0 ? raw : 0;
  if (rid && sq && !UNSEQUENCED_TYPES.has(String(m.type)) && sq <= (_seqSeen.value[rid] || 0)) return;
  onEvent(m);
  // Written in place on purpose. `_seqSeen` is a resume cursor and duplicate
  // gate, not state anything renders: its readers (`sub`, the gate above, a
  // history read comparing before and after) sample it, none subscribes, and
  // this runs for every event, where a copy per event would be pure churn.
  // The E2E contract relies on the same object staying in `S._seqSeen` across
  // nested writes.
  const seen = _seqSeen.value;
  if (rid && sq > (seen[rid] || 0)) seen[rid] = sq;
}

export const connectWS: ConnectWS = () => {
  const socket = new (resolveWsCtor())(wsUrl());
  wsSignal.value = socket;
  socket.onopen = () => {
    conn(true);
    if (currentId.value) sub(currentId.value);
  };
  socket.onclose = () => {
    conn(false);
    setTimeout(connectWS, WS_RECONNECT_MS);
  };
  socket.onmessage = (e) => {
    handleIncomingMessage(e.data);
  };
  clearInterval(connectWS._p);
  connectWS._p = setInterval(() => {
    try {
      socket.readyState === 1 && socket.send('{"type":"ping"}');
    } catch {
      /* ignore ping failure */
    }
  }, WS_PING_MS);
};

export function clearWsPing(): void {
  clearInterval(connectWS._p);
  connectWS._p = undefined;
}
