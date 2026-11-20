/**
 * ART-11: the first rows of a table the browser does not read whole -- a CSV
 * over the 8 MiB preview budget, an `.xlsx` workbook, or a gzip file -- from
 * `GET /artifacts/{id}/preview-table`.
 *
 * The server reads the exact version under its own time and size bounds and
 * returns at most 100 rows plus the file's row and column counts. This view
 * draws them with the interactive sheet, says how much of the file that is, and
 * keeps the exact-version download. Its one request is bound to the viewer's
 * read signal and a deadline, and no Artifact URL is ever an element `src`.
 */

import { copyLookup, type CopyTable } from "../../i18n/copy";
import { columnNames, filledWidth } from "../csv/csv";
import {
  api,
  ApiError,
  bytes,
  el,
  iconEl,
  PREVIEW_READ_MAX_BYTES,
  previewReadSignal,
} from "./api";
import { filesT, readFailureText, readRetryHelps } from "./copy";
import { appendEncodingNote } from "./encoding-note";
import { previewDownload } from "./partial";
import { renderSheet } from "./sheet";
import type { ArtifactRow } from "./types";
import { COMPRESSED_EXT, XLSX_EXT } from "./types";

const COPY: CopyTable = {
  zh: {
    "preview.loading": "正在读取表格开头并统计行数…",
    "preview.rows.exact": "共 {0} 行 × {1} 列",
    "preview.rows.atLeast": "至少 {0} 行 × {1} 列（行数未能在时限内数完）",
    "preview.rows.unknown": "行数未知 × {0} 列",
    "preview.shown.rows": "，仅显示前 {0} 行",
    "preview.shown.columns": "，仅显示前 {0} 列",
    "preview.shown.both": "，仅显示前 {0} 行、前 {1} 列",
    "preview.reason.tooLarge": "（文件 {0}，超出完整预览上限 {1}）",
    "preview.reason.gzip": "（gzip 压缩文件，按解压后的 {0} 预览）",
    "preview.reason.gzipUnnamed": "（gzip 压缩文件，按解压后的内容预览）",
    "preview.sheets": "工作表",
    "preview.empty": "表格中没有数据行。",
    "preview.error.not_tabular": "无法以表格预览：内容不是 CSV/TSV 表格。可下载原文件。",
    "preview.error.not_tabular.gzip":
      "这是 gzip 压缩文件，解压后的内容不是 CSV/TSV 表格，无法预览。可下载后解压查看。",
    "preview.error.unsupported_format":
      "不支持预览这种格式（旧版 .xls、加密的工作簿，或非 gzip 的压缩格式）。可在 Excel 中另存为 .xlsx 或 CSV 后再上传。",
    "preview.error.too_large_to_inspect":
      "文件结构超出预览检查上限（例如单行或单个字段过长、工作簿过大），已停止预览。可下载原文件。",
    "preview.error.invalid_file": "文件已损坏或格式无效，无法预览。可下载原文件。",
    "preview.error.sheet_not_found": "找不到这个工作表，它可能已被重命名或删除。",
    "preview.error.malformed": "预览响应格式无效，已停止显示。可重试或下载原文件。",
    "tile.compressed": "压缩文件",
  },
  en: {
    "preview.loading": "Reading the first rows and counting the table…",
    "preview.rows.exact": "{0} rows × {1} columns",
    "preview.rows.atLeast": "At least {0} rows × {1} columns (the count did not finish in time)",
    "preview.rows.unknown": "Unknown number of rows × {0} columns",
    "preview.shown.rows": ", showing the first {0} rows",
    "preview.shown.columns": ", showing the first {0} columns",
    "preview.shown.both": ", showing the first {0} rows and {1} columns",
    "preview.reason.tooLarge": " (the file is {0}, over the {1} full-preview limit)",
    "preview.reason.gzip": " (gzip-compressed; previewing the decompressed {0})",
    "preview.reason.gzipUnnamed": " (gzip-compressed; previewing the decompressed content)",
    "preview.sheets": "Sheets",
    "preview.empty": "The table has no data rows.",
    "preview.error.not_tabular":
      "This cannot be previewed as a table: the content is not a CSV/TSV table. You can download the original file.",
    "preview.error.not_tabular.gzip":
      "This is a gzip-compressed file whose content is not a CSV/TSV table, so it cannot be previewed. Download it and decompress it to read it.",
    "preview.error.unsupported_format":
      "This format cannot be previewed (a legacy .xls, a password-protected workbook, or a compression other than gzip). Save it as .xlsx or CSV in Excel and upload it again.",
    "preview.error.too_large_to_inspect":
      "The file is beyond what the preview inspects (for example a very long line or field, or a very large workbook), so the preview stopped. You can download the original file.",
    "preview.error.invalid_file": "The file is damaged or not in a valid format, so it cannot be previewed. You can download the original file.",
    "preview.error.sheet_not_found": "This sheet was not found; it may have been renamed or removed.",
    "preview.error.malformed": "The preview response was malformed and is not shown. Retry, or download the original file.",
    "tile.compressed": "Compressed file",
  },
};

