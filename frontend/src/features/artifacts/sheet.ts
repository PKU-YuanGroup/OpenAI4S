import { el, translate } from "./api";
import { filesT } from "./copy";

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
    if (!row || typeof row !== "object" || Array.isArray(row)) continue;
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
  shape: { rows: number; columns: number; keys: string[] };
} {
  const shape = sheetShape(rows);
  const safeRows = rows.slice(0, SHEET_MAX_ROWS);
  const columns = shape.keys.slice(0, SHEET_MAX_COLUMNS);
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
  const note = el("div", "renderer-note", sheetShapeText(sheetShape(rows), shownRows, shownColumns));
  container.appendChild(note);
  return note;
}

/** The shape sentence plus what the drawn table leaves out. */
export function sheetShapeText(
  shape: { rows: number; columns: number },
  shownRows: number,
  shownColumns: number,
): string {
  const text = translate("viewer.table.shape", shape.rows.toLocaleString(), shape.columns.toLocaleString());
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
  return hidden ? text + " " + hidden : text;
}

/** "Show all columns" keeps the drawn table within the default cell budget. */
export const SHEET_MAX_CELLS = SHEET_MAX_ROWS * SHEET_MAX_COLUMNS;

function cellText(row: Record<string, unknown>, key: string): string {
  const value = Object.prototype.hasOwnProperty.call(row, key) ? row[key] : "";
  return String(value ?? "");
}

const collator =
  typeof Intl !== "undefined" && typeof Intl.Collator === "function"
    ? new Intl.Collator(undefined, { numeric: true, sensitivity: "base" })
    : null;

/**
 * Sort order for one column: numbers numerically and before text, text in
 * natural order, and blank cells last in either direction.
 */
export function compareSheetCells(a: string, b: string, dir: 1 | -1): number {
  const blankA = a.trim() === "";
  const blankB = b.trim() === "";
  if (blankA || blankB) return blankA === blankB ? 0 : blankA ? 1 : -1;
  const na = Number(a);
  const nb = Number(b);
  const numA = Number.isFinite(na);
  const numB = Number.isFinite(nb);
  if (numA && numB) return (na - nb) * dir;
  if (numA !== numB) return (numA ? -1 : 1) * dir;
  return (collator ? collator.compare(a, b) : a < b ? -1 : a > b ? 1 : 0) * dir;
}

/**
 * Rows a filter keeps. `column:value` searches that column when the prefix
 * names one; anything else matches a substring of any column, ignoring case.
 */
export function filterSheetRows(
  rows: Record<string, unknown>[],
  columns: string[],
  query: string,
): Record<string, unknown>[] {
  const text = query.trim();
  if (!text) return rows;
  const colon = text.indexOf(":");
  if (colon > 0) {
    const name = text.slice(0, colon).trim().toLowerCase();
    const column = columns.find((key) => key.toLowerCase() === name);
    if (column !== undefined) {
      const needle = text.slice(colon + 1).trim().toLowerCase();
      return rows.filter((row) => cellText(row, column).toLowerCase().includes(needle));
    }
  }
  const needle = text.toLowerCase();
  return rows.filter((row) => columns.some((key) => cellText(row, key).toLowerCase().includes(needle)));
}

/**
 * app.js:8797-8802. Caps at 5000×100 and states the truncation.
 * Window-exported for `tests/browser_smoke.mjs` (tabular banner).
 *
 * ART-3: the default (workbench-off) viewer is interactive on the rows it
 * already read: a header click sorts (ascending, descending, original), the
 * filter box keeps matching rows, and a table wider than 100 columns can show
 * every column, with the drawn rows cut to the same cell budget. All of it is
 * client-side and needs no server flag.
 */
