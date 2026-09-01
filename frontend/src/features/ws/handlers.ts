import {
  _artBust,
  _tbl,
  artifacts as artifactsSignal,
} from "../../stores/artifacts";
import { _liveCell, liveCells } from "../../stores/notebook";
import { currentId, project, sessions as sessionsSignal } from "../../stores/session";
import { _replayGap, _seqSeen, _streamEpoch, stream as liveStream } from "../../stores/stream";
import { eventFrameId, mine, tryLane } from "./guards";
import { hasWsHandler, registerWsHandler } from "./registry";
import type { WsHandler, WsMessage } from "./types";

export const LOAD_SESSIONS_DEBOUNCE_MS = 300;
export const LOAD_ARTIFACTS_DEBOUNCE_MS = 150;

const TERMINAL_FRAME_STATUS = [
  "completed",
  "failed",
  "cancelled",
  "blocked_by_guardian",
  "success",
  "done",
  "ready",
];

let loadSessionsImpl: (() => void | Promise<void>) | null = null;
let loadArtifactsImpl: ((id: string) => void | Promise<void>) | null = null;
let frameUpdateTurnHandler: WsHandler | null = null;
let artifactCreatedSideEffects: WsHandler | null = null;
let sessionsTimer: ReturnType<typeof setTimeout> | null = null;
let artifactsTimer: ReturnType<typeof setTimeout> | null = null;

/** F-13 assigns the REST session walk. Until then the debounce is a no-op. */
export function setLoadSessionsImpl(fn: (() => void | Promise<void>) | null): void {
  loadSessionsImpl = fn;
}

/** F-17 assigns the REST artifact list fetch. */
export function setLoadArtifactsImpl(
  fn: ((id: string) => void | Promise<void>) | null,
): void {
  loadArtifactsImpl = fn;
}

/**
 * F-11 owns turn-ticket / turnDone inside `frame_update`. One handler per
 * type: later lanes must not `registerWsHandler("frame_update", …)` again.
 */
export function setFrameUpdateTurnHandler(handler: WsHandler | null): void {
  frameUpdateTurnHandler = handler;
}

/**
 * F-17 owns the rest of `artifact_created` (syncArtifactVersion, viewer,
 * live cell figures). Runs after the incremental upsert.
 */
export function setArtifactCreatedSideEffects(handler: WsHandler | null): void {
  artifactCreatedSideEffects = handler;
}

export function scheduleLoadSessions(): void {
  if (sessionsTimer !== null) clearTimeout(sessionsTimer);
  sessionsTimer = setTimeout(() => {
    sessionsTimer = null;
    const fn = loadSessionsImpl;
    if (fn) void fn();
  }, LOAD_SESSIONS_DEBOUNCE_MS);
}

export function scheduleLoadArtifacts(): void {
  if (artifactsTimer !== null) clearTimeout(artifactsTimer);
  artifactsTimer = setTimeout(() => {
    artifactsTimer = null;
    const id = currentId.value;
    const fn = loadArtifactsImpl;
    if (id && fn) void fn(id);
  }, LOAD_ARTIFACTS_DEBOUNCE_MS);
}

export function clearWsDebouncers(): void {
  if (sessionsTimer !== null) {
    clearTimeout(sessionsTimer);
    sessionsTimer = null;
  }
  if (artifactsTimer !== null) {
    clearTimeout(artifactsTimer);
    artifactsTimer = null;
  }
}

/** In-place patch of `S.sessions` from a `frame_update` payload. Array identity kept. */
export function patchSessionFromFrameUpdate(m: WsMessage): void {
  const fid = String(m.frame_id || m.root_frame_id || "");
  if (!fid) return;
  const rows = sessionsSignal.value;
  if (!Array.isArray(rows)) return;
  const row = rows.find((item) => {
    return Boolean(item && typeof item === "object" && (item as { id?: unknown }).id === fid);
  }) as Record<string, unknown> | undefined;
  if (!row) return;
  const status = m.status;
  if (status === "processing") row.running = true;
  if (typeof status === "string" && TERMINAL_FRAME_STATUS.includes(status)) {
    row.running = false;
  }
  if (status === "titled" && m.task_summary) row.task_summary = m.task_summary;
  if (status === "updated") {
    if (m.name) row.name = m.name;
    if (m.task_summary) row.task_summary = m.task_summary;
  }
}

