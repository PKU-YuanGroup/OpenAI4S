import { describe, expect, it } from "vitest";
import { editorKeywords, mdHighlight, mdKw, ocHighlight } from "./highlight";

const TOK = /class="tok-([^"]+)"/g;

function tokClasses(html: string): string[] {
  const out: string[] = [];
  let m: RegExpExecArray | null;
  while ((m = TOK.exec(html))) out.push(m[1] || "");
  return out;
}

describe("mdHighlight (unified scanner)", () => {
  it("emits only the app.js .tok-* class names", () => {
    const html = mdHighlight(
      'def foo(x):\n    # c\n    return 1\n    s = "hi"',
      "python",
    );
    const classes = new Set(tokClasses(html));
    for (const cls of classes) {
      expect(["com", "str", "num", "kw", "fn"]).toContain(cls);
    }
    expect(classes.has("kw")).toBe(true);
    expect(classes.has("fn")).toBe(true);
    expect(classes.has("num")).toBe(true);
    expect(classes.has("com")).toBe(true);
    expect(classes.has("str")).toBe(true);
  });

  it("unions _OC_KW and MD_KEYWORDS", () => {
    const py = mdKw("python");
    expect(py.has("self")).toBe(true);
    expect(py.has("print")).toBe(true);
    expect(py.has("match")).toBe(true);
    const bash = mdKw("bash");
    expect(bash.has("alias")).toBe(true);
    expect(bash.has("time")).toBe(true);
    expect(bash.has("cd")).toBe(true);
    expect(bash.has("exit")).toBe(true);
  });

  it("is the same function Notebook cells will call", () => {
    expect(ocHighlight).toBe(mdHighlight);
  });

  it("escapes source so a copy-button textContent round-trip stays safe", () => {
    const html = mdHighlight("<script>alert(1)</script>", "python");
    expect(html).not.toMatch(/<script\b/i);
    expect(html).toContain("&lt;script&gt;");
  });

  it("does not tokenize huge blobs", () => {
    const blob = "def x():\n" + "a".repeat(24001);
    const html = mdHighlight(blob, "python");
    expect(html).not.toContain("tok-kw");
    expect(html.startsWith("def") || html.includes("def")).toBe(true);
  });

  it("derives EDKW from the unified table", () => {
    expect(editorKeywords("py")).toContain("def");
    expect(editorKeywords("py")).toContain("self");
    expect(editorKeywords("sh")).toContain("cd");
    expect(editorKeywords("sh")).toContain("alias");
    expect(editorKeywords("js")).toContain("function");
    expect(editorKeywords("ts")).toContain("interface");
  });
});