export const previewT = copyLookup(COPY);

/** The server never takes longer than ~10 s; this only ends a stalled request. */
export const TABLE_PREVIEW_TIMEOUT_MS = 30_000;

/** What `GET /artifacts/{id}/preview-table` returns. */
export type TablePreviewPayload = {
  format: "csv" | "tsv" | "xlsx";
  encoding: string | null;
  compression: "gzip" | null;
  inner_filename: string | null;
  sheets: string[];
  sheet: string | null;
  /** CSV/TSV: the header cells. xlsx: column letters. */
  columns: string[];
  /** xlsx: the sheet row number of each returned row. */
  row_numbers: number[] | null;
  rows: string[][];
  /** Exact data-row count, or null when counting stopped at its bound. */
  total_rows: number | null;
  /** Rows counted before the bound, when `total_rows` is null. */
  total_rows_lower_bound: number | null;
  total_columns: number;
  truncated: boolean;
  size_bytes: number;
};

export type TablePreviewOptions = {
  /** The default viewer is here because the file is over its read budget. */
  reason?: "too_large";
  /** The worksheet to show; the workbook's first when omitted. */
  sheet?: string | null;
  /** Take over a 4xx refusal (the table viewer's binary-file card); true when handled. */
  onRefused?: (error: ApiError) => boolean;
};

/** A gzip file, by name or stored type. */
export function isCompressedArtifact(a: Pick<ArtifactRow, "filename" | "content_type">): boolean {
  return COMPRESSED_EXT.test(String(a.filename || "")) || /gzip/i.test(String(a.content_type || ""));
}

/** An `.xlsx` workbook, by name or stored type. */
export function isWorkbookArtifact(a: Pick<ArtifactRow, "filename" | "content_type">): boolean {
  return XLSX_EXT.test(String(a.filename || "")) || /spreadsheetml\.sheet/i.test(String(a.content_type || ""));
}

/** The route for one exact version (when the row pins one) and one sheet. */
export function tablePreviewPath(a: ArtifactRow, sheet?: string | null): string {
  const params = new URLSearchParams();
  if (a._exactVersion && a.version_id) params.set("version_id", String(a.version_id));
  if (sheet) params.set("sheet", sheet);
  const query = params.toString();
  return `/artifacts/${encodeURIComponent(a.id)}/preview-table${query ? "?" + query : ""}`;
}

const strings = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((item) => typeof item === "string");
const count = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= 0;

/** The payload, or null when its shape is not the documented one. */
export function readTablePreview(value: unknown): TablePreviewPayload | null {
  if (!value || typeof value !== "object") return null;
  const rec = value as Record<string, unknown>;
  const rows = rec.rows;
  if (!strings(rec.columns) || !Array.isArray(rows) || !rows.every(strings)) return null;
  if (!count(rec.total_columns) || !count(rec.size_bytes)) return null;
  if (rec.total_rows != null && !count(rec.total_rows)) return null;
  if (rec.total_rows_lower_bound != null && !count(rec.total_rows_lower_bound)) return null;
  const numbers = rec.row_numbers;
  if (numbers != null && (!Array.isArray(numbers) || !numbers.every(count) || numbers.length !== rows.length)) {
    return null;
  }
  const format = rec.format === "xlsx" || rec.format === "tsv" ? rec.format : "csv";
  return {
    format,
    encoding: typeof rec.encoding === "string" ? rec.encoding : null,
    compression: rec.compression === "gzip" ? "gzip" : null,
    inner_filename: typeof rec.inner_filename === "string" ? rec.inner_filename : null,
    sheets: strings(rec.sheets) ? rec.sheets : [],
    sheet: typeof rec.sheet === "string" ? rec.sheet : null,
    columns: rec.columns,
    row_numbers: numbers == null ? null : (numbers as number[]),
    rows: rows as string[][],
    total_rows: rec.total_rows == null ? null : (rec.total_rows as number),
    total_rows_lower_bound:
      rec.total_rows_lower_bound == null ? null : (rec.total_rows_lower_bound as number),
    total_columns: rec.total_columns,
    truncated: rec.truncated === true,
    size_bytes: rec.size_bytes,
  };
}

