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
 * mdCodeBlock copy chrome uses t() key-name fallback until F-07/F-10 wire i18n.
 */

import { esc, escQuote } from "./esc";
import { mdHighlight } from "./highlight";

const COPY_ICON =
  '<svg class="ic-svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>';

/** F-07 owns t(); key-name fallback matches t() when the dictionary is absent. */
function t(key: string): string {
  return key;
}

export function mdCodeBlock(code: string, lang: string): string {
  const label = (lang || "").trim();
  return (
    '<div class="codeblock">' +
    '<div class="cb-head"><span class="cb-lang">' +
    esc(label || "text") +
    "</span>" +
    '<button class="cb-copy" type="button" title="' +
    t("code.copy.title") +
    '">' +
    COPY_ICON +
    '<span class="cb-copy-t">' +
    t("msgAction.copy") +
    "</span></button></div>" +
    "<pre><code>" +
    mdHighlight(code, label) +
    "</code></pre></div>"
  );
}

const MDC0 = String.fromCharCode(0xe000);
const MDC1 = String.fromCharCode(0xe001);
const mdCodeRestore = new RegExp(MDC0 + "(\\d+)" + MDC1, "g");

export function mdInline(t: string | null | undefined): string {
  t = String(t == null ? "" : t);
  const codes: string[] = [];
  t = t.replace(/`([^`]+)`/g, (_m, c: string) => {
    codes.push(c);
    return MDC0 + (codes.length - 1) + MDC1;
  });
  t = esc(t);
  // esc() now also escapes quotes (F-08). Capture groups interpolated into a
  // double-quoted HTML attribute still go through escQuote so an alt/href/src
  // value cannot close the attribute even if a future edit reorders the chain.
  t = t.replace(
    /!\[([^\]]*)\]\((data:image\/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+)\)/g,
    (_m, alt: string, src: string) =>
      '<img alt="' + escQuote(alt) + '" src="' + src + '">',
  );
  t = t.replace(
    /!\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g,
    (_m, alt: string, src: string) =>
      '<img alt="' + escQuote(alt) + '" src="' + escQuote(src) + '">',
  );
  t = t.replace(
    /\[([^\]]+)\]\(((?:https?:|mailto:|\/|#)[^\s)]+)\)/g,
    (_m, text: string, href: string) =>
      '<a href="' + escQuote(href) + '" target="_blank" rel="noopener">' + text + "</a>",
  );
  t = t.replace(/\*\*\*([^*]+?)\*\*\*/g, "<strong><em>$1</em></strong>");
  t = t.replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(^|[^\w*])__([^_]+?)__(?!\w)/g, "$1<strong>$2</strong>");
  t = t.replace(/(^|[^*])\*([^*\n]+?)\*/g, "$1<em>$2</em>");
  t = t.replace(/(^|[^\w_])_([^_\n]+?)_(?!\w)/g, "$1<em>$2</em>");
  t = t.replace(/~~([^~]+?)~~/g, "<del>$1</del>");
  t = t.replace(mdCodeRestore, (_m, k: string) => {
    const idx = +k;
    return "<code>" + esc(codes[idx]) + "</code>";
  });
  return t;
}

type MdListItem = { indent: number; ordered: boolean; text: string };
type MdListCursor = { v: number };

function mdBuildList(items: MdListItem[], cur: MdListCursor): string {
  const start = items[cur.v];
  if (!start) return "<ul></ul>";
  const ordered = start.ordered;
  const indent = start.indent;
  let html = "<" + (ordered ? "ol" : "ul") + ">";
  while (cur.v < items.length) {
    const item = items[cur.v];
    if (!item || item.indent < indent) break;
    if (item.indent > indent) break;
    const text = item.text;
    cur.v++;
    let nested = "";
    const next = items[cur.v];
    if (next && next.indent > indent) nested = mdBuildList(items, cur);
    html += "<li>" + mdInline(text) + nested + "</li>";
  }
  return html + "</" + (ordered ? "ol" : "ul") + ">";
}

function mdList(
  lines: string[],
  start: number,
  n: number,
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
  return { html: mdBuildList(items, { v: 0 }), next: i };
}

export function renderMd(src: string | null | undefined): string {
  const lines = String(src == null ? "" : src)
    .replace(/\r\n?/g, "\n")
    .split("\n");
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
    const hm = line.match(/^(#{1,6})[ \t]+(.*?)[ \t]*#*$/);
    if (hm) {
      const lv = (hm[1] || "").length;
      html += "<h" + lv + ">" + mdInline(hm[2]) + "</h" + lv + ">";
      i++;
      continue;
    }
    if (hrRe.test(line)) {
      html += "<hr>";
      i++;
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      const q: string[] = [];
      while (i < n && /^\s*>\s?/.test(lines[i] || "")) {
        q.push((lines[i] || "").replace(/^\s*>\s?/, ""));
        i++;
      }
      html += "<blockquote>" + renderMd(q.join("\n")) + "</blockquote>";
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
        head.map((c) => "<th>" + mdInline(c) + "</th>").join("") +
        "</tr></thead><tbody>";
      while (i < n && (lines[i] || "").indexOf("|") !== -1 && (lines[i] || "").trim()) {
        const r = cells(lines[i] || "");
        t +=
          "<tr>" +
          head.map((_c, ci) => "<td>" + mdInline(r[ci] || "") + "</td>").join("") +
          "</tr>";
        i++;
      }
      html += '<div class="md-table-wrap">' + t + "</tbody></table></div>";
      continue;
    }
    if (listRe.test(line)) {
      const lr = mdList(lines, i, n);
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
      !looksTable(i)
    ) {
      para.push(lines[i] || "");
      i++;
    }
    html += "<p>" + mdInline(para.join(" ")) + "</p>";
  }
  return html;
}
