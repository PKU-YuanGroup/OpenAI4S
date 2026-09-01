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

/**
 * Artifact table parse. JSON branch is app.js:12878; the CSV branch uses
 * parseDelimited so a newline inside quotes is one cell, not a new row.
 */
export function parseTable(
  text: string,
  a: ArtifactRef = {},
): Record<string, unknown>[] | null {
  const nm = (a.filename || "").toLowerCase();
  if (nm.endsWith(".json") || /^\s*[\[{]/.test(text)) {
    try {
      let j: unknown = JSON.parse(text);
      if (!Array.isArray(j)) {
        const obj = j as Record<string, unknown> | null;
        j = (obj && (obj.rows || obj.data || obj.candidates || obj.items)) || [];
      }
      if (Array.isArray(j) && j.length && typeof j[0] === "object") {
        return j as Record<string, unknown>[];
      }
    } catch {
      // invalid JSON is not a table
    }
    return null;
  }
  const raw = String(text == null ? "" : text).replace(/\r/g, "");
  const firstLine =
    raw.split("\n").find((l) => l.trim()) || raw.split("\n", 1)[0] || "";
  const sep = delimiterFor(nm, a.content_type, firstLine);
  const rows = parseDelimited(raw, sep).filter((r) => !isBlankRow(r));
  if (rows.length < 2) return null;
  const cols = (rows[0] || []).map((c) => c.trim());
  return rows.slice(1).map((l) => {
    const o: Record<string, unknown> = {};
    cols.forEach((c, i) => {
      o[c] = (l[i] ?? "").trim();
    });
    return o;
  });
}
