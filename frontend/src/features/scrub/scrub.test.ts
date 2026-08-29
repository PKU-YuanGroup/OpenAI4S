import { describe, expect, it } from "vitest";
import { publicText } from "./scrub";

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