/**
 * Rows keyed the way the client parser keys them: a repeated header name gets
 * a `.1` suffix and a cell past the header is keyed by position. A workbook's
 * rows lead with their sheet row number.
 */
export function previewRecords(payload: TablePreviewPayload): Record<string, unknown>[] {
  const header = payload.columns.map((name) => name.trim());
  let width = header.length;
  for (const row of payload.rows) width = Math.max(width, filledWidth(row));
  const names = columnNames(header, width);
  const numbers = payload.row_numbers;
  return payload.rows.map((row, index) => {
    const record: Record<string, unknown> = {};
    if (numbers) record["#"] = String(numbers[index] ?? "");
    names.forEach((name, column) => {
      record[name] = row[column] ?? "";
    });
    return record;
  });
}

/** "共 900,000 行 × 6 列，仅显示前 100 行（文件 39.0 MB，超出完整预览上限 8.0 MB）". */
export function previewSummary(
  payload: TablePreviewPayload,
  options: Pick<TablePreviewOptions, "reason"> = {},
): string {
  const columns = payload.total_columns.toLocaleString();
  let text: string;
  if (payload.total_rows != null) {
    text = previewT("preview.rows.exact", payload.total_rows.toLocaleString(), columns);
  } else if (payload.total_rows_lower_bound != null) {
    text = previewT("preview.rows.atLeast", payload.total_rows_lower_bound.toLocaleString(), columns);
  } else {
    text = previewT("preview.rows.unknown", columns);
  }
  const shownRows = payload.rows.length;
  const moreRows = payload.total_rows == null || payload.total_rows > shownRows;
  const shownColumns = payload.columns.length;
  const moreColumns = shownColumns < payload.total_columns;
  if (moreRows && moreColumns) {
    text += previewT("preview.shown.both", shownRows.toLocaleString(), shownColumns.toLocaleString());
  } else if (moreRows && shownRows) {
    text += previewT("preview.shown.rows", shownRows.toLocaleString());
  } else if (moreColumns) {
    text += previewT("preview.shown.columns", shownColumns.toLocaleString());
  }
  if (payload.compression === "gzip") {
    text += payload.inner_filename
      ? previewT("preview.reason.gzip", payload.inner_filename)
      : previewT("preview.reason.gzipUnnamed");
  } else if (options.reason === "too_large") {
    text += previewT("preview.reason.tooLarge", bytes(payload.size_bytes), bytes(PREVIEW_READ_MAX_BYTES));
  }
  return text;
}

/** The person-facing reason a preview was refused or failed. */
export function previewFailureText(error: unknown, a: ArtifactRow): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "not_tabular":
        return previewT(isCompressedArtifact(a) ? "preview.error.not_tabular.gzip" : "preview.error.not_tabular");
      case "unsupported_format":
      case "too_large_to_inspect":
      case "invalid_file":
      case "sheet_not_found":
        return previewT("preview.error." + error.code);
    }
    return readFailureText(error);
  }
  if (error instanceof Error && error.name === "TimeoutError") return filesT("viewer.read.timeout");
  if (error instanceof Error && error.name === "MalformedPreview") return previewT("preview.error.malformed");
  return readFailureText(error);
}

function failureCard(
  container: HTMLElement,
  a: ArtifactRow,
  url: string,
  error: unknown,
  retry: () => void,
): void {
  const card = el("div", "renderer-fallback");
  card.appendChild(iconEl("alert-triangle", 18));
  const text = el("div", "renderer-fallback-text", previewFailureText(error, a));
  text.setAttribute("role", "alert");
  card.appendChild(text);
  // A refusal is a fact about the bytes; asking again cannot change it.
  const refused = error instanceof ApiError && error.status >= 400 && error.status < 500;
  if (!refused && readRetryHelps(error)) {
    const again = el("button", "outline-btn small", filesT("viewer.read.retry"));
    again.setAttribute("type", "button");
    again.onclick = retry;
    card.appendChild(again);
  }
  previewDownload(card, a, url);
  container.appendChild(card);
}

