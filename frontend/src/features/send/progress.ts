/**
 * How long the running turn has been going, and what the session has used.
 *
 * A 32-minute turn showed one unchanging "Running…" and nothing else; the
 * session's token count was buried in the action timeline. Beside Stop, the
 * turn clock counts the turn's elapsed time (from the execution's own start
 * when the queue snapshot names it, so a reload keeps counting from the real
 * start); in the conversation header, a chip shows the session's tokens so
 * far and opens the usage breakdown. A running Cell's own elapsed time is on
 * its card (`messages/cardState.ts`).
 */

import { effect, untracked } from "@preact/signals";
import { LANG } from "../../i18n/runtime";
import { currentId } from "../../stores/session";
import { running } from "../../stores/stream";
import { api } from "../sessions/api";
import { S } from "../timeline/s";

const COPY: Record<"en" | "zh", Record<string, string>> = {
  en: {
    clockTitle: "This turn has been running for {0}",
    usageTitle: "Tokens this session has used so far: {0} in · {1} out. Click for the breakdown.",
    tokens: "{0} tokens",
    minutes: "{0} min {1} s",
    seconds: "{0} s",
  },
  zh: {
    clockTitle: "本回合已运行 {0}",
    usageTitle: "本会话累计用量：输入 {0} · 输出 {1} tokens。点击查看明细。",
    tokens: "{0} tokens",
    minutes: "{0} 分 {1} 秒",
    seconds: "{0} 秒",
  },
};

function copy(key: string, ...args: Array<string | number>): string {
  let text = (COPY[LANG === "zh" ? "zh" : "en"] || COPY.en)[key] || key;
  args.forEach((value, index) => {
    text = text.split("{" + index + "}").join(String(value));
  });
  return text;
}

/** "0:07", "12:03", "1:02:03". */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** "7 s", "12 min 3 s" (the clock's title). */
export function formatElapsedLong(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  return m ? copy("minutes", m, total % 60) : copy("seconds", total);
}

/** "850", "12.3k", "1.2M". */
export function formatTokens(n: number): string {
  if (!Number.isFinite(n) || n < 1000) return String(Math.max(0, Math.round(n || 0)));
  if (n < 1_000_000) return (n / 1000).toFixed(n < 10_000 ? 1 : 0).replace(/\.0$/, "") + "k";
  return (n / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
}

let localStart = 0;

/** The running agent execution's own start, when the queue snapshot names it. */
function executionStart(): number | null {
  const owner = (S.executionQueue || {}).owner;
  const startedAt = owner && owner.owner && owner.owner.kind === "agent" ? Number(owner.started_at) : NaN;
  return Number.isFinite(startedAt) && startedAt > 0 ? startedAt * 1000 : null;
}

export function paintTurnClock(now: number = Date.now()): void {
  if (typeof document === "undefined") return;
  const node = document.getElementById("turn-clock");
  if (!node) return;
  if (!running.value) {
    node.classList.add("hidden");
    node.textContent = "";
    return;
  }
  const from = executionStart() ?? localStart;
  const ms = Math.max(0, now - from);
  node.textContent = formatElapsed(ms);
  node.title = copy("clockTitle", formatElapsedLong(ms));
  node.classList.remove("hidden");
}

let usageSeq = 0;

/** Re-read the open session's token totals into the header chip. */
export async function refreshSessionUsage(): Promise<void> {
  if (typeof document === "undefined") return;
  const node = document.getElementById("session-usage");
  const fid = currentId.value;
  const seq = ++usageSeq;
  if (!node) return;
  if (!fid) {
    node.classList.add("hidden");
    return;
  }
  let frame: { input_tokens?: unknown; output_tokens?: unknown };
  try {
    frame = (await api(`/frames/${encodeURIComponent(fid)}`)) as typeof frame;
  } catch {
    return;
  }
  if (seq !== usageSeq || currentId.value !== fid) return;
  const input = Number(frame.input_tokens || 0);
  const output = Number(frame.output_tokens || 0);
  const total = input + output;
  node.classList.toggle("hidden", !(total > 0));
  node.textContent = copy("tokens", formatTokens(total));
  node.title = copy("usageTitle", input.toLocaleString(), output.toLocaleString());
}

let bound = false;
let timer: ReturnType<typeof setInterval> | null = null;
let ticks = 0;

function tick(): void {
  paintTurnClock();
  // The usage moves with every model call: re-read it now and then.
  ticks += 1;
  if (ticks % 15 === 0) void refreshSessionUsage();
}

/** Bound once from `bindComposer`, after the Shell has rendered. */
export function bindTurnProgress(onUsageClick?: () => void): void {
  if (bound || typeof document === "undefined") return;
  bound = true;
  const chip = document.getElementById("session-usage");
  if (chip && onUsageClick) chip.onclick = () => onUsageClick();
  effect(() => {
    const on = !!running.value;
    untracked(() => {
      if (on) {
        localStart = Date.now();
        ticks = 0;
        if (!timer) timer = setInterval(tick, 1000);
      } else if (timer) {
        clearInterval(timer);
        timer = null;
      }
      paintTurnClock();
      void refreshSessionUsage();
    });
  });
  effect(() => {
    void currentId.value;
    untracked(() => void refreshSessionUsage());
  });
}