export function renderSheet(container: HTMLElement, rows: Record<string, unknown>[]): void {
  if (!rows.every((row) => row !== null && typeof row === "object" && !Array.isArray(row))) {
    container.appendChild(el("pre", "renderer-source", JSON.stringify(rows, null, 2)));
    return;
  }
  const cap = sheetCap(rows);
  const note = appendSheetShape(container, rows, cap.safeRows.length, cap.columns.length);
  const allKeys = cap.shape.keys;
  const state: { query: string; sortKey: string; dir: 0 | 1 | -1; allColumns: boolean } = {
    query: "",
    sortKey: "",
    dir: 0,
    allColumns: false,
  };
  const tools = el("div", "sheet-tools");
  const filter = el("input", "sheet-filter") as HTMLInputElement;
  filter.type = "search";
  filter.placeholder = filesT("sheet.filter.ph");
  filter.setAttribute("aria-label", filesT("sheet.filter.label"));
  tools.appendChild(filter);
  let widen: HTMLElement | null = null;
  if (allKeys.length > SHEET_MAX_COLUMNS) {
    widen = el("button", "outline-btn small", filesT("sheet.columns.all", allKeys.length.toLocaleString()));
    widen.setAttribute("type", "button");
    tools.appendChild(widen);
  }
  const status = el("span", "sheet-status");
  status.setAttribute("role", "status");
  tools.appendChild(status);
  container.appendChild(tools);
  const hold = el("div", "sheet-hold");
  container.appendChild(hold);
  let drawnNodes: HTMLElement[] = [];

  const paint = (): void => {
    drawnNodes.forEach((node) => node.remove());
    drawnNodes = [];
    const columns = state.allColumns ? allKeys : cap.columns;
    let shown = filterSheetRows(rows, columns, state.query);
    if (state.sortKey && state.dir) {
      const key = state.sortKey;
      const dir = state.dir;
      shown = shown
        .map((row, index) => ({ row, index }))
        .sort((x, y) => compareSheetCells(cellText(x.row, key), cellText(y.row, key), dir) || x.index - y.index)
        .map((entry) => entry.row);
    }
    const rowLimit = state.allColumns
      ? Math.max(1, Math.min(SHEET_MAX_ROWS, Math.floor(SHEET_MAX_CELLS / Math.max(1, columns.length))))
      : SHEET_MAX_ROWS;
    const drawn = shown.slice(0, rowLimit);
    const notes: string[] = [];
    if (state.query.trim()) notes.push(filesT("sheet.filtered", shown.length.toLocaleString(), rows.length.toLocaleString()));
    if (state.allColumns || state.query.trim() || state.sortKey) {
      if (drawn.length < shown.length) notes.push(filesT("sheet.rowsCapped", drawn.length.toLocaleString()));
    }
    status.textContent = notes.join(" · ");
    // The shape sentence follows the drawn columns and the display cap; rows a
    // filter leaves out are the status line's business, not "not shown" here.
    note.textContent = sheetShapeText(
      cap.shape,
      state.query.trim() ? cap.shape.rows : drawn.length,
      columns.length,
    );
    if (widen) {
      widen.textContent = state.allColumns
        ? filesT("sheet.columns.first", SHEET_MAX_COLUMNS)
        : filesT("sheet.columns.all", allKeys.length.toLocaleString());
    }
    const table = el("table", "sheet");
    const head = el("tr");
    columns.forEach((key) => {
      const th = el("th", state.sortKey === key && state.dir ? "sheet-sorted" : null, key);
      th.setAttribute("tabindex", "0");
      th.setAttribute("title", filesT("sheet.sort.hint"));
      th.setAttribute(
        "aria-sort",
        state.sortKey === key && state.dir ? (state.dir > 0 ? "ascending" : "descending") : "none",
      );
      if (state.sortKey === key && state.dir) th.dataset.sort = state.dir > 0 ? "asc" : "desc";
      const toggle = (): void => {
        if (state.sortKey !== key) {
          state.sortKey = key;
          state.dir = 1;
        } else {
          state.dir = state.dir === 1 ? -1 : state.dir === -1 ? 0 : 1;
          if (!state.dir) state.sortKey = "";
        }
        paint();
      };
      th.onclick = toggle;
      th.onkeydown = (event: KeyboardEvent) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          toggle();
        }
      };
      head.appendChild(th);
    });
    table.appendChild(head);
    drawn.forEach((row) => {
      const tr = el("tr");
      columns.forEach((key) => tr.appendChild(el("td", null, cellText(row, key))));
      table.appendChild(tr);
    });
    hold.appendChild(table);
    drawnNodes.push(table);
    if (!drawn.length && rows.length) {
      const empty = el("div", "dock-empty", filesT("sheet.noMatch"));
      hold.appendChild(empty);
      drawnNodes.push(empty);
    }
  };

  let pending: ReturnType<typeof setTimeout> | null = null;
  filter.oninput = () => {
    if (pending) clearTimeout(pending);
    pending = setTimeout(() => {
      pending = null;
      state.query = filter.value || "";
      paint();
    }, 150);
  };
  if (widen) {
    widen.onclick = () => {
      state.allColumns = !state.allColumns;
      paint();
    };
  }
  paint();
}
