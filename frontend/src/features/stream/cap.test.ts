import { describe, expect, it } from "vitest";
import {
  LIVE_OUTPUT_CHAR_CAP,
  LIVE_OUTPUT_TRUNCATION,
  appendLiveOutput,
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
