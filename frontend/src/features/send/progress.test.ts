/**
 * The running turn shows how long it has been going, from the execution's own
 * start when the queue names it, and the session header shows its tokens.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLang } from "../../i18n/runtime";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { running } from "../../stores/stream";
import { S } from "../timeline/s";
import { actionTimeline } from "../../stores/timeline";
import {
  formatElapsed,
  formatTokens,
  paintTurnClock,
  paintUsageTitle,
  refreshSessionUsage,
  usageBreakdown,
} from "./progress";

class Node {
  textContent = "";
  title = "";
  private classes = new Set<string>(["hidden"]);
  classList = {
    add: (name: string) => this.classes.add(name),
    remove: (name: string) => this.classes.delete(name),
    toggle: (name: string, on?: boolean) => {
      const next = on ?? !this.classes.has(name);
      if (next) this.classes.add(name);
      else this.classes.delete(name);
      return next;
    },
    contains: (name: string) => this.classes.has(name),
  };
}

let clock: Node;
let usage: Node;

beforeEach(async () => {
  resetStoreFields();
  await setLang("zh");
  clock = new Node();
  usage = new Node();
  vi.stubGlobal("document", {
    getElementById: (id: string) => (id === "turn-clock" ? clock : id === "session-usage" ? usage : null),
    querySelectorAll: () => [],
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  S.executionQueue = null;
});

describe("turn progress", () => {
  it("formats an elapsed time and a token count compactly", () => {
    expect(formatElapsed(7_000)).toBe("0:07");
    expect(formatElapsed(723_000)).toBe("12:03");
    expect(formatElapsed(3_723_000)).toBe("1:02:03");
    expect(formatTokens(850)).toBe("850");
    expect(formatTokens(1_234)).toBe("1.2k");
    expect(formatTokens(12_345)).toBe("12k");
    expect(formatTokens(1_250_000)).toBe("1.3M");
  });

  it("counts from the running execution's own start, so a reload keeps the real elapsed time", () => {
    running.value = true;
    S.executionQueue = {
      owner: { execution_id: "e", owner: { kind: "agent", id: "job" }, started_at: 1_000 },
      queue: [],
    };
    paintTurnClock(1_000_000 + 125_000);
    expect(clock.classList.contains("hidden")).toBe(false);
    expect(clock.textContent).toBe("2:05");
    expect(clock.title).toBe("本回合已运行 2 分 5 秒");

    running.value = false;
    paintTurnClock();
    expect(clock.classList.contains("hidden")).toBe(true);
  });

  it("shows the session's tokens in the header, and nothing for a session that used none", async () => {
    currentId.value = "f-u";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ id: "f-u", input_tokens: 10_123, output_tokens: 2_201 }),
      })),
    );
    await refreshSessionUsage();
    expect(usage.classList.contains("hidden")).toBe(false);
    expect(usage.textContent).toBe("12k token");
    expect(usage.title).toContain("10,123");
    expect(usage.title).toContain("2,201");

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ id: "f-u" }) })),
    );
    await refreshSessionUsage();
    expect(usage.classList.contains("hidden")).toBe(true);
  });

  it("the chip's tooltip says how much of the total was new, cached, output and reasoning (TURN-7)", async () => {
    currentId.value = "f-u";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ id: "f-u", input_tokens: 4_080_795, output_tokens: 58_464 }),
      })),
    );
    // Before the timeline is read: the two totals, as before.
    await refreshSessionUsage();
    expect(usage.title).toContain("输入 4,080,795");
    // Two model calls; `input_tokens` includes what was read from the cache.
    const call = (input: number, cached: number, output: number, reasoning: number) => ({
      usage: { input_tokens: input, cache_read: cached, output_tokens: output, reasoning_tokens: reasoning },
    });
    actionTimeline.value = {
      root_frame_id: "f-u",
      has_earlier: false,
      groups: [call(80_795, 0, 8_464, 3_720), { usage: null }, call(4_000_000, 3_869_032, 50_000, 20_000)],
    };
    paintUsageTitle();
    expect(usage.title).toBe(
      "本会话累计用量：新输入 211,763 · 缓存输入 3,869,032 · 输出 58,464（其中推理 23,720） token。缓存输入是重复发送、由模型服务从缓存读取的上下文。",
    );
    expect(usageBreakdown(actionTimeline.value, "f-u")).toEqual({
      fresh: 211_763,
      cached: 3_869_032,
      output: 58_464,
      reasoning: 23_720,
    });
    // Another session's timeline, or one with earlier pages not loaded, is not a breakdown of this one.
    expect(usageBreakdown(actionTimeline.value, "f-other")).toBeNull();
    expect(usageBreakdown({ ...(actionTimeline.value as object), has_earlier: true }, "f-u")).toBeNull();
  });

  it("the chip says what the latest turn added, not only the session's total (M-18)", async () => {
    currentId.value = "f-t";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ id: "f-t", input_tokens: 909_502, output_tokens: 26_578 }),
      })),
    );
    const row = (turn: string, kind: string, input = 0, cached = 0, output = 0) => ({
      turn_id: turn,
      kind,
      usage: input || output ? { input_tokens: input, cache_read: cached, output_tokens: output } : null,
    });
    actionTimeline.value = {
      root_frame_id: "f-t",
      has_earlier: true,
      groups: [
        row("turn-1", "code", 400_000, 380_000, 9_000),
        row("turn-2", "user"),
        row("turn-2", "code", 40_000, 30_000, 1_500),
        row("turn-2", "finalize", 42_000, 40_000, 800),
        row("turn-2", "terminal"),
      ],
    };
    await refreshSessionUsage();
    expect(usage.textContent).toBe("936k token · 本轮 +84k");
    expect(usage.title).toContain("最近一轮：新输入 12,000 · 缓存输入 70,000 · 输出 2,300。");
    // A turn whose start is in an earlier, unloaded page is not summed as a whole turn.
    actionTimeline.value = { ...(actionTimeline.value as object), groups: [row("turn-2", "code", 40_000, 0, 1)] };
    expect(usage.textContent).toBe("936k token · 本轮 +84k");
    paintUsageTitle();
    expect(usage.textContent).toBe("936k token");
    await setLang("en");
    actionTimeline.value = { root_frame_id: "f-t", has_earlier: false, groups: [row("turn-3", "code", 900, 0, 100)] };
    paintUsageTitle();
    expect(usage.textContent).toBe("936k tokens · +1k this turn");
    // The frame's totals are re-read now and then; the timeline arrives live.
    // The total is never below the turn it contains.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ id: "f-t" }) })),
    );
    await refreshSessionUsage();
    expect(usage.textContent).toBe("1k tokens · +1k this turn");
    expect(usage.classList.contains("hidden")).toBe(false);
  });
});
