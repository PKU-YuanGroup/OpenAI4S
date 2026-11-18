/**
 * Self-contained markdown renderer. Port of app.js:12709-12876.
 *
 * Invariants (do not "modernize"):
 *  1. STREAMING SAFETY — an unclosed fence still renders as code, never as
 *     headings / a leaked ```lang line.
 *  2. Inline code spans are tokenized out first so emphasis/link processing
 *     never touches their contents.
 *  3. Whole-string esc() runs before any markup replacement.
 *  4. Attribute captures go through escQuote.
 *  5. Link href scheme whitelist is exactly `(https?:|mailto:|/|#)`.
 *  6. No marked, no DOMPurify.
 *
 * mdCodeBlock's copy chrome reads the real dictionary; `copy.ts` handles its
 * clicks (one delegated listener, like app.js).
 */

import { t } from "../../i18n/runtime";
import { esc, escQuote } from "./esc";
import { mdHighlight } from "./highlight";

const COPY_ICON =
  '<svg class="ic-svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>';

export function mdCodeBlock(code: string, lang: string): string {
  const label = (lang || "").trim();
  return (
    '<div class="codeblock">' +
    '<div class="cb-head"><span class="cb-lang">' +
    esc(label || "text") +
    "</span>" +
    '<button class="cb-copy" type="button" title="' +
    esc(t("code.copy.title")) +
    '">' +
    COPY_ICON +
    '<span class="cb-copy-t">' +
    esc(t("msgAction.copy")) +
    "</span></button></div>" +
    "<pre><code>" +
    mdHighlight(code, label) +
    "</code></pre></div>"
  );
}

/**
 * 0.2.0 stored every default completion link as `/api/artifacts/<id>`, a path
 * the contract-v1 gateway answers with 404 (it deliberately has no
 * un-versioned alias), so a reopened session's "Artifacts:" list was dead.
 * Exactly that one-segment form is rewritten onto the versioned reader; any
 * other href -- deeper paths, a query or fragment, dot segments -- is left as
 * written.
 */
