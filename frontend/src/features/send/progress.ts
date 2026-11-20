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
import { actionTimeline } from "../../stores/timeline";
import { api } from "../sessions/api";
import { S } from "../timeline/s";

const COPY: Record<"en" | "zh", Record<string, string>> = {
  en: {
    clockTitle: "This turn has been running for {0}",
    usageTitle: "Tokens this session has used so far: {0} in · {1} out. Click for the breakdown.",
    usageParts:
      "Tokens this session has used so far: {0} new input · {1} cached input · {2} output{3}. Cached input is context sent again and read from the provider's cache.",
    usageReasoning: " (of which {0} reasoning)",
    turnParts: "The latest turn: {0} new input · {1} cached input · {2} output.",
    tokens: "{0} tokens",
    tokensTurn: "{0} tokens · +{1} this turn",
    minutes: "{0} min {1} s",
    seconds: "{0} s",
  },
  zh: {
    clockTitle: "本回合已运行 {0}",
    usageTitle: "本会话累计用量：输入 {0} · 输出 {1} tokens。点击查看明细。",
    usageParts:
      "本会话累计用量：新输入 {0} · 缓存输入 {1} · 输出 {2}{3} tokens。缓存输入是重复发送、由模型服务从缓存读取的上下文。",
    usageReasoning: "（其中推理 {0}）",
    turnParts: "最近一轮：新输入 {0} · 缓存输入 {1} · 输出 {2}。",
    tokens: "{0} tokens",
    tokensTurn: "{0} tokens · 本轮 +{1}",
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

export type UsageBreakdown = { fresh: number; cached: number; output: number; reasoning: number };

/**
 * TURN-7: what the one total is made of. "3.7M tokens" was 95% context read
 * back from the provider's cache. The per-call usage the action timeline
 * already carries is summed: `input_tokens` includes the cached part, so new
 * input is the rest. Null unless the whole timeline of this session is loaded.
 */
export function usageBreakdown(timeline: unknown, fid: string | null): UsageBreakdown | null {
  const view = timeline as { root_frame_id?: unknown; has_earlier?: unknown; groups?: unknown } | null;
  if (!view || !fid || view.root_frame_id !== fid || view.has_earlier === true || !Array.isArray(view.groups)) {
    return null;
  }
  const sum = { input: 0, cached: 0, output: 0, reasoning: 0 };
  for (const group of view.groups as Array<{ usage?: Record<string, unknown> | null }>) {
    const usage = group && group.usage;
    if (!usage) continue;
    sum.input += Number(usage.input_tokens) || 0;
    sum.cached += Number(usage.cache_read) || 0;
    sum.output += Number(usage.output_tokens) || 0;
    sum.reasoning += Number(usage.reasoning_tokens) || 0;
  }
  if (!(sum.input + sum.output > 0)) return null;
  return { fresh: Math.max(0, sum.input - sum.cached), cached: sum.cached, output: sum.output, reasoning: sum.reasoning };
}

type TimelineRow = { turn_id?: unknown; kind?: unknown; usage?: Record<string, unknown> | null };

/**
 * M-18: what the latest turn added. A session total of "936k tokens" said
 * nothing about the turn just run. The latest turn is the turn of the
 * timeline's last row; its model calls are summed when the whole turn is in
 * the loaded window (its own user row is there, or nothing is earlier).
 */
export function latestTurnUsage(timeline: unknown, fid: string | null): (UsageBreakdown & { total: number }) | null {
  const view = timeline as { root_frame_id?: unknown; has_earlier?: unknown; groups?: unknown } | null;
  if (!view || !fid || view.root_frame_id !== fid || !Array.isArray(view.groups)) return null;
  const rows = view.groups as TimelineRow[];
  const turn = [...rows].reverse().find((row) => row && typeof row.turn_id === "string" && row.turn_id)?.turn_id;
  if (!turn) return null;
  const own = rows.filter((row) => row && row.turn_id === turn);
  if (view.has_earlier === true && !own.some((row) => row.kind === "user")) return null;
  const sum = { input: 0, cached: 0, output: 0, reasoning: 0 };
  for (const row of own) {
    const usage = row.usage;
    if (!usage) continue;
    sum.input += Number(usage.input_tokens) || 0;
    sum.cached += Number(usage.cache_read) || 0;
    sum.output += Number(usage.output_tokens) || 0;
    sum.reasoning += Number(usage.reasoning_tokens) || 0;
  }
  const total = sum.input + sum.output;
  if (!(total > 0)) return null;
  return {
    fresh: Math.max(0, sum.input - sum.cached),
    cached: sum.cached,
    output: sum.output,
    reasoning: sum.reasoning,
    total,
  };
}

/** The frame's own totals, kept for the tooltip's fallback wording. */
let lastTotals: { fid: string; input: number; output: number } | null = null;

/**
 * The chip: the session's total and what the latest turn added (M-18); its
 * tooltip the breakdown when the timeline can give one, else input / output.
 */
export function paintUsageTitle(): void {
  if (typeof document === "undefined") return;
  const node = document.getElementById("session-usage");
  const totals = lastTotals;
  if (!node || !totals || totals.fid !== currentId.value) return;
  const turn = latestTurnUsage(actionTimeline.value, totals.fid);
  // The frame's totals are re-read now and then, the timeline arrives live:
  // never "0 tokens · +1.3k this turn".
  const sum = Math.max(totals.input + totals.output, turn ? turn.total : 0);
  node.classList.toggle("hidden", !(sum > 0));
  const total = formatTokens(sum);
  node.textContent = turn ? copy("tokensTurn", total, formatTokens(turn.total)) : copy("tokens", total);
  const parts = usageBreakdown(actionTimeline.value, totals.fid);
  const turnLine = turn
    ? " " + copy("turnParts", turn.fresh.toLocaleString(), turn.cached.toLocaleString(), turn.output.toLocaleString())
    : "";
  node.title = (parts
    ? copy(
        "usageParts",
        parts.fresh.toLocaleString(),
        parts.cached.toLocaleString(),
        parts.output.toLocaleString(),
        parts.reasoning ? copy("usageReasoning", parts.reasoning.toLocaleString()) : "",
      )
    : copy("usageTitle", totals.input.toLocaleString(), totals.output.toLocaleString())) + turnLine;
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
  lastTotals = { fid, input, output };
  paintUsageTitle();
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
  // The timeline is read after the session opens and again as the turn goes on.
  effect(() => {
    void actionTimeline.value;
    untracked(() => followTimeline());
  });
}

let lastTurnTotal = -1;
let usageRefresh: ReturnType<typeof setTimeout> | null = null;

/** A model call landed in the timeline: the session's totals moved too, so they are re-read soon. */
function followTimeline(): void {
  paintUsageTitle();
  const turn = latestTurnUsage(actionTimeline.value, currentId.value);
  const total = turn ? turn.total : 0;
  if (total === lastTurnTotal) return;
  lastTurnTotal = total;
  if (usageRefresh) return;
  usageRefresh = setTimeout(() => {
    usageRefresh = null;
    void refreshSessionUsage();
  }, 500);
}
