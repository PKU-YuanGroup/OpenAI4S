import { describe, expect, it } from "vitest";
import {
  LIVE_OUTPUT_CHAR_CAP,
  LIVE_OUTPUT_TRUNCATION,
  appendLiveOutput,
  liveOutputIncrement,
} from "./cap";

describe("appendLiveOutput", () => {
  it("concatenates under the cap", () => {
    expect(appendLiveOutput("ab", "cd")).toBe("abcd");
    expect(appendLiveOutput("", "x")).toBe("x");
    expect(appendLiveOutput(null, "x")).toBe("x");
  });

  it("truncates at 1MB and is idempotent afterwards", () => {
    const big = "x".repeat(LIVE_OUTPUT_CHAR_CAP);
    const once = appendLiveOutput("", big + "more");
    expect(once.length).toBe(LIVE_OUTPUT_CHAR_CAP + LIVE_OUTPUT_TRUNCATION.length);
    expect(once.endsWith(LIVE_OUTPUT_TRUNCATION)).toBe(true);
    expect(once.slice(0, LIVE_OUTPUT_CHAR_CAP)).toBe(big);
    expect(appendLiveOutput(once, "again")).toBe(once);
    expect(appendLiveOutput(once, "zzz")).toBe(once);
    const already = appendLiveOutput(big, "y");
    expect(appendLiveOutput(already, "z")).toBe(already);
  });
});

describe("liveOutputIncrement", () => {
  it("builds exactly what appendLiveOutput builds, from the length alone", () => {
    const chunks = ["a".repeat(400000), "", "b".repeat(400000), "c".repeat(300000), "d", "e"];
    let whole = "";
    let built = "";
    let truncated = false;
    for (const chunk of chunks) {
      whole = appendLiveOutput(whole, chunk);
      const step = liveOutputIncrement(built.length, truncated, chunk);
      built += step.added;
      truncated = step.truncated;
      expect(built).toBe(whole);
      expect(truncated).toBe(whole.includes(LIVE_OUTPUT_TRUNCATION));
    }
    expect(built.length).toBe(LIVE_OUTPUT_CHAR_CAP + LIVE_OUTPUT_TRUNCATION.length);
    // Exactly at the cap, the next chunk (even an empty one) adds the marker.
    expect(liveOutputIncrement(LIVE_OUTPUT_CHAR_CAP, false, "")).toEqual({
      added: LIVE_OUTPUT_TRUNCATION,
      truncated: true,
    });
    expect(appendLiveOutput("x".repeat(LIVE_OUTPUT_CHAR_CAP), "")).toBe(
      "x".repeat(LIVE_OUTPUT_CHAR_CAP) + LIVE_OUTPUT_TRUNCATION,
    );
  });
});
