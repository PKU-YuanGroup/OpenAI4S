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
import { formatElapsed, formatTokens, paintTurnClock, refreshSessionUsage } from "./progress";

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
    expect(usage.textContent).toBe("12k tokens");
    expect(usage.title).toContain("10,123");
    expect(usage.title).toContain("2,201");

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ id: "f-u" }) })),
    );
    await refreshSessionUsage();
    expect(usage.classList.contains("hidden")).toBe(true);
  });
});
