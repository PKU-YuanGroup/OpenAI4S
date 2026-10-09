/**
 * Canonical Auto Mode events as refresh hints, plus reconnect and reopen.
 *
 * docs/auto-mode.md, "Workbench status surface" §7: the socket only says that
 * a transition already committed, so every hint becomes a GET and no event
 * field is copied onto the lines. The registry allows one handler per type and
 * the send lane already owns `candidate_ready` and `auto_run_terminal` (the
 * gated-candidate card and the workbench refresh); it calls `autoModeHint`
 * from those handlers rather than giving them up. This lane registers only the
 * other five.
 */

import { effect, untracked } from "@preact/signals";
import { onLanguageChange } from "../../i18n/runtime";
import { _openGen, currentId } from "../../stores/session";
import { ws as wsSignal } from "../../stores/stream";
import { mine } from "../ws/guards";
import { registerWsHandler } from "../ws/registry";
import type { WsMessage } from "../ws/types";
import {
  autoModeAuditsContextChanged,
  autoModeAuditsOpen,
  scheduleAutoModeAuditsRefresh,
} from "./audits";
import { autoModeStatusVisible, repaintAutoModeBlock } from "./menu";
import { refreshAutoModeStatus, syncAutoModeContext } from "./status";
import { CANONICAL_AUTO_EVENTS } from "./types";

/** The canonical types no other lane registers. */
export const AUTO_MODE_HINT_TYPES = [
  "auto_run_started",
  "auto_audit_started",
  "auto_audit_completed",
  "repair_started",
  "repair_completed",
] as const;

const STATUS_DEBOUNCE_MS = 50;

let statusTimer: ReturnType<typeof setTimeout> | null = null;
let installed = false;
let disposers: Array<() => void> = [];
let watchedSocket: unknown = null;

/** Coalesce a burst (an audit's start and completion) into one GET. */
export function scheduleAutoModeStatusRefresh(): void {
  if (statusTimer !== null) clearTimeout(statusTimer);
  statusTimer = setTimeout(() => {
    statusTimer = null;
    if (autoModeStatusVisible()) void refreshAutoModeStatus();
  }, STATUS_DEBOUNCE_MS);
}

/**
 * One canonical event. Never throws: the send lane calls this from handlers
 * whose own work must not be lost to it, and the resume cursor advances only
 * after the handler returns.
 */
export function autoModeHint(m: WsMessage | null | undefined): void {
  try {
    if (!m || typeof m.type !== "string" || !CANONICAL_AUTO_EVENTS.has(m.type)) return;
    if (!mine(m.root_frame_id)) return;
    if (autoModeStatusVisible()) scheduleAutoModeStatusRefresh();
    if (m.type === "auto_audit_started" || m.type === "auto_audit_completed") {
      scheduleAutoModeAuditsRefresh(m.subject_kind);
    } else if (m.type === "auto_run_terminal") {
      scheduleAutoModeAuditsRefresh();
    }
  } catch {
    /* a lost hint is recovered by the next GET */
  }
}

/** Register the five types this lane owns. Idempotent; a type someone else took still raises. */
export function registerAutoModeHandlers(): void {
  if (installed) return;
  for (const type of AUTO_MODE_HINT_TYPES) registerWsHandler(type, autoModeHint);
  installed = true;
}

function onSocketOpen(): void {
  // Hints sent while the socket was down are gone; the GET is the recovery.
  if (autoModeStatusVisible()) scheduleAutoModeStatusRefresh();
  if (autoModeAuditsOpen()) scheduleAutoModeAuditsRefresh();
}

/**
 * Reopen, reconnect and language switches. Returns a disposer (tests).
 *
 * - Reopen: `currentId` or `_openGen` moved. The status re-keys (dropping any
 *   read still in flight for the previous opening) and is read again if shown.
 * - Reconnect: every socket after the first that opens.
 */
export function watchAutoModeLifecycle(): () => void {
  const stopContext = effect(() => {
    void currentId.value;
    void _openGen.value;
    untracked(() => {
      if (!syncAutoModeContext()) return;
      if (autoModeStatusVisible()) void refreshAutoModeStatus();
      autoModeAuditsContextChanged();
    });
  });
  const stopSocket = effect(() => {
    const socket = wsSignal.value as { addEventListener?: (type: string, fn: () => void) => void } | null;
    untracked(() => {
      if (!socket || socket === watchedSocket) return;
      const reconnect = watchedSocket !== null;
      watchedSocket = socket;
      if (!reconnect || typeof socket.addEventListener !== "function") return;
      socket.addEventListener("open", onSocketOpen);
    });
  });
  const stopLanguage = onLanguageChange(() => repaintAutoModeBlock());
  return () => {
    stopContext();
    stopSocket();
    stopLanguage();
  };
}

/** Install the handlers and watchers once. */
export function installAutoModeHints(): void {
  registerAutoModeHandlers();
  if (!disposers.length) disposers = [watchAutoModeLifecycle()];
}

/** Test seam. Call after `resetWsHandlers()`, which this cannot observe. */
export function resetAutoModeHints(): void {
  if (statusTimer !== null) clearTimeout(statusTimer);
  statusTimer = null;
  for (const dispose of disposers) dispose();
  disposers = [];
  installed = false;
  watchedSocket = null;
}
