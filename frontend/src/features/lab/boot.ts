import { effect, untracked } from "@preact/signals";
import { h, render } from "preact";
import { currentId, _openGen } from "../../stores/session";
import { _replayGap, ws } from "../../stores/stream";
import { hasWsHandler, registerWsHandler } from "../ws/registry";
import { lab, labConnected } from "./state";
import { LabPane } from "./view";
import "./lab.css";

export const LAB_REFRESH_MS = 250;
let timer: ReturnType<typeof setTimeout> | undefined;

export function renderLab(): void {
  if (typeof document === "undefined") return;
  const host = document.getElementById("dock-lab");
  if (host) render(h(LabPane, {}), host);
}
export function resetLab(): void {
  clearTimeout(timer); timer = undefined;
  lab.scope(currentId.value, _openGen.value, true);
}
export async function loadLab(fid: string): Promise<void> {
  if (fid !== currentId.value) return;
  lab.scope(fid, _openGen.value);
  await lab.refresh();
}

/** A trailing read is scheduled even when a prior REST read is still in flight. */
export function scheduleLabRefresh(fid: string | null): void {
  if (!fid || fid !== currentId.value || timer !== undefined) return;
  const generation = _openGen.value;
  timer = setTimeout(() => {
    timer = undefined;
    if (fid === currentId.value && generation === _openGen.value) void loadLab(fid);
  }, LAB_REFRESH_MS);
}

/** Installs only lab_update; replay remains owned by the shared WS module. */
export function bindLabLifecycle(): () => void {
  if (!hasWsHandler("lab_update")) registerWsHandler("lab_update", (m) => {
    scheduleLabRefresh(typeof m.root_frame_id === "string" ? m.root_frame_id : null);
  });
  const scopeEffect = effect(() => {
    const fid = currentId.value, gen = _openGen.value;
    untracked(() => {
      clearTimeout(timer); timer = undefined;
      lab.scope(fid, gen);
      scheduleLabRefresh(fid);
    });
  });
  const gapEffect = effect(() => {
    const gap = _replayGap.value, fid = currentId.value;
    if (gap === fid) untracked(() => scheduleLabRefresh(fid));
  });
  const socketEffect = effect(() => {
    const socket = ws.value as WebSocket | null;
    const opened = () => {
      if (ws.peek() !== socket) return;
      labConnected.value = true;
      scheduleLabRefresh(currentId.peek());
    };
    const closed = () => { if (ws.peek() === socket) labConnected.value = false; };
    untracked(() => {
      labConnected.value = socket?.readyState === 1;
      if (labConnected.value) scheduleLabRefresh(currentId.peek());
    });
    socket?.addEventListener?.("open", opened);
    socket?.addEventListener?.("close", closed);
    return () => {
      socket?.removeEventListener?.("open", opened);
      socket?.removeEventListener?.("close", closed);
    };
  });
  return () => { scopeEffect(); gapEffect(); socketEffect(); clearTimeout(timer); timer = undefined; };
}
let installed = false;
export function bootLab(target: Record<string, unknown> = globalThis as Record<string, unknown>): void {
  target.loadLab = loadLab;
  target.resetLab = resetLab;
  target.renderLab = renderLab;
  if (!installed) { installed = true; bindLabLifecycle(); }
}
