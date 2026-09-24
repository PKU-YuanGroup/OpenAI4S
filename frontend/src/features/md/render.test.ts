import { describe, expect, it } from "vitest";
import { esc } from "./esc";
import { renderMd, mdInline } from "./render";

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
