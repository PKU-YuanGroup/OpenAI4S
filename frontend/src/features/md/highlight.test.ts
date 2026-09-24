import { describe, expect, it } from "vitest";
import {
  HIGHLIGHT_MAX_CHARS,
  editorKeywords,
  mdHighlight,
  mdKw,
  ocHighlight,
  resetMdHighlightMemo,
} from "./highlight";

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
    const blob = "def x():\n" + "a".repeat(HIGHLIGHT_MAX_CHARS + 1);
    const html = mdHighlight(blob, "python");
    expect(html).not.toContain("tok-kw");
    expect(html.startsWith("def") || html.includes("def")).toBe(true);
  });

  it("highlights a block past the old 24,000-character cap", () => {
    resetMdHighlightMemo();
    const code = "def fit(x):\n    return x * 2  # scale\n".repeat(800);
    expect(code.length).toBeGreaterThan(24_000);
    const html = mdHighlight(code, "python");
    expect(tokClasses(html)).toEqual(expect.arrayContaining(["kw", "fn", "num", "com"]));
  });

  it("a streamed block that only grew resumes and matches a fresh highlight", () => {
    const samples: Array<[string, string]> = [
      ["python", 'def f(a, b=0x1F):\n    """doc\n    string"""\n    s = "q\\"uote" + \'x\'  # note\n    return g(a) + 1.5e-3j\n'],
      ["javascript", "/* block\n comment */ const n = 12_000; // line\nfunction go(x) { return `t${x}` + 'y'; }\n"],
      ["r", "x <- c(1, 2.5)  # r comment\nplot(x, main = \"title\")\n"],
      ["sql", "SELECT id, 'a''b' FROM t -- trailing\nWHERE n > 10;\n"],
    ];
    for (const [lang, sample] of samples) {
      const code = sample.repeat(3);
      for (const step of [1, 2, 3, 5, 7, 13]) {
        resetMdHighlightMemo();
        for (let end = step; end < code.length + step; end += step) {
          const prefix = code.slice(0, Math.min(end, code.length));
          const streamed = mdHighlight(prefix, lang);
          resetMdHighlightMemo();
          const fresh = mdHighlight(prefix, lang);
          expect(streamed, `${lang} step ${step} at ${prefix.length}`).toBe(fresh);
          // Rebuild the memo the next step extends.
          mdHighlight(prefix, lang);
        }
      }
    }
  });

  it("streams a large block in time linear in its length", () => {
    const code = "for i in range(10):\n    total = total + compute(i, 'x')  # step\n".repeat(2500);
    const onePass = (): number => {
      resetMdHighlightMemo();
      const started = performance.now();
      mdHighlight(code, "python");
      return performance.now() - started;
    };
    onePass();
    const fresh = Math.min(onePass(), onePass(), onePass());
    resetMdHighlightMemo();
    const started = performance.now();
    for (let end = 100; end <= code.length; end += 100) mdHighlight(code.slice(0, end), "python");
    const elapsed = performance.now() - started;
    // Re-tokenizing from the top on each of ~1,600 chunks costs ~800 fresh
    // passes; resuming at the last line costs ~25. The budget is relative to
    // this machine: a fixed 1500 ms failed on a CI runner at 1560 ms.
    expect(elapsed).toBeLessThan(200 * fresh);
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