function artifactPayload(m: WsMessage): Record<string, unknown> {
  const nested = m.artifact;
  if (nested && typeof nested === "object" && !Array.isArray(nested)) return nested;
  return {};
}

/**
 * Incremental upsert from any of the four `artifact_created` shapes.
 * Bare refresh (no id) is a no-op here; the debounced REST reload follows.
 */
export function upsertArtifactFromEvent(m: WsMessage): Record<string, unknown> | null {
  const nested = artifactPayload(m);
  const aid = nested.id || nested.artifact_id || m.artifact_id;
  if (!aid) return null;
  const id = String(aid);
  const row: Record<string, unknown> = { ...nested, id };
  if (row.artifact_id == null) row.artifact_id = id;
  if (row.filename == null && m.filename != null) row.filename = m.filename;
  if (row.producing_cell_id == null && m.producing_cell_id != null) {
    row.producing_cell_id = m.producing_cell_id;
  }
  const list = artifactsSignal.value;
  const rows: unknown[] = Array.isArray(list) ? list : [];
  if (rows !== list) artifactsSignal.value = rows;
  const idx = rows.findIndex((item) => {
    if (!item || typeof item !== "object") return false;
    const rec = item as { id?: unknown; artifact_id?: unknown };
    return rec.id === id || rec.artifact_id === id;
  });
  if (idx >= 0) {
    Object.assign(rows[idx] as object, row);
  } else {
    rows.push(row);
  }
  const bust = _artBust.value;
  bust[id] = row.version_id || String(Date.now());
  const fn = String(row.filename || "");
  const tbl = _tbl.value;
  if (tbl && fn) {
    const base = fn.split("/").pop();
    if (base) {
      for (const k in tbl) if (k.indexOf(base) !== -1) delete tbl[k];
    }
  }
  return row;
}

function handleReplayBegin(m: WsMessage): void {
  const fid = eventFrameId(m);
  // A restarted daemon issues a new epoch; every cursor we hold describes a
  // stream it never produced, so drop them all rather than resuming from a
  // position it cannot interpret.
  if (m.epoch && m.epoch !== _streamEpoch.value) {
    _streamEpoch.value = m.epoch;
    _seqSeen.value = {};
  }
  if (mine(fid)) {
    const live = liveStream.value as { wrap?: { remove: () => void } } | null;
    if (live && live.wrap) live.wrap.remove();
    liveStream.value = null;
    liveCells.value = [];
    _liveCell.value = null;
    // `gap` means the server could not serve our cursor — the buffer had
    // aged past it, or it belonged to a previous run. Replaying from a hole
    // we cannot see would leave the transcript quietly wrong, so reload it.
    if (m.gap) {
      const seen = _seqSeen.value;
      if (typeof fid === "string") seen[fid] = 0;
      _replayGap.value = fid;
    }
  }
}

function handleReplayEnd(m: WsMessage): void {
  const fid = eventFrameId(m);
  if (mine(fid)) {
    if (_replayGap.value === fid) {
      _replayGap.value = null;
      tryLane("openConversation", fid, project.value);
    }
    tryLane("down");
  }
}

function handleFrameUpdate(m: WsMessage): void {
  const fid = eventFrameId(m);
  if (mine(m.frame_id) || mine(fid)) {
    if (frameUpdateTurnHandler) frameUpdateTurnHandler(m);
  }
  patchSessionFromFrameUpdate(m);
  scheduleLoadSessions();
}

function handleArtifactCreated(m: WsMessage): void {
  upsertArtifactFromEvent(m);
  if (artifactCreatedSideEffects) artifactCreatedSideEffects(m);
  scheduleLoadArtifacts();
}

function registerUnlessPresent(type: string, handler: WsHandler): void {
  if (!hasWsHandler(type)) registerWsHandler(type, handler);
}

export function registerBuiltinHandlers(): void {
  registerUnlessPresent("replay_begin", handleReplayBegin);
  registerUnlessPresent("replay_end", handleReplayEnd);
  registerUnlessPresent("frame_update", handleFrameUpdate);
  registerUnlessPresent("artifact_created", handleArtifactCreated);
}
