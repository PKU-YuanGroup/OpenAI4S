import { el, translate } from "./api";

/**
 * app.js:8771-8774. Union of every row's keys — not `rows[0]`, which hides
 * fields that only appear later in a ragged JSON table.
 */
export function sheetShape(rows: Record<string, unknown>[]): {
  rows: number;
  columns: number;
  keys: string[];
} {
  const keys = new Set<string>();
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    for (const key of Object.keys(row)) keys.add(key);
  }
  return { rows: rows.length, columns: keys.size, keys: Array.from(keys) };
}

export const SHEET_MAX_ROWS = 5000;
export const SHEET_MAX_COLUMNS = 100;

export function sheetCap(rows: Record<string, unknown>[]): {
  safeRows: Record<string, unknown>[];
  columns: string[];
  hiddenRows: number;
  hiddenColumns: number;
  shape: { rows: number; columns: number };
} {
  const shape = sheetShape(rows);
  const safeRows = rows.slice(0, SHEET_MAX_ROWS);
  const columns = Object.keys(safeRows[0] || {}).slice(0, SHEET_MAX_COLUMNS);
  return {
    safeRows,
    columns,
    hiddenRows: Math.max(0, shape.rows - safeRows.length),
    hiddenColumns: Math.max(0, shape.columns - columns.length),
    shape,
  };
}

/** app.js:8785-8795. Reuses `nb.table.*` sentences on purpose. */
export function appendSheetShape(
  container: HTMLElement,
  rows: Record<string, unknown>[],
  shownRows: number,
  shownColumns: number,
): HTMLElement {
  const shape = sheetShape(rows);
  const note = el(
    "div",
    "renderer-note",
    translate("viewer.table.shape", shape.rows.toLocaleString(), shape.columns.toLocaleString()),
  );
  const hiddenRows = Math.max(0, shape.rows - shownRows);
  const hiddenColumns = Math.max(0, shape.columns - shownColumns);
  let hidden = "";
  if (hiddenRows && hiddenColumns) {
    hidden = translate(
      "nb.table.bothHidden",
      hiddenRows.toLocaleString(),
      hiddenColumns.toLocaleString(),
    );
  } else if (hiddenRows) hidden = translate("nb.table.rowsHidden", hiddenRows.toLocaleString());
  else if (hiddenColumns) hidden = translate("nb.table.colsHidden", hiddenColumns.toLocaleString());
  if (hidden) note.appendChild(document.createTextNode(" " + hidden));
  container.appendChild(note);
  return note;
}

/**
 * app.js:8797-8802. Caps at 5000×100 and states the truncation.
 * Window-exported for `tests/browser_smoke.mjs` (tabular banner).
 */
export function renderSheet(container: HTMLElement, rows: Record<string, unknown>[]): void {
  const cap = sheetCap(rows);
  appendSheetShape(container, rows, cap.safeRows.length, cap.columns.length);
  const table = el("table", "sheet");
  const head = el("tr");
  cap.columns.forEach((key) => head.appendChild(el("th", null, key)));
  table.appendChild(head);
  cap.safeRows.forEach((row) => {
    const tr = el("tr");
    cap.columns.forEach((key) => tr.appendChild(el("td", null, String(row[key] ?? ""))));
    table.appendChild(tr);
  });
  container.appendChild(table);
}
