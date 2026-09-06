import { describe, expect, it } from "vitest";
import { acDetectFrom, edacDetectFrom, edacExt } from "./detect";

describe("composer trigger parsing (@ / # / /)", () => {
  it("detects @ at start of input", () => {
    expect(acDetectFrom("@plot", 5)).toEqual({
      trigger: "@",
      query: "plot",
      start: 0,
    });
  });

  it("detects @ after whitespace", () => {
    const before = "see @res";
    expect(acDetectFrom(before, before.length)).toEqual({
      trigger: "@",
      query: "res",
      start: 4,
    });
  });

  it("detects # sessions and / skills", () => {
    expect(acDetectFrom("go #sess", 8)).toEqual({
      trigger: "#",
      query: "sess",
      start: 3,
    });
    expect(acDetectFrom("run /py-ml", 10)).toEqual({
      trigger: "/",
      query: "py-ml",
      start: 4,
    });
  });

  it("keeps an empty query when the trigger was just typed", () => {
    expect(acDetectFrom("hello @", 7)).toEqual({
      trigger: "@",
      query: "",
      start: 6,
    });
  });

  it("does not fire mid-token (foo@bar) or without a trigger", () => {
    expect(acDetectFrom("foo@bar", 7)).toBeNull();
    expect(acDetectFrom("hello world", 11)).toBeNull();
    expect(acDetectFrom("email a@b.com", 13)).toBeNull();
  });

  it("stops the query at whitespace or another trigger", () => {
    expect(acDetectFrom("@a @b", 5)).toEqual({
      trigger: "@",
      query: "b",
      start: 3,
    });
    expect(acDetectFrom("@file#", 6)).toBeNull();
  });
});

describe("editor identifier detection", () => {
  it("requires at least two ASCII identifier chars", () => {
    expect(edacDetectFrom("d", 1)).toBeNull();
    expect(edacDetectFrom("de", 2)).toEqual({ query: "de", start: 0 });
  });

  it("reads the token under the caret, not a range selection", () => {
    expect(edacDetectFrom("define x", 2, 6)).toBeNull();
    expect(edacDetectFrom("  myVar", 7)).toEqual({ query: "myVar", start: 2 });
  });

  it("does not treat Han as an identifier (IME must not open the popup)", () => {
    expect(edacDetectFrom("变量", 2)).toBeNull();
  });

  it("edacExt reads the last extension, lowercased", () => {
    expect(edacExt("foo.PY")).toBe("py");
    expect(edacExt("a.b.ts")).toBe("ts");
    expect(edacExt("README")).toBe("");
  });
});
