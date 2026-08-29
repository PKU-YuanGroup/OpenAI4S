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
});
