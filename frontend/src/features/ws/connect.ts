import { effect } from "@preact/signals";
import { currentId, project } from "../../stores/session";
import { _seqSeen, _streamEpoch, ws as wsSignal } from "../../stores/stream";
import { onEvent } from "./registry";
import type { WsMessage } from "./types";

export const API = "/api/v1";
export const WS_PING_MS = 25000;
export const WS_RECONNECT_MS = 1500;
/**
 * How long a ping may go unanswered. Any frame counts as an answer: the
 * server sends a pong for every ping, and a busy stream proves the same.
 */
export const WS_PONG_TIMEOUT_MS = 10000;

export type WsSocket = {
  readyState: number;
  send: (data: string) => void;
  close?: () => void;
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

/**
 * Name the session directory this tab's sidebar lists -- the open project,
 * or every project on Home -- so the server sends it `session_status` for
 * sessions this tab is not viewing (one stopped or started elsewhere).
 */
export function subDirectory(): void {
  try {
    const socket = wsSignal.value as WsSocket | null;
    if (socket && socket.readyState === 1) {
      socket.send(JSON.stringify({ type: "view_directory", project_id: project.value || null }));
    }
  } catch {
    /* closed / mid-handshake: onopen names it again */
  }
}

let directoryWatched = false;

/** Re-name the directory whenever the sidebar changes project. */
export function watchDirectory(): void {
  if (directoryWatched) return;
  directoryWatched = true;
  effect(() => {
    void project.value;
    subDirectory();
  });
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

let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let pongTimer: ReturnType<typeof setTimeout> | null = null;

function clearPongTimer(): void {
  if (pongTimer !== null) clearTimeout(pongTimer);
  pongTimer = null;
}

/**
 * Treat a socket that went silent as closed. Chromium's offline mode, a
 * sleeping laptop or a dropped route freeze a WebSocket without ever firing
 * `close`, so the banner (bound to close) never showed and the page claimed a
 * live connection for as long as the user waited. Its handlers are detached
 * first, so a late real `close` cannot start a second reconnect loop or show
 * the banner over a newer, healthy socket; then the ordinary close path runs
 * once (banner, reconnect).
 */
export function declareSocketDead(socket: WsSocket | null): void {
  if (!socket || wsSignal.value !== socket) return;
  clearPongTimer();
  const fire = socket.onclose;
  socket.onclose = null;
  socket.onmessage = null;
  socket.onopen = null;
  try {
    socket.close?.();
  } catch {
    /* already closing */
  }
  if (fire) fire({});
}

let networkWatched = false;

/** The browser's own word on connectivity, faster than any ping. */
function watchNetwork(): void {
  if (networkWatched || typeof window === "undefined" || typeof window.addEventListener !== "function") return;
  networkWatched = true;
  window.addEventListener("offline", () => {
    declareSocketDead(wsSignal.value as WsSocket | null);
  });
  window.addEventListener("online", () => {
    // Only a pending reconnect is waiting on the network; a live socket (or a
    // connect already in flight) owns the connection. Back now, so do not sit
    // out the rest of that delay.
    if (reconnectTimer === null) return;
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
    connectWS();
  });
}

export const connectWS: ConnectWS = () => {
  reconnectTimer = null;
  clearPongTimer();
  watchNetwork();
  const socket = new (resolveWsCtor())(wsUrl());
  wsSignal.value = socket;
  socket.onopen = () => {
    conn(true);
    if (currentId.value) sub(currentId.value);
    subDirectory();
  };
  socket.onclose = () => {
    conn(false);
    clearPongTimer();
    if (reconnectTimer !== null) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connectWS, WS_RECONNECT_MS);
  };
  socket.onmessage = (e) => {
    // Any frame proves the connection is alive, a pong or not.
    clearPongTimer();
    handleIncomingMessage(e.data);
  };
  clearInterval(connectWS._p);
  connectWS._p = setInterval(() => {
    try {
      if (socket.readyState !== 1) return;
      socket.send('{"type":"ping"}');
      if (pongTimer === null) {
        pongTimer = setTimeout(() => {
          pongTimer = null;
          declareSocketDead(socket);
        }, WS_PONG_TIMEOUT_MS);
      }
    } catch {
      /* ignore ping failure */
    }
  }, WS_PING_MS);
};

/** Teardown: every timer the socket loop owns (ping, pong wait, reconnect). */
export function clearWsPing(): void {
  clearInterval(connectWS._p);
  connectWS._p = undefined;
  clearPongTimer();
  if (reconnectTimer !== null) clearTimeout(reconnectTimer);
  reconnectTimer = null;
}
