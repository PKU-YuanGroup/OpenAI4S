/**
 * Unified code highlighter: the mdHighlight character scanner (app.js:12740-12759)
 * is the only implementation. `_ocHighlight` (6093-6118) is not ported.
 *
 * Keyword table = _OC_KW(6093-6096) ∪ MD_KEYWORDS(12716-12723).
 * EDKW (13137-13149) is derived from that table plus editor-only extras.
 * Span class names stay `.tok-com` / `.tok-str` / `.tok-num` / `.tok-kw` / `.tok-fn`.
 *
 * Visible change vs app.js Notebook cells: they pick up the chat keyword set
 * (e.g. python `self`/`print`, bash `alias`/`time`) and this scanner (JS `//`
 * comments, python triple quotes, sticky numbers) instead of the `#`-only regex
 * tokenizer. CSS is unchanged.
 */

import { esc } from "./esc";

const OC_KW = {
  python:
    "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case",
  bash: "if then else elif fi for while until do done case esac function in select return set unset export local read source echo cd exit",
  r: "if else for while repeat function return break next in TRUE FALSE NULL NA Inf NaN library require",
} as const;

const MD_KEYWORDS_SRC = {
  python:
    "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case self print len range enumerate zip map filter open int float str list dict set tuple bool sum min max abs sorted reversed type isinstance super",
  javascript:
    "function return if else for while do const let var new class extends super import from export default async await yield try catch finally throw typeof instanceof in of this null undefined true false void delete switch case break continue static get set",
  bash: "if then else elif fi for while until do done case esac function in select time echo export local return set unset read source alias",
  r: "if else for while repeat function return break next in TRUE FALSE NULL NA Inf NaN library require",
  sql: "select from where group by order having join inner left right outer on as insert into values update set delete create table drop alter index and or not null distinct limit union all",
  _default:
    "if else for while return function class import from export const let var def new try catch finally throw switch case break continue true false null undefined and or not in is with as async await yield",
} as const;

function unionWords(...lists: string[]): string {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const list of lists) {
    for (const word of list.split(/\s+/)) {
      if (!word || seen.has(word)) continue;
      seen.add(word);
      out.push(word);
    }
  }
  return out.join(" ");
}

/** Unified keyword strings; mdKw() splits these. MD order, then OC-only words. */
export const MD_KEYWORDS: Record<string, string> = {
  python: unionWords(MD_KEYWORDS_SRC.python, OC_KW.python),
  javascript: MD_KEYWORDS_SRC.javascript,
  bash: unionWords(MD_KEYWORDS_SRC.bash, OC_KW.bash),
  r: unionWords(MD_KEYWORDS_SRC.r, OC_KW.r),
  sql: MD_KEYWORDS_SRC.sql,
  _default: MD_KEYWORDS_SRC._default,
};

export const MD_LINE_COMMENT: Record<string, string> = {
  python: "#",
  bash: "#",
  r: "#",
  yaml: "#",
  toml: "#",
  ruby: "#",
  javascript: "//",
  sql: "--",
};

export const MD_BLOCK_COMMENT: Record<string, readonly [string, string]> = {
  javascript: ["/*", "*/"],
};

const MD_LANG_ALIAS: Record<string, string> = {
  py: "python",
  python: "python",
  js: "javascript",
  javascript: "javascript",
  ts: "javascript",
  typescript: "javascript",
  jsx: "javascript",
  tsx: "javascript",
  node: "javascript",
  json: "javascript",
  sh: "bash",
  bash: "bash",
  shell: "bash",
  zsh: "bash",
  console: "bash",
  r: "r",
  rlang: "r",
  sql: "sql",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
  ini: "toml",
  rb: "ruby",
  ruby: "ruby",
};

export function mdLang(l: string | null | undefined): string {
  const key = (l || "").toLowerCase();
  return MD_LANG_ALIAS[key] || key;
}

const mdKwCache: Record<string, Set<string>> = {};

export function mdKw(lang: string | null | undefined): Set<string> {
  const c = mdLang(lang);
  const cached = mdKwCache[c];
  if (cached) return cached;
  const source = MD_KEYWORDS[c] || MD_KEYWORDS._default || "";
  const s = new Set(source.split(/\s+/).filter(Boolean));
  mdKwCache[c] = s;
  return s;
}

function sp(cls: string, s: string): string {
  return '<span class="tok-' + cls + '">' + esc(s) + "</span>";
}

