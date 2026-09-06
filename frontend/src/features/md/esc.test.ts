import { describe, expect, it } from "vitest";
import { esc, escQuote } from "./esc";

describe("esc (app.js:5 + F-08 quote escape)", () => {
  it("keeps the old &<> replacements", () => {
    expect(esc("a<b>c")).toBe("a&lt;b&gt;c");
    expect(esc("a&b")).toBe("a&amp;b");
    expect(esc("<script>")).toBe("&lt;script&gt;");
    expect(esc("")).toBe("");
    expect(esc(null)).toBe("");
    expect(esc(undefined)).toBe("");
  });

  it("escapes quotes after & so &quot; is not double-encoded", () => {
    expect(esc('"')).toBe("&quot;");
    expect(esc('say "hi"')).toBe("say &quot;hi&quot;");
    expect(esc("&\"")).toBe("&amp;&quot;");
    expect(esc('a&b<c>d"e')).toBe("a&amp;b&lt;c&gt;d&quot;e");
  });

  it("escQuote only touches quotes (attribute discipline)", () => {
    expect(escQuote('x"y')).toBe("x&quot;y");
    expect(escQuote("already &quot; encoded")).toBe("already &quot; encoded");
    expect(escQuote("<tag>")).toBe("<tag>");
  });
});
