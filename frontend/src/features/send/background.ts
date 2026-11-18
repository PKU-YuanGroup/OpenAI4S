/**
 * The session's background cells (`host.exec_background`), above the composer.
 *
 * A cell an agent left running, or one launched from the Notebook, kept going
 * with nothing on screen to show it and no way to stop it: Stop ends a turn's
 * own background cells (and says so), but any other one could only be found
 * by asking the agent. While any is running, this strip lists each with its
 * first statement, its elapsed time and the last line it printed, and a Stop
 * for that cell alone (`POST /frames/{fid}/background/{exec_id}/stop`).
 */

import { effect, untracked } from "@preact/signals";
import { LANG } from "../../i18n/runtime";
import { currentId } from "../../stores/session";
import { running } from "../../stores/stream";
import { api, apiErrorText } from "../sessions/api";
import { hint } from "../sessions/chrome";
import { el } from "../messages/dom";
import { formatElapsed } from "./progress";

export type BackgroundCell = {
  exec_id: string;
  status?: string;
  started_at?: number | null;
  stop_requested?: boolean;
  launched_by?: string;
  code_preview?: string;
  output_tail?: string;
};

const COPY: Record<"en" | "zh", Record<string, string>> = {
  en: {
    head: "Running in the background · {0}",
    headTitle:
      "Cells started with host.exec_background keep running outside the turn. Stopping a turn stops the ones it started; stop any other here.",
    stop: "Stop",
    stopping: "Stopping…",
    stopTitle: "Stop this background cell",
    fromNotebook: "from the Notebook",
    stopFailed: "Could not stop the background cell: {0}",
    stoppedWithTurn: "Stopped, and stopped {0} background cell(s) this turn started",
  },
  zh: {
    head: "后台运行中 · {0} 个",
    headTitle: "用 host.exec_background 启动的单元会在回合之外继续运行。停止回合会停下该回合启动的；其余的可以在这里逐个停止。",
    stop: "停止",
    stopping: "正在停止…",
    stopTitle: "停止这个后台单元",
    fromNotebook: "Notebook 启动",
    stopFailed: "停止后台单元失败：{0}",
    stoppedWithTurn: "已停止，并停止了本回合启动的 {0} 个后台任务",
  },
};

export function backgroundCopy(key: string, ...args: Array<string | number>): string {
  let text = (COPY[LANG === "zh" ? "zh" : "en"] || COPY.en)[key] || key;
  args.forEach((value, index) => {
    text = text.split("{" + index + "}").join(String(value));
  });
  return text;
}

/** Cells this tab asked to stop, until the listing stops calling them running. */
const stopping = new Set<string>();
let cells: BackgroundCell[] = [];
let readSeq = 0;

export function renderBackgroundStrip(now: number = Date.now()): void {
  if (typeof document === "undefined") return;
  const box = document.getElementById("bg-strip");
  if (!box) return;
  const live = cells.filter((cell) => cell.status === "running");
  box.innerHTML = "";
  box.classList.toggle("hidden", !live.length);
  if (!live.length) return;
  const head = el("div", "bg-head", backgroundCopy("head", live.length));
  head.title = backgroundCopy("headTitle");
  box.appendChild(head);
  const frameId = currentId.value;
  live.forEach((cell) => {
    const row = el("div", "bg-row");
    row.dataset.execId = cell.exec_id;
    row.appendChild(el("span", "bg-code", cell.code_preview || cell.exec_id));
    if (cell.launched_by === "user") row.appendChild(el("span", "bg-origin", backgroundCopy("fromNotebook")));
    if (cell.output_tail) row.appendChild(el("span", "bg-tail", "↳ " + cell.output_tail));
    const started = Number(cell.started_at || 0);
    if (started) row.appendChild(el("span", "bg-time", formatElapsed(now - started)));
    const pending = stopping.has(cell.exec_id) || !!cell.stop_requested;
    const btn = el(
      "button",
      "outline-btn small bg-stop",
      backgroundCopy(pending ? "stopping" : "stop"),
    ) as HTMLButtonElement;
    btn.type = "button";
    btn.title = backgroundCopy("stopTitle");
    btn.disabled = pending;
    btn.onclick = () => {
      btn.disabled = true;
      btn.textContent = backgroundCopy("stopping");
      void stopBackgroundCell(frameId, cell.exec_id);
    };
    row.appendChild(btn);
    box.appendChild(row);
  });
}

/** Re-read the open session's background cells and repaint the strip. */
export async function refreshBackgroundCells(): Promise<void> {
  const fid = currentId.value;
  const seq = ++readSeq;
  if (!fid) {
    cells = [];
    renderBackgroundStrip();
    return;
  }
  let listed: BackgroundCell[];
  try {
    const answer = (await api(`/frames/${encodeURIComponent(fid)}/background`)) as { cells?: unknown };
    listed = Array.isArray(answer.cells) ? (answer.cells as BackgroundCell[]) : [];
  } catch {
    return;
  }
  if (seq !== readSeq || currentId.value !== fid) return;
  cells = listed.filter((cell) => cell && typeof cell.exec_id === "string");
  stopping.forEach((id) => {
    if (!cells.some((cell) => cell.exec_id === id && cell.status === "running")) stopping.delete(id);
  });
  renderBackgroundStrip();
}

async function stopBackgroundCell(fid: string | null, execId: string): Promise<void> {
  if (!fid) return;
  stopping.add(execId);
  try {
    await api(`/frames/${encodeURIComponent(fid)}/background/${encodeURIComponent(execId)}/stop`, {
      method: "POST",
      body: "{}",
    });
  } catch (error) {
    stopping.delete(execId);
    hint(backgroundCopy("stopFailed", apiErrorText(error)), true);
  }
  // The stop escalates on its own thread; look again shortly.
  setTimeout(() => void refreshBackgroundCells(), 1500);
  void refreshBackgroundCells();
}

/** What a turn's Stop says when it also stopped that turn's background cells. */
export function backgroundStoppedHint(result: unknown): string {
  const stopped =
    result && typeof result === "object" ? (result as { background_stopped?: unknown }).background_stopped : null;
  return Array.isArray(stopped) && stopped.length ? backgroundCopy("stoppedWithTurn", stopped.length) : "";
}

let bound = false;
let poll: ReturnType<typeof setInterval> | null = null;

function tick(): void {
  const live = cells.some((cell) => cell.status === "running");
  if (live || running.value) void refreshBackgroundCells();
  else renderBackgroundStrip();
}

/** Bound once from `bindComposer`. Reads on a session switch and run start/end; polls while anything runs. */
export function bindBackgroundCells(): void {
  if (bound || typeof document === "undefined") return;
  bound = true;
  effect(() => {
    void currentId.value;
    void running.value;
    untracked(() => {
      void refreshBackgroundCells();
      if (!poll && typeof setInterval === "function") poll = setInterval(tick, 5000);
    });
  });
}

/** Test seam. */
export function resetBackgroundCells(): void {
  cells = [];
  stopping.clear();
  readSeq += 1;
}