const LEGACY_ARTIFACT_HREF = /^\/api\/artifacts\/([^/?#]+)$/;
const ARTIFACT_READER = "/api/v1/artifacts/";

function mdHref(href: string): string {
  const m = LEGACY_ARTIFACT_HREF.exec(href);
  const segment = m ? m[1] || "" : "";
  if (!segment || segment === "." || segment === "..") return href;
  return ARTIFACT_READER + segment;
}

const MDC0 = String.fromCharCode(0xe000);
const MDC1 = String.fromCharCode(0xe001);
const mdCodeRestore = new RegExp(MDC0 + "(\\d+)" + MDC1, "g");
const MDM0 = String.fromCharCode(0xe002);
const MDM1 = String.fromCharCode(0xe003);
const mdMathRestore = new RegExp(MDM0 + "(\\d+)" + MDM1, "g");

/** Rendering options. Defaults keep every other markdown surface unchanged. */
export type MdRenderOptions = {
  /**
   * `"none"` is the notebook-only mode (F7): the rendered markdown belongs to a
   * file that must not make the preview load or execute anything. No `<img>`
   * and no `<a>` is produced anywhere (including nested quotes/lists/tables):
   * an image becomes its escaped alt text only, and a link becomes plain
   * `text (url)`. `"default"` / omitted keeps today's behaviour byte for byte.
   */
  resources?: "default" | "none";
  /**
   * TeX math (`$…$`, `$$…$$`, `\(…\)`, `\[…\]`) becomes an inert
   * `.md-math` element holding the escaped source, which `typesetMath`
   * replaces later. Off by default: only Markdown Artifacts opt in.
   */
  math?: boolean;
  /**
   * A relative image (`![a](growth.png)`) becomes `<img data-md-src>` with no
   * `src`, for the Artifact viewer to resolve against sibling Artifacts.
   * Ignored with `resources: "none"`.
   */
  relativeImages?: boolean;
};

type MdMath = { tex: string; display: boolean };

/** How far an inline `$` looks for its closing `$`: bounds the scan on long `$` runs. */
const INLINE_MATH_WINDOW = 1000;

function isSpace(ch: string | undefined): boolean {
  return ch === undefined || /\s/.test(ch);
}

/**
 * Tokenize TeX math out of one inline run, before any escaping or emphasis can
 * touch its `_`, `*`, `<` or `&`. Pandoc's rule decides a single `$`: the
 * opener is followed by a non-space, the closer is preceded by a non-space and
 * not followed by a digit, so "$5 and $10" stays text. `\$` is a literal
 * dollar. A delimiter with no partner is left exactly as written.
 */
function tokenizeMath(t: string, out: MdMath[]): string {
  let result = "";
  let noDisplayClose = false;
  let noParenClose = false;
  let noBracketClose = false;
  const push = (tex: string, display: boolean): string => {
    out.push({ tex, display });
    return MDM0 + (out.length - 1) + MDM1;
  };
  let i = 0;
  while (i < t.length) {
    const ch = t.charAt(i);
    const next = t.charAt(i + 1);
    if (ch === "\\" && next === "$") {
      result += "$";
      i += 2;
      continue;
    }
    if (ch === "\\" && (next === "(" || next === "[")) {
      const display = next === "[";
      const closer = display ? "\\]" : "\\)";
      const known = display ? noBracketClose : noParenClose;
      const end = known ? -1 : t.indexOf(closer, i + 2);
      if (end < 0) {
        if (display) noBracketClose = true;
        else noParenClose = true;
      } else if (end > i + 2) {
        result += push(t.slice(i + 2, end), display);
        i = end + 2;
        continue;
      }
    }
    if (ch === "$" && next === "$") {
      const end = noDisplayClose ? -1 : t.indexOf("$$", i + 2);
      if (end < 0) noDisplayClose = true;
      else if (end > i + 2) {
        result += push(t.slice(i + 2, end), true);
        i = end + 2;
        continue;
      }
      result += "$$";
      i += 2;
      continue;
    }
    if (ch === "$" && !isSpace(next)) {
      const limit = Math.min(t.length, i + 1 + INLINE_MATH_WINDOW);
      let close = -1;
      for (let j = i + 1; j < limit; j++) {
        const c = t.charAt(j);
        if (c === "\\") {
          j++;
          continue;
        }
        if (c === "$" && !isSpace(t.charAt(j - 1)) && !/\d/.test(t.charAt(j + 1))) {
          close = j;
          break;
        }
      }
      if (close > i + 1) {
        result += push(t.slice(i + 1, close), false);
        i = close + 1;
        continue;
      }
    }
    result += ch;
    i++;
  }
  return result;
}

/**
 * The inert element `typesetMath` turns into rendered math. Display math found
 * inside a paragraph stays a `<span>`: a `<div>` there would close the `<p>`.
 */
function mathElement(tex: string, display: boolean, block = false): string {
  const source = esc(tex.trim());
  if (block) return '<div class="md-math md-math-block" data-display="1">' + source + "</div>";
  return '<span class="md-math" data-display="' + (display ? "1" : "0") + '">' + source + "</span>";
}

/** A link/image target with no scheme, no leading `/` and no fragment: a sibling file. */
function isRelativeTarget(src: string): boolean {
  return !!src && !/^[a-z][a-z0-9+.-]*:/i.test(src) && !/^[/#?\\]/.test(src);
}

export function mdInline(t: string | null | undefined, options: MdRenderOptions = {}): string {
  t = String(t == null ? "" : t);
  const codes: string[] = [];
  t = t.replace(/`([^`]+)`/g, (_m, c: string) => {
    codes.push(c);
    return MDC0 + (codes.length - 1) + MDC1;
  });
  const maths: MdMath[] = [];
  if (options.math) t = tokenizeMath(t, maths);
  t = esc(t);
  // esc() now also escapes quotes (F-08). Capture groups interpolated into a
  // double-quoted HTML attribute still go through escQuote so an alt/href/src
  // value cannot close the attribute even if a future edit reorders the chain.
  // Bracket text excludes `[` as well as `]`: with `[^\]]` every `[` of a
  // long run rescanned the rest of it looking for a `]` (quadratic: 20,000
  // `[` took ~170ms per render). An unbalanced `[` is not link text anyway.
  if (options.resources === "none") {
    // F7: an image is only its escaped alt text; a link is plain text plus the
    // URL in parentheses. Nothing here can become an element or an attribute.
    t = t.replace(/!\[([^[\]]*)\]\([^)]*\)/g, (_m, alt: string) => alt);
    t = t.replace(
      /\[([^[\]]+)\]\(([^)]*)\)/g,
      (_m, text: string, href: string) => (href ? text + " (" + href + ")" : text),
    );
  } else {
    t = t.replace(
      /!\[([^[\]]*)\]\((data:image\/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+)\)/g,
      (_m, alt: string, src: string) =>
        '<img alt="' + escQuote(alt) + '" src="' + src + '">',
    );
    t = t.replace(
      /!\[([^[\]]*)\]\((https?:\/\/[^\s)]+)\)/g,
      (_m, alt: string, src: string) =>
        '<img alt="' + escQuote(alt) + '" src="' + escQuote(src) + '">',
    );
    if (options.relativeImages) {
      // No `src`: the viewer resolves the path against sibling Artifacts and
      // loads the bytes through its bounded read. `src` here is already
      // escaped, so the attribute decodes back to exactly what was written.
      t = t.replace(/!\[([^[\]]*)\]\(([^\s)]+)\)/g, (m: string, alt: string, src: string) =>
        isRelativeTarget(src)
          ? '<img class="md-relimg" alt="' + escQuote(alt) + '" data-md-src="' + escQuote(src) + '">'
          : m,
      );
    }
    t = t.replace(
      /\[([^[\]]+)\]\(((?:https?:|mailto:|\/|#)[^\s)]+)\)/g,
      (_m, text: string, href: string) =>
        '<a href="' + escQuote(mdHref(href)) + '" target="_blank" rel="noopener">' + text + "</a>",
    );
  }
  t = t.replace(/\*\*\*([^*]+?)\*\*\*/g, "<strong><em>$1</em></strong>");
  t = t.replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>");
  // ART-8: `\w` is ASCII-only, so an underscore between CJK letters
  // ("队列_临床_标志物.csv") opened emphasis and the underscores vanished.
  // Intraword means any Unicode letter or digit, as in CommonMark.
  t = t.replace(/(^|[^\p{L}\p{N}_*])__([^_]+?)__(?![\p{L}\p{N}_])/gu, "$1<strong>$2</strong>");
  t = t.replace(/(^|[^*])\*([^*\n]+?)\*/g, "$1<em>$2</em>");
  t = t.replace(/(^|[^\p{L}\p{N}_])_([^_\n]+?)_(?![\p{L}\p{N}_])/gu, "$1<em>$2</em>");
  t = t.replace(/~~([^~]+?)~~/g, "<del>$1</del>");
  t = t.replace(mdCodeRestore, (_m, k: string) => {
    const idx = +k;
    return "<code>" + esc(codes[idx]) + "</code>";
  });
  if (maths.length) {
    t = t.replace(mdMathRestore, (m: string, k: string) => {
      const math = maths[+k];
      return math ? mathElement(math.tex, math.display) : m;
    });
  }
  return t;
}

/**
 * A display-math block starting on `lines[start]`: `$$` or `\[` opens it and
 * the matching closer ends a line (possibly the same one). Null when the
 * line opens nothing or the block never closes -- then it stays text.
 */
function mathBlock(
  lines: string[],
  start: number,
  n: number,
): { tex: string; next: number } | null {
  const first = (lines[start] || "").trim();
  const opener = first.startsWith("$$") ? "$$" : first.startsWith("\\[") ? "\\[" : "";
  if (!opener) return null;
  const closer = opener === "$$" ? "$$" : "\\]";
  const rest = first.slice(2);
  // One line: `$$ x $$`. A closer inside the line followed by more text is
  // inline display math in a paragraph, not a block.
  if (rest.endsWith(closer) && rest.length >= closer.length) {
    const body = rest.slice(0, rest.length - closer.length);
    if (body.indexOf(closer) !== -1) return null;
    return body.trim() ? { tex: body, next: start + 1 } : null;
  }
  if (rest.indexOf(closer) !== -1) return null;
  const body = [rest];
  for (let i = start + 1; i < n; i++) {
    const line = (lines[i] || "").trim();
    if (line.endsWith(closer)) {
      body.push(line.slice(0, line.length - closer.length));
      const tex = body.join("\n");
      return tex.trim() ? { tex, next: i + 1 } : null;
    }
    // A blank line ends a paragraph, and an unclosed block must not swallow
    // the rest of the document.
    if (!line) return null;
    body.push(lines[i] || "");
  }
  return null;
}

type MdListItem = { indent: number; ordered: boolean; text: string };
type MdListCursor = { v: number };

/**
 * Nesting past this renders flat. Both nestings recurse once per level, and
 * a pathological answer (12,000 `>`, or 12,000 ever-deeper list items)
 * overflowed the stack and took the whole history render down with it.
 */
const MAX_NESTING = 32;

function mdBuildList(
  items: MdListItem[],
  cur: MdListCursor,
  depth: number,
  options: MdRenderOptions,
): string {
  const start = items[cur.v];
  if (!start) return "<ul></ul>";
  const ordered = start.ordered;
  const indent = start.indent;
  const nests = depth < MAX_NESTING;
  let html = "<" + (ordered ? "ol" : "ul") + ">";
  while (cur.v < items.length) {
    const item = items[cur.v];
    if (!item || item.indent < indent) break;
    if (item.indent > indent && nests) break;
    const text = item.text;
    cur.v++;
    let nested = "";
    const next = items[cur.v];
    if (next && next.indent > indent && nests) nested = mdBuildList(items, cur, depth + 1, options);
    html += "<li>" + mdInline(text, options) + nested + "</li>";
  }
  return html + "</" + (ordered ? "ol" : "ul") + ">";
}

function mdList(
  lines: string[],
  start: number,
  n: number,
  options: MdRenderOptions,
): { html: string; next: number } {
  const itemRe = /^(\s*)([-*+]|\d+[.)])[ \t]+(.*)$/;
  const items: MdListItem[] = [];
  let i = start;
  while (i < n) {
    const line = lines[i] ?? "";
    const m = line.match(itemRe);
    if (m) {
      items.push({
        indent: (m[1] || "").replace(/\t/g, "  ").length,
        ordered: /\d/.test(m[2] || ""),
        text: m[3] || "",
      });
      i++;
    } else if (!line.trim()) {
      let k = i + 1;
      while (k < n && !(lines[k] || "").trim()) k++;
      if (k < n && itemRe.test(lines[k] || "")) {
        i = k;
        continue;
      }
      break;
    } else if (/^\s+\S/.test(line) && items.length) {
      const last = items[items.length - 1];
      if (last) last.text += " " + line.trim();
      i++;
    } else break;
  }
  return { html: mdBuildList(items, { v: 0 }, 0, options), next: i };
}

export function renderMd(src: string | null | undefined, options: MdRenderOptions = {}): string {
  const lines = String(src == null ? "" : src)
    .replace(/\r\n?/g, "\n")
    .split("\n");
  return renderLines(lines, 0, options);
}

function renderLines(lines: string[], quoteDepth: number, options: MdRenderOptions): string {
  const n = lines.length;
  let i = 0;
  let html = "";
  const fenceRe = /^(\s*)(`{3,}|~{3,})[ \t]*([\w+#.\-]*)[ \t]*$/;
  const listRe = /^(\s*)([-*+]|\d+[.)])[ \t]+/;
  const hrRe = /^\s*([-*_])[ \t]*(?:\1[ \t]*){2,}$/;
  // Table delimiter row, matched cell-by-cell so there is no nested-quantifier
  // regex to catastrophically backtrack (ReDoS-safe). A cell is `:?-+:?` padded.
  const cellDelimRe = /^[ \t]*:?-+:?[ \t]*$/;
  const isDelimRow = (s: string): boolean => {
    let tr = s.trim();
    if (tr.indexOf("-") === -1) return false;
    if (tr.charAt(0) === "|") tr = tr.slice(1);
    if (tr.charAt(tr.length - 1) === "|") tr = tr.slice(0, -1);
    const parts = tr.split("|");
    for (let j = 0; j < parts.length; j++) {
      if (!cellDelimRe.test(parts[j] || "")) return false;
    }
    return true;
  };
  const looksTable = (idx: number): boolean => {
    const row = lines[idx];
    const next = lines[idx + 1];
    return (
      row != null &&
      row.indexOf("|") !== -1 &&
      idx + 1 < n &&
      next != null &&
      isDelimRow(next)
    );
  };
  while (i < n) {
    const line = lines[i] ?? "";
    const fm = line.match(fenceRe);
    if (fm) {
      const marker = fm[2] || "";
      const fchar = marker.charAt(0);
      const flen = marker.length;
      const lang = fm[3] || "";
      const code: string[] = [];
      i++;
      // Closing fence detected without a dynamically-built RegExp (regex-injection
      // safe): a line that trims to >= flen of the same fence char and nothing else.
      const isClose = (s: string): boolean => {
        const tr = s.trim();
        if (tr.length < flen) return false;
        for (let j = 0; j < tr.length; j++) if (tr.charAt(j) !== fchar) return false;
        return true;
      };
      while (i < n && !isClose(lines[i] || "")) {
        code.push(lines[i] || "");
        i++;
      }
      if (i < n) i++;
      html += mdCodeBlock(code.join("\n"), lang);
      continue;
    }
    if (!line.trim()) {
      i++;
      continue;
    }
    if (options.math) {
      const block = mathBlock(lines, i, n);
      if (block) {
        html += mathElement(block.tex, true, true);
        i = block.next;
        continue;
      }
    }
    const hm = line.match(/^(#{1,6})[ \t]+(.*?)[ \t]*#*$/);
    if (hm) {
      const lv = (hm[1] || "").length;
      html += "<h" + lv + ">" + mdInline(hm[2], options) + "</h" + lv + ">";
      i++;
      continue;
    }
    if (hrRe.test(line)) {
      html += "<hr>";
      i++;
      continue;
    }
    if (quoteDepth < MAX_NESTING && /^\s*>\s?/.test(line)) {
      const q: string[] = [];
      while (i < n && /^\s*>\s?/.test(lines[i] || "")) {
        q.push((lines[i] || "").replace(/^\s*>\s?/, ""));
        i++;
      }
      html += "<blockquote>" + renderLines(q, quoteDepth + 1, options) + "</blockquote>";
      continue;
    }
    if (looksTable(i)) {
      const cells = (r: string): string[] =>
        r
          .trim()
          .replace(/^\||\|$/g, "")
          .split("|")
          .map((x) => x.trim());
      const head = cells(lines[i] || "");
      i += 2;
      let t =
        "<table><thead><tr>" +
        head.map((c) => "<th>" + mdInline(c, options) + "</th>").join("") +
        "</tr></thead><tbody>";
      while (i < n && (lines[i] || "").indexOf("|") !== -1 && (lines[i] || "").trim()) {
        const r = cells(lines[i] || "");
        t +=
          "<tr>" +
          head.map((_c, ci) => "<td>" + mdInline(r[ci] || "", options) + "</td>").join("") +
          "</tr>";
        i++;
      }
      html += '<div class="md-table-wrap">' + t + "</tbody></table></div>";
      continue;
    }
    if (listRe.test(line)) {
      const lr = mdList(lines, i, n, options);
      html += lr.html;
      i = lr.next;
      continue;
    }
    const para = [line];
    i++;
    while (
      i < n &&
      (lines[i] || "").trim() &&
      !listRe.test(lines[i] || "") &&
      !fenceRe.test(lines[i] || "") &&
      !hrRe.test(lines[i] || "") &&
      !/^(#{1,6}[ \t]|\s*>)/.test(lines[i] || "") &&
      !looksTable(i) &&
      !(options.math && /^\s*(\$\$|\\\[)/.test(lines[i] || ""))
    ) {
      para.push(lines[i] || "");
      i++;
    }
    html += "<p>" + mdInline(para.join(" "), options) + "</p>";
  }
  return html;
}