/**
 * Lightweight language-aware tokenizer. Returns escaped HTML with
 * `<span class="tok-*">` wrappers; concatenating textContent of the result
 * is byte-identical to the source (copy button contract).
 */
export function mdHighlight(code: string | null | undefined, lang?: string | null): string {
  code = String(code == null ? "" : code);
  if (!code) return "";
  if (code.length > 24000) return esc(code);
  const c = mdLang(lang);
  const kw = mdKw(lang);
  const lc = MD_LINE_COMMENT[c] || null;
  const bc = MD_BLOCK_COMMENT[c] || null;
  const py = c === "python";
  const reIdent = /[A-Za-z_$@][\w$]*/y;
  const reNum = /0[xX][0-9a-fA-F]+|\d[\d_]*\.?\d*(?:[eE][+-]?\d+)?[jJ]?/y;
  let i = 0;
  const n = code.length;
  let out = "";
  while (i < n) {
    const ch = code.charAt(i);
    if (lc && code.startsWith(lc, i)) {
      let j = code.indexOf("\n", i);
      if (j < 0) j = n;
      out += sp("com", code.slice(i, j));
      i = j;
      continue;
    }
    if (bc && code.startsWith(bc[0], i)) {
      let j = code.indexOf(bc[1], i);
      j = j < 0 ? n : j + bc[1].length;
      out += sp("com", code.slice(i, j));
      i = j;
      continue;
    }
    if (py && (code.startsWith('"""', i) || code.startsWith("'''", i))) {
      const q = code.slice(i, i + 3);
      let j = code.indexOf(q, i + 3);
      j = j < 0 ? n : j + 3;
      out += sp("str", code.slice(i, j));
      i = j;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") {
      let j = i + 1;
      while (j < n && code.charAt(j) !== ch) {
        if (code.charAt(j) === "\\") j++;
        j++;
      }
      j = Math.min(n, j + 1);
      out += sp("str", code.slice(i, j));
      i = j;
      continue;
    }
    if (ch >= "0" && ch <= "9") {
      reNum.lastIndex = i;
      const mn = reNum.exec(code);
      const tk = mn ? mn[0] : ch;
      out += sp("num", tk);
      i += tk.length;
      continue;
    }
    if (/[A-Za-z_$@]/.test(ch)) {
      reIdent.lastIndex = i;
      const mi = reIdent.exec(code);
      const w = mi ? mi[0] : ch;
      i += w.length;
      if (w.charAt(0) === "@") out += sp("fn", w);
      else if (kw.has(w)) out += sp("kw", w);
      else if (code.charAt(i) === "(") out += sp("fn", w);
      else out += esc(w);
      continue;
    }
    out += esc(ch);
    i++;
  }
  return out;
}

/** Notebook cells use the same scanner; `_ocHighlight` is not a second implementation. */
export const ocHighlight = mdHighlight;

function uniqueAppend(base: readonly string[], extra: Iterable<string>): string[] {
  const seen = new Set<string>(base);
  const out = base.slice();
  for (const word of extra) {
    if (!word || seen.has(word)) continue;
    seen.add(word);
    out.push(word);
  }
  return out;
}

