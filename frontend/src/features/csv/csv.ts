/**
 * Tabular parsers for the new workbench.
 *
 * scientific_renderers.js is the scientific-parser home and is not modified
 * (F-08: 本体不动). It does not export a CSV parser; the RFC-4180-ish loop at
 * app.js:9690-9704 (parseDelimited) is the only path that keeps a newline
 * inside a quoted field. csvFields (12907-12916) and parseTable (12878) now
 * share that engine so a quoted-newline sample cannot diverge.
 */

export type ArtifactRef = {
  filename?: string | null;
  content_type?: string | null;
};

/**
 * Minimal RFC-4180-ish parser: quoted fields, `""` escapes, CRLF.
 * Port of app.js:9690-9704.
 */
export function parseDelimited(text: string, sep: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let q = false;
  const src = String(text == null ? "" : text);
  for (let i = 0; i < src.length; i++) {
    const ch = src.charAt(i);
    if (q) {
      if (ch === '"') {
        if (src.charAt(i + 1) === '"') {
          field += '"';
          i++;
        } else q = false;
      } else field += ch;
    } else if (ch === '"') q = true;
    else if (ch === sep) {
      row.push(field);
      field = "";
    } else if (ch === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (ch !== "\r") field += ch;
  }
  if (field.length || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

/**
 * One-record field splitter. Same engine as parseDelimited; fields are trimmed
 * to match app.js:12907-12916. A quoted newline in `line` stays inside the field.
 */
export function csvFields(line: string, sep?: string): string[] {
  sep = sep || ",";
  const rows = parseDelimited(String(line == null ? "" : line), sep);
  const row = rows[0];
  if (!row) return [""];
  return row.map((s) => s.trim());
}

export function csv(line: string, sep?: string): string[] {
  return csvFields(line, sep);
}

/**
 * The delimiter a tabular artifact actually uses.
 * Port of app.js:12892-12904.
 */
export function delimiterFor(
  filename: string | null | undefined,
  contentType: string | null | undefined,
  headerLine: string | null | undefined,
): string {
  const name = String(filename || "").toLowerCase();
  const type = String(contentType || "").toLowerCase();
  if (/\.tsv$/.test(name) || /tab-separated/.test(type)) return "\t";
  if (/\.csv$/.test(name) || /\bcsv\b/.test(type)) return ",";
  const header = String(headerLine || "");
  let best = ",";
  let width = 1;
  for (const candidate of ["\t", ",", ";", "|"]) {
    const fields = csvFields(header, candidate).length;
    if (fields > width) {
      best = candidate;
      width = fields;
    }
  }
  return best;
}

function isBlankRow(row: string[]): boolean {
  return !row.some((c) => String(c).trim());
}

/** Cells up to the last non-blank one; trailing separators add no column. */
function filledWidth(row: string[]): number {
  let width = row.length;
  while (width > 0 && !String(row[width - 1]).trim()) width--;
  return width;
}

/**
 * One key per column. Row objects are keyed by header, so a repeated name
 * (`mean,mean,sd`) used to overwrite its twin and cells past the header were
 * dropped. A repeat takes pandas' `.1`, `.2` suffix; a cell past the header is
 * keyed by its position, as array rows are.
 */
function columnNames(header: string[], width: number): string[] {
  const seen = new Set<string>();
  const names: string[] = [];
  for (let i = 0; i < width; i++) {
    const base = i < header.length ? header[i] || "" : String(i);
    let name = base;
    for (let n = 1; seen.has(name); n++) name = `${base}.${n}`;
    seen.add(name);
    names.push(name);
  }
  return names;
}

/** Whether parseTable reads `text` as JSON rather than as a delimited table. */
function readsAsJson(text: string, a: ArtifactRef): boolean {
  const nm = (a.filename || "").toLowerCase();
  const type = String(a.content_type || "").toLowerCase();
  // A declared CSV/TSV is not sniffed for JSON: its first header cell can
  // open with a bracket (`[Na+],[Cl-],conc`).
  const delimited = /\.(csv|tsv)$/.test(nm) || /\bcsv\b|tab-separated/.test(type);
  return nm.endsWith(".json") || (!delimited && /^\s*[\[{]/.test(text));
}

/** The first line with any non-space character, else the first line. */
function firstFilledLine(raw: string): string {
  for (let start = 0; ; ) {
    const end = raw.indexOf("\n", start);
    const line = raw.slice(start, end < 0 ? raw.length : end);
    if (line.trim()) return line;
    if (end < 0) break;
    start = end + 1;
  }
  const end = raw.indexOf("\n");
  return end < 0 ? raw : raw.slice(0, end);
}

/**
 * Artifact table parse. JSON branch is app.js:12878; the CSV branch uses
 * parseDelimited so a newline inside quotes is one cell, not a new row.
 */
export function parseTable(
  text: string,
  a: ArtifactRef = {},
): Record<string, unknown>[] | null {
  const nm = (a.filename || "").toLowerCase();
  if (readsAsJson(text, a)) {
    try {
      let j: unknown = JSON.parse(text);
      if (!Array.isArray(j)) {
        const obj = j as Record<string, unknown> | null;
        j = (obj && (obj.rows || obj.data || obj.candidates || obj.items)) || [];
      }
      // A homogeneous array of arrays (`df.values.tolist()`) is a table whose
      // columns are positions; only a MIX of shapes is left as raw text.
      if (Array.isArray(j) && j.length && j.every((row) => Array.isArray(row))) {
        j = (j as unknown[][]).map((row) =>
          Object.fromEntries(row.map((value, index) => [String(index), value])),
        );
      }
      if (
        Array.isArray(j) && j.length &&
        j.every((row) => row !== null && typeof row === "object" && !Array.isArray(row))
      ) {
        return j as Record<string, unknown>[];
      }
    } catch {
      // invalid JSON is not a table
    }
    return null;
  }
  const raw = String(text == null ? "" : text).replace(/\r/g, "");
  const sep = delimiterFor(nm, a.content_type, firstFilledLine(raw));
  const rows = parseDelimited(raw, sep).filter((r) => !isBlankRow(r));
  if (rows.length < 2) return null;
  const header = (rows[0] || []).map((c) => c.trim());
  let width = header.length;
  for (let i = 1; i < rows.length; i++) width = Math.max(width, filledWidth(rows[i] || []));
  const cols = columnNames(header, width);
  return rows.slice(1).map((l) => {
    const o: Record<string, unknown> = {};
    cols.forEach((c, i) => {
      o[c] = (l[i] ?? "").trim();
    });
    return o;
  });
}

/** What String.prototype.trim removes: ECMAScript white space and line terminators. */
function isTrimmed(c: number): boolean {
  return c === 32 || (c >= 9 && c <= 13) || c === 0xa0 || c === 0x1680 ||
    (c >= 0x2000 && c <= 0x200a) || c === 0x2028 || c === 0x2029 ||
    c === 0x202f || c === 0x205f || c === 0x3000 || c === 0xfeff;
}

/**
 * A table's row count and column names (in file order), which is all a Files
 * thumbnail shows. For a delimited file this is one pass over the text that
 * keeps only the header's cells: parseTable would build every cell string and
 * every row object of what can be a file of tens of MB. JSON has no cheaper
 * reading than parsing it. The answer is parseTable's, row for row.
 */
export function tableShape(
  text: string,
  a: ArtifactRef = {},
): { rows: number; columns: string[] } | null {
  if (readsAsJson(text, a)) {
    const rows = parseTable(text, a);
    return rows && rows.length ? { rows: rows.length, columns: Object.keys(rows[0] || {}) } : null;
  }
  const src = String(text == null ? "" : text);
  const nm = (a.filename || "").toLowerCase();
  // parseTable drops every \r before parsing, so this pass skips them too.
  const sep = delimiterFor(nm, a.content_type, firstFilledLine(src).replace(/\r/g, "")).charCodeAt(0);
  let quoted = false;
  let fieldLen = 0; // parseDelimited's field.length
  let cells = 0; // cells completed in this row
  let filled = false; // this field has a character trim() keeps
  let rowWidth = 0; // filledWidth of this row so far
  // Assigned inside endRow(); the cast keeps TypeScript from pinning it to null.
  let header = null as string[] | null;
  let headerCells: string[] = [];
  let field = ""; // kept only until the header is known
  let rows = 0; // non-blank rows, header included
  let width = 0;
  const append = (c: number): void => {
    fieldLen++;
    if (!isTrimmed(c)) filled = true;
    if (!header) field += String.fromCharCode(c);
  };
  const endField = (): void => {
    if (filled) rowWidth = cells + 1;
    if (!header) headerCells.push(field);
    cells++;
    fieldLen = 0;
    filled = false;
    field = "";
  };
  const endRow = (): void => {
    endField();
    if (rowWidth > 0) {
      rows++;
      if (!header) header = headerCells.map((cell) => cell.trim());
      else width = Math.max(width, rowWidth);
    }
    headerCells = [];
    cells = 0;
    rowWidth = 0;
  };
  for (let i = 0; i < src.length; i++) {
    const c = src.charCodeAt(i);
    if (c === 13) continue;
    if (quoted) {
      if (c === 34) {
        let next = i + 1;
        while (src.charCodeAt(next) === 13) next++;
        if (src.charCodeAt(next) === 34) {
          append(34);
          i = next;
        } else quoted = false;
      } else append(c);
    } else if (c === 34) quoted = true;
    else if (c === sep) endField();
    else if (c === 10) endRow();
    else append(c);
  }
  if (fieldLen || cells) endRow();
  if (!header || rows < 2) return null;
  return { rows: rows - 1, columns: columnNames(header, Math.max(header.length, width)) };
}