function sheetTabs(
  container: HTMLElement,
  a: ArtifactRow,
  url: string,
  payload: TablePreviewPayload,
  options: TablePreviewOptions,
): void {
  const bar = el("div", "preview-sheets");
  bar.setAttribute("role", "tablist");
  bar.setAttribute("aria-label", previewT("preview.sheets"));
  bar.appendChild(el("span", "preview-sheets-label", previewT("preview.sheets")));
  for (const name of payload.sheets) {
    const active = name === payload.sheet;
    const tab = el("button", active ? "preview-sheet active" : "preview-sheet", name);
    tab.setAttribute("type", "button");
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", active ? "true" : "false");
    tab.title = name;
    if (!active) tab.onclick = () => renderTablePreview(container, a, url, { ...options, sheet: name });
    bar.appendChild(tab);
  }
  container.appendChild(bar);
}

function paint(
  container: HTMLElement,
  a: ArtifactRow,
  url: string,
  payload: TablePreviewPayload,
  options: TablePreviewOptions,
): void {
  if (payload.format === "xlsx" && payload.sheets.length) sheetTabs(container, a, url, payload, options);
  appendEncodingNote(container, payload.encoding || "");
  const summary = previewSummary(payload, options);
  const records = previewRecords(payload);
  if (records.length) {
    renderSheet(container, records, { summary });
  } else {
    const note = el("div", "renderer-note renderer-completeness", summary);
    note.setAttribute("role", "status");
    container.appendChild(note);
    container.appendChild(el("div", "dock-empty", previewT("preview.empty")));
  }
  previewDownload(container, a, url);
}

/** The request each container is showing; a newer one (another sheet) replaces it. */
const inflight = new WeakMap<Element, AbortController>();

/** One request, cancelled with the view or by a newer one, and ended by a deadline. */
function requestPreview(
  container: HTMLElement,
  path: string,
  controller: AbortController,
): Promise<unknown> {
  const owner = previewReadSignal(container);
  let timedOut = false;
  const stop = (): void => controller.abort();
  if (owner) {
    if (owner.aborted) controller.abort();
    else owner.addEventListener("abort", stop, { once: true });
  }
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, TABLE_PREVIEW_TIMEOUT_MS);
  return api(path, { signal: controller.signal })
    .catch((error: unknown) => {
      if (timedOut) {
        const timeout = new Error("table preview timed out");
        timeout.name = "TimeoutError";
        throw timeout;
      }
      throw error;
    })
    .finally(() => {
      clearTimeout(timer);
      owner?.removeEventListener("abort", stop);
    });
}

/**
 * Show the server's bounded head of this exact version in `container`, which
 * it owns: anything already there is replaced.
 */
export function renderTablePreview(
  container: HTMLElement,
  a: ArtifactRow,
  url: string,
  options: TablePreviewOptions = {},
): void {
  // A newer render of this container (another sheet, a retry) replaces this
  // one; its aborted request may no longer paint, even if its answer arrived.
  inflight.get(container)?.abort();
  const controller = new AbortController();
  inflight.set(container, controller);
  container.innerHTML = "";
  const loading = el("div", "renderer-note", previewT("preview.loading"));
  loading.setAttribute("role", "status");
  container.appendChild(loading);
  requestPreview(container, tablePreviewPath(a, options.sheet), controller)
    .then((value) => {
      if (controller.signal.aborted || container.isConnected === false) return;
      const payload = readTablePreview(value);
      if (!payload) {
        const malformed = new Error("malformed table preview");
        malformed.name = "MalformedPreview";
        throw malformed;
      }
      container.innerHTML = "";
      paint(container, a, url, payload, options);
    })
    .catch((error: unknown) => {
      if (container.isConnected === false) return;
      // Cancelled with its view or replaced (not by the deadline): nothing to report.
      if (controller.signal.aborted && !(error instanceof Error && error.name === "TimeoutError")) return;
      container.innerHTML = "";
      if (error instanceof ApiError && error.status >= 400 && error.status < 500 && options.onRefused?.(error)) {
        return;
      }
      failureCard(container, a, url, error, () => renderTablePreview(container, a, url, options));
    });
}

/** The gzip tile: a package glyph and a label, never decoded bytes. */
export function compressedTile(): HTMLElement {
  const tile = el("span", "big thumb-compressed");
  tile.appendChild(iconEl("package", 28));
  tile.appendChild(el("span", "thumb-kind", previewT("tile.compressed")));
  return tile;
}
