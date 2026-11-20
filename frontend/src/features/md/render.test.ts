import { describe, expect, it } from "vitest";
import { esc } from "./esc";
import { CHAT_MD, renderMd, mdInline } from "./render";

/**
 * The five hostile markdown samples from tests/browser_smoke.mjs
 * (browser data boundary; currently ~295-300). A Vitest mirror: each must
 * not become an executable path (raw <script>/<img>/<svg> tag, event-handler
 * attribute, or javascript: href).
 */
const XSS_ATTACKS = [
  "before <script>window.__xssProbe()<\/script> after",
  "text <img src=x onerror=\"window.__xssProbe()\"> text",
  "<div onclick=\"window.__xssProbe()\">x</div>",
  "[link](javascript:window.__xssProbe())",
  "<svg onload=\"window.__xssProbe()\"></svg>",
] as const;

function rawTags(html: string): string[] {
  const names: string[] = [];
  const re = /<([A-Za-z][\w:-]*)\b/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html))) names.push((m[1] || "").toLowerCase());
  return names;
}

function executablePaths(html: string): string[] {
  const found: string[] = [];
  const tags = rawTags(html);
  if (tags.includes("script")) found.push("script");
  if (tags.includes("img")) found.push("img");
  if (tags.includes("svg")) found.push("svg");
  if (/<[^>]*\sonerror\s*=/i.test(html)) found.push("onerror");
  if (/<[^>]*\sonclick\s*=/i.test(html)) found.push("onclick");
  if (/<[^>]*\sonload\s*=/i.test(html)) found.push("onload");
  if (/<a\b[^>]*href\s*=\s*(["']?)javascript:/i.test(html)) found.push("javascript:");
  return found;
}

describe("renderMd XSS mirror (browser_smoke attacks)", () => {
  it("turns each of the 5 samples into non-executable markup", () => {
    for (const md of XSS_ATTACKS) {
      const html = renderMd(md);
      expect(executablePaths(html), md).toEqual([]);
      expect(rawTags(html), md).toEqual(["p"]);
    }
  });

  it("escapes the script sample rather than dropping it", () => {
    const html = renderMd(XSS_ATTACKS[0]);
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toMatch(/<script\b/i);
  });

  it("does not promote javascript: to an href", () => {
    const html = renderMd(XSS_ATTACKS[3]);
    expect(html).not.toMatch(/href\s*=/i);
    expect(html).toContain("javascript:");
  });
});

describe("renderMd esc-then-markup chain", () => {
  it("renders bold/links the same way as the old no-quote cases", () => {
    expect(renderMd("**bold**")).toBe("<p><strong>bold</strong></p>");
    expect(renderMd("[x](https://ex.com)")).toBe(
      '<p><a href="https://ex.com" target="_blank" rel="noopener">x</a></p>',
    );
    expect(renderMd("[x](mailto:a@b.c)")).toContain('href="mailto:a@b.c"');
    expect(renderMd("[x](/path)")).toContain('href="/path"');
    expect(renderMd("[x](#frag)")).toContain('href="#frag"');
  });

  it("rewrites a stored 0.2.0 completion link onto the versioned Artifact reader", () => {
    // 0.2.0 wrote default completion links as `/api/artifacts/<id>`; the
    // contract-v1 gateway answers that path with a 404 and has no alias, so a
    // reopened session's "Artifacts:" list was dead. Exactly the one-segment
    // form is rewritten.
    expect(renderMd("- [stats.json](/api/artifacts/a-c7c20745a417)")).toBe(
      '<ul><li><a href="/api/v1/artifacts/a-c7c20745a417" target="_blank" rel="noopener">stats.json</a></li></ul>',
    );
    expect(mdInline("[x](/api/artifacts/artifact%2Flegacy)")).toContain(
      'href="/api/v1/artifacts/artifact%2Flegacy"',
    );
    // Current links and every other shape are left exactly as written.
    expect(mdInline("[x](/api/v1/artifacts/a-1)")).toContain('href="/api/v1/artifacts/a-1"');
    expect(mdInline("[x](/api/v1/artifacts/versions/v-1)")).toContain(
      'href="/api/v1/artifacts/versions/v-1"',
    );
    expect(mdInline("[x](/api/artifacts/a-1/versions)")).toContain(
      'href="/api/artifacts/a-1/versions"',
    );
    expect(mdInline("[x](/api/artifacts/a-1?x=1)")).toContain('href="/api/artifacts/a-1?x=1"');
    expect(mdInline("[x](/api/artifacts/..)")).toContain('href="/api/artifacts/.."');
    expect(mdInline("[x](https://ex.com/api/artifacts/a-1)")).toContain(
      'href="https://ex.com/api/artifacts/a-1"',
    );
    expect(mdInline("[x](/static/api/artifacts/a-1)")).toContain(
      'href="/static/api/artifacts/a-1"',
    );
  });

  it("keeps the scheme whitelist (https / http / mailto / / / # only)", () => {
    expect(mdInline("[x](javascript:alert(1))")).toBe("[x](javascript:alert(1))");
    expect(mdInline("[x](data:text/html,hi)")).toBe("[x](data:text/html,hi)");
    expect(mdInline("[x](https://ok)")).toContain("<a href=");
  });

  it("escQuote still binds alt/href after whole-string esc", () => {
    const html = mdInline('![say "hi"](https://ex.com/a.png)');
    expect(html).toBe(
      '<img alt="say &quot;hi&quot;" src="https://ex.com/a.png">',
    );
  });

  it("escapes quotes in body text without breaking old &<> cases", () => {
    expect(renderMd('say "hi"')).toBe("<p>say &quot;hi&quot;</p>");
    expect(renderMd("a<b>")).toBe("<p>a&lt;b&gt;</p>");
    expect(esc('a&b<c>"')).toBe("a&amp;b&lt;c&gt;&quot;");
  });

  it("keeps an unclosed fence as code, not a heading", () => {
    const html = renderMd("```python\n# comment");
    expect(html).toContain("codeblock");
    expect(html).toContain("tok-com");
    expect(html).not.toContain("<h1>");
  });

  it("does not let inline-code contents become emphasis", () => {
    expect(mdInline("`**not bold**`")).toBe("<code>**not bold**</code>");
  });

  it("only allows raster data: images, not svg", () => {
    expect(mdInline("![x](data:image/png;base64,aaa)")).toContain("<img ");
    expect(mdInline("![x](data:image/svg+xml;base64,aaa)")).not.toContain("<img ");
  });

  it("wraps markdown tables in an overflow-x container", () => {
    const html = renderMd("| a | b |\n| --- | --- |\n| 1 | 2 |");
    expect(html).toContain('<div class="md-table-wrap"><table>');
    expect(html).toContain("</table></div>");
  });
});

describe("renderMd on pathological input", () => {
  it("renders 12,000 nested blockquotes instead of overflowing the stack", () => {
    const html = renderMd(">".repeat(12000) + " the bottom");
    expect(html).toContain("the bottom");
    // Nesting stops at a readable depth; the rest renders as text.
    expect(html.match(/<blockquote>/g)).toHaveLength(32);
    expect(renderMd("> a\n>> b\n> c")).toBe(
      "<blockquote><p>a</p><blockquote><p>b</p></blockquote><p>c</p></blockquote>",
    );
  });

  it("renders 12,000 ever-deeper list items instead of overflowing the stack", () => {
    const lines = Array.from({ length: 12000 }, (_, i) => " ".repeat(i) + "- item " + i);
    const html = renderMd(lines.join("\n"));
    expect(html).toContain("item 11999");
    expect(renderMd("- a\n  - b\n- c")).toBe("<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>");
  });

  it("does not rescan a long run of [ for every [ (it was quadratic)", () => {
    const run = "[".repeat(60000);
    const started = performance.now();
    expect(mdInline(run)).toBe(run);
    expect(mdInline("![".repeat(30000))).toBe("![".repeat(30000));
    // ~1ms linear; the quadratic scan took seconds at this size.
    expect(performance.now() - started).toBeLessThan(400);
  });

  it("still links bracket text, and an unbalanced [ stays text", () => {
    expect(mdInline("[a [b](https://ex.com/x)")).toBe(
      '[a <a href="https://ex.com/x" target="_blank" rel="noopener">b</a>',
    );
    expect(mdInline("![plot](https://ex.com/p.png)")).toBe('<img alt="plot" src="https://ex.com/p.png">');
  });
});


describe("notebook-only resource-free rendering (F7)", () => {
  it("turns every image into its escaped alt text and every link into text (url)", () => {
    const md = [
      "![plot](https://example.com/x.png)",
      "![data](data:image/png;base64,QUJDREVGRw==)",
      "[paper](https://example.com/paper)",
      "[bad](javascript:alert(1))",
      "![unsafe <img src=x>](https://example.com/y.png)",
    ].join("\n\n");
    const html = renderMd(md, { resources: "none" });
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<a ");
    expect(html).not.toContain("base64");
    expect(html).toContain("plot");
    expect(html).toContain("paper (https://example.com/paper)");
    expect(html).toContain("bad (javascript:alert(1))");
    // The alt text is escaped, not emitted as markup.
    expect(html).not.toContain("<img src=x>");
    expect(html).toContain("&lt;img src=x&gt;");
  });

  it("applies inside every nested markdown path", () => {
    const md = [
      "# [title](https://example.com)",
      "",
      "> ![quote](data:image/png;base64,QUJD)",
      "",
      "- [item](https://example.com)",
      "",
      "| a |",
      "|---|",
      "| [cell](https://example.com) |",
    ].join("\n");
    const html = renderMd(md, { resources: "none" });
    expect(html).not.toContain("<a ");
    expect(html).not.toContain("<img");
    expect(html).toContain("title (https://example.com)");
    expect(html).toContain("item (https://example.com)");
    expect(html).toContain("cell (https://example.com)");
  });

  it("leaves default rendering for every other surface unchanged", () => {
    expect(renderMd("![a](https://x/y.png)")).toBe(
      '<p><img alt="a" src="https://x/y.png"></p>',
    );
    expect(renderMd("[x](https://ex.com)")).toContain('href="https://ex.com"');
    expect(renderMd("[x](https://ex.com)", { resources: "default" })).toBe(
      renderMd("[x](https://ex.com)"),
    );
  });
});

describe("underscores inside non-ASCII words (ART-8)", () => {
  it("keeps a CJK file name's underscores instead of reading them as emphasis", () => {
    const html = mdInline("[project-inputs/队列_临床_与_标志物.csv](/api/v1/artifacts/a-1)");
    expect(html).toContain("队列_临床_与_标志物.csv");
    expect(html).not.toContain("<em>");
    expect(mdInline("Ü_ber_x")).not.toContain("<em>");
  });

  it("still reads standalone underscores as emphasis", () => {
    expect(mdInline("a _word_ here")).toContain("<em>word</em>");
    expect(mdInline("强调 _这个_ 词")).toContain("<em>这个</em>");
    expect(mdInline("__bold__ text")).toContain("<strong>bold</strong>");
    expect(mdInline("snake_case_name")).not.toContain("<em>");
  });
});

describe("Markdown Artifact math (ART-2)", () => {
  const on = { math: true };

  it("leaves math as text on every surface that did not opt in", () => {
    expect(renderMd("$x^2$ and $$y$$")).toBe("<p>$x^2$ and $$y$$</p>");
  });

  it("tokenizes inline math before escaping and emphasis touch it", () => {
    const html = renderMd("Let $a_1 < b_2$ and *c*.", on);
    expect(html).toBe(
      '<p>Let <span class="md-math" data-display="0">a_1 &lt; b_2</span> and <em>c</em>.</p>',
    );
    expect(renderMd("\\(x \\cdot y\\)", on)).toContain(
      '<span class="md-math" data-display="0">x \\cdot y</span>',
    );
  });

  it("keeps currency, escaped dollars and code spans as text", () => {
    expect(renderMd("It costs $5 and $10 today.", on)).toBe("<p>It costs $5 and $10 today.</p>");
    expect(renderMd("a \\$ sign", on)).toBe("<p>a $ sign</p>");
    expect(renderMd("`$x$` stays code", on)).toBe("<p><code>$x$</code> stays code</p>");
    expect(renderMd("$ spaced $ is not math", on)).not.toContain("md-math");
  });

  it("renders display blocks, single-line and multi-line, and never a div inside a paragraph", () => {
    const block = renderMd(["Before", "$$", "\\frac{a}{b} & c", "$$", "After"].join("\n"), on);
    expect(block).toBe(
      '<p>Before</p><div class="md-math md-math-block" data-display="1">\\frac{a}{b} &amp; c</div><p>After</p>',
    );
    expect(renderMd("$$E = mc^2$$", on)).toBe(
      '<div class="md-math md-math-block" data-display="1">E = mc^2</div>',
    );
    expect(renderMd("\\[\na^2+b^2\n\\]", on)).toContain('data-display="1">a^2+b^2</div>');
    const inline = renderMd("where $$x$$ holds", on);
    expect(inline).toContain('<span class="md-math" data-display="1">x</span>');
    expect(inline).not.toContain("<div");
  });

  it("does not let an unclosed block swallow the document", () => {
    const html = renderMd(["$$", "a + b", "", "# Next", "text"].join("\n"), on);
    expect(html).not.toContain("md-math");
    expect(html).toContain("<h1>Next</h1>");
  });

  it("bounds the scan on a long run of dollars", () => {
    const started = Date.now();
    renderMd("$a ".repeat(20000), on);
    expect(Date.now() - started).toBeLessThan(2000);
  });
});

describe("Markdown Artifact relative images (ART-2)", () => {
  const on = { relativeImages: true };

  it("turns a relative image into an unresolved placeholder", () => {
    expect(renderMd("![growth](growth.png)", on)).toBe(
      '<p><img class="md-relimg" alt="growth" data-md-src="growth.png"></p>',
    );
    expect(renderMd("![f](figures/a%20b.png)", on)).toContain('data-md-src="figures/a%20b.png"');
  });

  it("never turns a scheme, an absolute path or a fragment into a placeholder", () => {
    for (const src of ["javascript:alert(1)", "data:text/html,x", "/api/v1/x.png", "#top", "//evil/x.png"]) {
      expect(renderMd(`![x](${src})`, on)).not.toContain("<img");
    }
    expect(renderMd('![a"b](x.png)', on)).toContain('alt="a&quot;b"');
  });

  it("is off by default and in the no-resources mode", () => {
    expect(renderMd("![g](growth.png)")).toBe("<p>![g](growth.png)</p>");
    expect(renderMd("![g](growth.png)", { resources: "none", relativeImages: true })).toBe("<p>g</p>");
  });
});

describe("a completion's metrics in the conversation (M-60)", () => {
  const completion = [
    "The hazard ratio for the new drug is below 1.",
    "",
    "Metrics:",
    "- n raw: 181",
    "- hr new drug ci lower: 0.505",
    "- peptide avg MW (Da): 1019.14",
    "",
    "Completed work:",
    "- Fitted the Cox model",
  ].join("\n");

  it("folds the list under a summary that counts it, values exact", () => {
    const html = renderMd(completion, CHAT_MD);
    expect(html).toContain(
      '<details class="md-fold md-metrics"><summary>Metrics · 3</summary><ul><li>n raw: 181</li><li>hr new drug ci lower: 0.505</li><li>peptide avg MW (Da): 1019.14</li></ul></details>',
    );
    // Only that block: the rest of the answer reads as before.
    expect(html).toContain("<p>Completed work:</p><ul><li>Fitted the Cox model</li></ul>");
    expect(renderMd("指标：\n- 样本数: 181", CHAT_MD)).toBe(
      '<details class="md-fold md-metrics"><summary>指标 · 1</summary><ul><li>样本数: 181</li></ul></details>',
    );
  });

  it("is off outside the conversation and for a heading with no list", () => {
    expect(renderMd(completion)).toContain("<p>Metrics:</p><ul>");
    expect(renderMd("Metrics:\n\nnone recorded", CHAT_MD)).toBe("<p>Metrics:</p><p>none recorded</p>");
    expect(renderMd("Metrics: see below\n- a: 1", CHAT_MD)).not.toContain("<details");
  });
});
