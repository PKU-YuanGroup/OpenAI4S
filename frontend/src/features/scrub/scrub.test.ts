import { describe, expect, it } from "vitest";
import { publicModelId, publicText } from "./scrub";

describe("publicText", () => {
  it("redacts Bearer, key-shaped tokens, and query credentials", () => {
    expect(publicText("Bearer abc.def")).toBe("Bearer [redacted]");
    expect(publicText("sk-abcdefghijk")).toBe("[redacted]");
    expect(publicText("https://h/?api_key=secret&x=1")).toBe(
      "https://h/?api_key=[redacted]&x=1",
    );
  });

  it("ellipsizes past the limit", () => {
    expect(publicText("abcdefghij", 4)).toBe("abc…");
    expect(publicText(null)).toBe("");
  });
});

describe("publicModelId", () => {
  it("leaves model ids that merely start with a credential prefix alone", () => {
    for (const id of ["ark-code-latest", "ark-deepseek-v3-250324", "sk-small-2024-08-06", "claude-sonnet-4-5-20250929"]) {
      expect(publicModelId(id)).toBe(id);
    }
    // The generic scrubber is unchanged, and still takes the short test shape.
    expect(publicText("ark-code-latest")).toBe("[redacted]");
    expect(publicText("sk-abcdefghijk")).toBe("[redacted]");
  });

  it("still redacts real key shapes, Bearer tokens and query credentials", () => {
    expect(publicModelId("sk-proj-fake-Q3vT9wXk2LmN8pRz4YbC7dFh1JsA6uEo")).toBe("[redacted]");
    expect(publicModelId("sk-ant-api03-fake-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_aa")).toBe("[redacted]");
    expect(publicModelId("sk-fake-0123456789abcdefghijABCDEFGHIJ")).toBe("[redacted]");
    expect(publicModelId("Bearer abc.def")).toBe("Bearer [redacted]");
    expect(publicModelId("m?api_key=secret")).toBe("m?api_key=[redacted]");
  });

  it("redacts an Ark key, whose UUID body has no long unbroken segment", () => {
    // A real Ark key is `ark-` + a UUID + a short suffix: segments 3/8/4/4/4/12/5,
    // none reaching the 16 characters the segment rule needs.
    for (const key of [
      ["ark-fake", "3f2a9c1e", "7b4d", "4e8a", "9c2f", "1a2b3c4d5e6f", "ab12c"].join("-"),
      ["ark", "3f2a9c1e", "7b4d", "4e8a", "9c2f", "1a2b3c4d5e6f", "fake"].join("-"),
      ["sk", "3F2A9C1E", "7B4D", "4E8A", "9C2F", "1A2B3C4D5E6F", "fake"].join("-"),
    ]) {
      expect(publicModelId(key)).toBe("[redacted]");
      expect(publicModelId(`model ${key} pasted`)).toBe("model [redacted] pasted");
    }
    // A long chunked body with digits, the shape source_secret_scan's ark rule
    // takes, even when no chunk is a UUID's.
    expect(publicModelId("ark-fake-a1b2c3-d4e5f6-a7b8c9-d0e1f2-a3b4c5")).toBe("[redacted]");
  });

  it("trims and caps", () => {
    expect(publicModelId("  gpt-4o  ")).toBe("gpt-4o");
    expect(publicModelId("abcdefghij", 4)).toBe("abc…");
    expect(publicModelId(null)).toBe("");
  });
});