// Original EDKW arrays (app.js:13137-13147). Table keywords missing from these
// are appended so the editor list cannot drift from mdKw().
const EDKW_PY = [
  "def",
  "class",
  "return",
  "import",
  "from",
  "as",
  "if",
  "elif",
  "else",
  "for",
  "while",
  "break",
  "continue",
  "pass",
  "with",
  "try",
  "except",
  "finally",
  "raise",
  "lambda",
  "yield",
  "global",
  "nonlocal",
  "assert",
  "async",
  "await",
  "and",
  "or",
  "not",
  "in",
  "is",
  "None",
  "True",
  "False",
  "self",
  "print",
  "len",
  "range",
  "enumerate",
  "zip",
  "map",
  "filter",
  "list",
  "dict",
  "set",
  "tuple",
  "str",
  "int",
  "float",
  "bool",
  "open",
  "super",
  "isinstance",
  "format",
];
const EDKW_JS = [
  "const",
  "let",
  "var",
  "function",
  "return",
  "if",
  "else",
  "for",
  "while",
  "do",
  "break",
  "continue",
  "switch",
  "case",
  "default",
  "try",
  "catch",
  "finally",
  "throw",
  "new",
  "delete",
  "typeof",
  "instanceof",
  "void",
  "in",
  "of",
  "this",
  "class",
  "extends",
  "super",
  "import",
  "export",
  "from",
  "as",
  "async",
  "await",
  "yield",
  "static",
  "get",
  "set",
  "null",
  "undefined",
  "true",
  "false",
  "console",
  "document",
  "window",
  "Object",
  "Array",
  "String",
  "Number",
  "Boolean",
  "Promise",
  "Math",
  "JSON",
  "Map",
  "Set",
];
const EDKW_TS_EXTRA = [
  "interface",
  "type",
  "enum",
  "namespace",
  "declare",
  "readonly",
  "public",
  "private",
  "protected",
  "implements",
  "abstract",
  "keyof",
  "never",
  "unknown",
  "any",
  "string",
  "number",
  "boolean",
];
const EDKW_CSS = [
  "display",
  "position",
  "flex",
  "grid",
  "color",
  "background",
  "background-color",
  "border",
  "border-radius",
  "margin",
  "padding",
  "width",
  "height",
  "max-width",
  "min-width",
  "font-size",
  "font-weight",
  "font-family",
  "line-height",
  "text-align",
  "align-items",
  "justify-content",
  "gap",
  "opacity",
  "overflow",
  "z-index",
  "transition",
  "transform",
  "box-shadow",
  "cursor",
  "white-space",
  "absolute",
  "relative",
  "fixed",
  "sticky",
  "inherit",
  "none",
  "auto",
  "block",
  "hidden",
  "pointer",
  "center",
];
const EDKW_HTML = [
  "div",
  "span",
  "class",
  "href",
  "src",
  "style",
  "input",
  "button",
  "script",
  "link",
  "section",
  "header",
  "footer",
  "article",
  "label",
  "textarea",
  "select",
  "option",
  "table",
  "thead",
  "tbody",
  "title",
  "width",
  "height",
  "placeholder",
  "value",
  "type",
  "target",
  "alt",
  "aria-label",
  "data-icon",
];
const EDKW_SH = [
  "echo",
  "export",
  "source",
  "function",
  "local",
  "return",
  "if",
  "then",
  "elif",
  "else",
  "fi",
  "for",
  "in",
  "do",
  "done",
  "while",
  "case",
  "esac",
  "read",
  "cd",
  "mkdir",
  "grep",
  "sed",
  "awk",
  "cat",
  "chmod",
  "exit",
  "set",
  "unset",
  "true",
  "false",
];
const EDKW_R = [
  "function",
  "return",
  "if",
  "else",
  "for",
  "while",
  "repeat",
  "break",
  "next",
  "library",
  "require",
  "TRUE",
  "FALSE",
  "NULL",
  "NA",
  "c",
  "list",
  "vector",
  "data.frame",
  "matrix",
  "print",
  "cat",
  "paste",
  "paste0",
  "length",
  "names",
  "nrow",
  "ncol",
  "sapply",
  "lapply",
  "ggplot",
  "aes",
];
const EDKW_YAML = [
  "true",
  "false",
  "null",
  "name",
  "version",
  "on",
  "jobs",
  "steps",
  "run",
  "uses",
  "with",
  "env",
  "needs",
];
const EDKW_XML = ["version", "encoding", "xmlns", "xsi"];
const EDKW_JSON = ["true", "false", "null"];

const pyKw = uniqueAppend(EDKW_PY, mdKw("python"));
const jsKw = uniqueAppend(EDKW_JS, mdKw("javascript"));
const tsKw = uniqueAppend(jsKw.concat(EDKW_TS_EXTRA), mdKw("javascript"));
const shKw = uniqueAppend(EDKW_SH, mdKw("bash"));
const rKw = uniqueAppend(EDKW_R, mdKw("r"));

/** Per-extension editor keyword lists, derived from the unified table. */
export const EDKW: Record<string, readonly string[]> = {
  py: pyKw,
  js: jsKw,
  ts: tsKw,
  mjs: jsKw,
  cjs: jsKw,
  jsx: jsKw,
  tsx: tsKw,
  css: EDKW_CSS,
  html: EDKW_HTML,
  htm: EDKW_HTML,
  sh: shKw,
  bash: shKw,
  zsh: shKw,
  r: rKw,
  yaml: EDKW_YAML,
  yml: EDKW_YAML,
  xml: EDKW_XML,
  json: EDKW_JSON,
};

export function editorKeywords(ext: string): readonly string[] {
  return EDKW[ext] || [];
}
