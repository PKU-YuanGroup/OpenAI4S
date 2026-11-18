import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreFields } from "../../stores/signal-field";
import { renderLegacyTable } from "../table/workbench";
import {
  abortPreviewReads,
  ApiError,
  beginPreviewRead,
  bytes,
  PREVIEW_READ_MAX_BYTES,
  setArtifactsFetch,
} from "./api";
import { compatibilityRendererDescriptor } from "./catalog";
import { filesT } from "./copy";
import {
  isCompressedArtifact,
  isWorkbookArtifact,
  previewFailureText,
  previewRecords,
  previewSummary,
  previewT,
  readTablePreview,
  renderTablePreview,
  TABLE_PREVIEW_TIMEOUT_MS,
  tablePreviewPath,
  type TablePreviewPayload,
} from "./table-preview";
import { pruneThumbReads, tileThumb } from "./thumbs";
import type { ArtifactRow } from "./types";

class LiveNode {
  children: LiveNode[] = [];
  parent: LiveNode | null = null;
  page = false;
  className = "";
  textContent = "";
  title = "";
  href = "";
  type = "";
  value = "";
  placeholder = "";
  html = "";
  attrs: Record<string, string> = {};
  dataset: Record<string, string> = {};
  onclick: (() => void) | null = null;
  oninput: (() => void) | null = null;
  onkeydown: ((event: unknown) => void) | null = null;
  tagName: string;
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
  }
  get isConnected(): boolean {
    return this.page || (!!this.parent && this.parent.isConnected);
  }
  get innerHTML(): string {
    return this.html;
  }
  set innerHTML(value: string) {
    for (const child of this.children) child.parent = null;
    this.children = [];
    this.html = value;
  }
  appendChild(child: LiveNode): LiveNode {
    child.parent = this;
    this.children.push(child);
    return child;
  }
  remove(): void {
    if (this.parent) this.parent.children = this.parent.children.filter((node) => node !== this);
    this.parent = null;
  }
  setAttribute(name: string, value: string): void {
    this.attrs[name] = value;
  }
  contains(other: LiveNode): boolean {
    for (let node: LiveNode | null = other; node; node = node.parent) if (node === this) return true;
    return false;
  }
}

const walk = (node: LiveNode): LiveNode[] => [node, ...node.children.flatMap(walk)];
const byTag = (root: LiveNode, tag: string) => walk(root).filter((node) => node.tagName === tag);
const byClass = (root: LiveNode, name: string) =>
  walk(root).filter((node) => node.className.split(" ").includes(name));
const texts = (root: LiveNode) => walk(root).map((node) => node.textContent).filter(Boolean);
const host = (node: LiveNode) => node as unknown as HTMLElement;

function page(): LiveNode {
  const root = new LiveNode("div");
  root.page = true;
  const container = root.appendChild(new LiveNode("div"));
  return container;
}

const row: ArtifactRow = {
  id: "a-1",
  filename: "bioreactor_big.csv",
  content_type: "text/csv",
  version_id: "v-1",
  _exactVersion: true,
};
const URL_EXACT = "/api/v1/artifacts/versions/v-1";

function csvHead(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    kind: "table",
    format: "csv",
    delimiter: ",",
    encoding: "utf-8",
    compression: null,
    inner_filename: null,
    sheets: [],
    sheet: null,
    columns: ["timestamp", "sensor", "ph"],
    row_numbers: null,
    rows: [
      ["2026-01-01 00:00:00", "19", "7.09"],
      ["2026-01-01 00:00:01", "2", "7.33"],
    ],
    total_rows: 900000,
    total_rows_lower_bound: null,
    total_columns: 3,
    truncated: true,
    size_bytes: 40 * 1024 * 1024,
    ...over,
  };
}

function workbook(sheet: string): Record<string, unknown> {
  return csvHead({
    format: "xlsx",
    delimiter: null,
    encoding: null,
    sheets: ["临床信息", "标志物与疗效"],
    sheet,
    columns: ["A", "B"],
    row_numbers: [1, 3],
    rows: [
      [sheet + " title", ""],
      ["患者编号", "年龄"],
    ],
    total_rows: 243,
    total_columns: 2,
    size_bytes: 19891,
  });
}

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });

function payload(over: Record<string, unknown> = {}): TablePreviewPayload {
  const parsed = readTablePreview(csvHead(over));
  if (!parsed) throw new Error("fixture is not a valid payload");
  return parsed;
}

describe("the preview-table contract the viewer relies on", () => {
  it("asks for the exact version and the chosen sheet, and latest only when nothing is pinned", () => {
    expect(tablePreviewPath({ id: "a/1", version_id: "v 1", _exactVersion: true }, "临床 信息")).toBe(
      "/artifacts/a%2F1/preview-table?version_id=v+1&sheet=%E4%B8%B4%E5%BA%8A+%E4%BF%A1%E6%81%AF",
    );
    expect(tablePreviewPath({ id: "a", version_id: "v1" })).toBe("/artifacts/a/preview-table");
  });

  it("accepts the documented shape and refuses a malformed one", () => {
    expect(readTablePreview(csvHead())?.total_rows).toBe(900000);
    expect(readTablePreview(csvHead({ rows: [["ok"], [1]] }))).toBeNull();
    expect(readTablePreview(csvHead({ total_rows: -1 }))).toBeNull();
    expect(readTablePreview(csvHead({ row_numbers: [1] }))).toBeNull();
    expect(readTablePreview(null)).toBeNull();
    expect(readTablePreview(csvHead({ total_rows: null, total_rows_lower_bound: 5 }))?.total_rows).toBeNull();
  });

  it("keys rows like the client parser and leads a workbook's rows with their sheet row number", () => {
    const records = previewRecords(
      payload({ columns: ["gene", "gene", " score "], rows: [["TP53", "x", "1", "extra"]], total_columns: 4 }),
    );
    expect(records).toEqual([{ gene: "TP53", "gene.1": "x", score: "1", "3": "extra" }]);
    const sheet = previewRecords(readTablePreview(workbook("临床信息"))!);
    expect(Object.keys(sheet[0]!)).toEqual(["#", "A", "B"]);
    expect(sheet.map((record) => record["#"])).toEqual(["1", "3"]);
  });

  it("describes the whole file, not the rows it shows", () => {
    const big = payload();
    expect(previewSummary(big, { reason: "too_large" })).toBe(
      previewT("preview.rows.exact", (900000).toLocaleString(), "3") +
        previewT("preview.shown.rows", "2") +
        previewT("preview.reason.tooLarge", bytes(40 * 1024 * 1024), bytes(PREVIEW_READ_MAX_BYTES)),
    );
    // A count stopped by its bound is a lower bound, never an exact total.
    const bounded = payload({ total_rows: null, total_rows_lower_bound: 5_000_000 });
    expect(previewSummary(bounded)).toContain(previewT("preview.rows.atLeast", (5_000_000).toLocaleString(), "3"));
    // Everything shown: no "first N rows".
    const whole = payload({ total_rows: 2, truncated: false });
    expect(previewSummary(whole)).toBe(previewT("preview.rows.exact", "2", "3"));
    const gz = payload({ compression: "gzip", inner_filename: "scrna_counts.csv", columns: ["a"], total_columns: 1201 });
    expect(previewSummary(gz)).toBe(
      previewT("preview.rows.exact", (900000).toLocaleString(), (1201).toLocaleString()) +
        previewT("preview.shown.both", "2", "1") +
        previewT("preview.reason.gzip", "scrna_counts.csv"),
    );
  });

  it("names each refusal instead of a bare HTTP status", () => {
    const gz: ArtifactRow = { id: "g", filename: "small.txt.gz" };
    const refusal = (code: string, status = 415) => new ApiError({ error: "x", code }, status);
    expect(previewFailureText(refusal("not_tabular"), gz)).toBe(previewT("preview.error.not_tabular.gzip"));
    expect(previewFailureText(refusal("not_tabular"), row)).toBe(previewT("preview.error.not_tabular"));
    expect(previewFailureText(refusal("unsupported_format"), row)).toBe(previewT("preview.error.unsupported_format"));
    expect(previewFailureText(refusal("too_large_to_inspect", 413), row)).toBe(
      previewT("preview.error.too_large_to_inspect"),
    );
    expect(previewFailureText(refusal("invalid_file", 422), row)).toBe(previewT("preview.error.invalid_file"));
    expect(previewFailureText(new ApiError({ error: "boom" }, 500), row)).toBe(filesT("viewer.read.failedStatus", 500));
  });

  it("recognises gzip and workbooks by name or stored type, whatever the inner name says", () => {
    expect(isCompressedArtifact({ filename: "x.csv.gz", content_type: "text/csv" })).toBe(true);
    expect(isCompressedArtifact({ filename: "data.csv", content_type: "application/gzip" })).toBe(true);
    expect(isCompressedArtifact({ filename: "x.csv", content_type: "text/csv" })).toBe(false);
    expect(isWorkbookArtifact({ filename: "cohort.xlsx" })).toBe(true);
    expect(isWorkbookArtifact({ filename: "old.xls", content_type: "application/vnd.ms-excel" })).toBe(false);
    const id = (a: ArtifactRow) => compatibilityRendererDescriptor(a).renderer?.renderer_id;
    expect(id({ id: "a", filename: "scrna_counts.csv.gz", content_type: "text/csv" })).toBe("compressed");
    expect(id({ id: "a", filename: "data.csv", content_type: "application/gzip" })).toBe("compressed");
    expect(id({ id: "a", filename: "cohort.xlsx" })).toBe("spreadsheet");
    expect(id({ id: "a", filename: "old.xls" })).toBe("download");
    expect(id({ id: "a", filename: "plain.csv" })).toBe("table");
  });
});

describe("renderTablePreview", () => {
  let calls: string[];
  let reply: (url: string) => Promise<Response>;

  beforeEach(() => {
    resetStoreFields();
    abortPreviewReads();
    calls = [];
    reply = async () => json(csvHead());
    vi.stubGlobal("document", { createElement: (tag: string) => new LiveNode(tag) });
    setArtifactsFetch((url, init) => {
      calls.push(url);
      if (init?.signal?.aborted) return Promise.reject(new DOMException("aborted", "AbortError"));
      return new Promise<Response>((resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        reply(url).then(resolve, reject);
      });
    });
  });
  afterEach(() => {
    vi.useRealTimers();
    abortPreviewReads();
    pruneThumbReads();
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
    resetStoreFields();
  });

  it("draws the head as an interactive sheet with the whole file's size and the exact download", async () => {
    const container = page();
    renderTablePreview(host(container), row, URL_EXACT, { reason: "too_large" });
    expect(texts(container)).toContain(previewT("preview.loading"));
    await vi.waitFor(() => expect(byTag(container, "TABLE")).toHaveLength(1));
    expect(calls).toEqual(["/api/v1/artifacts/a-1/preview-table?version_id=v-1"]);
    expect(byTag(container, "TH").map((node) => node.textContent)).toEqual(["timestamp", "sensor", "ph"]);
    const note = byClass(container, "renderer-completeness")[0]!;
    expect(note.attrs.role).toBe("status");
    expect(note.attrs["data-state"]).toBe("partial");
    expect(note.textContent).toBe(previewSummary(payload(), { reason: "too_large" }));
    expect(byTag(container, "INPUT")).toHaveLength(1); // the sheet's filter
    expect(byTag(container, "A").map((node) => node.href)).toEqual([URL_EXACT]);
    expect(texts(container)).not.toContain(previewT("preview.loading"));
  });

  it("switches sheets through the route, and a slower answer for an older sheet never paints", async () => {
    const pending = new Map<string, (response: Response) => void>();
    reply = (url) =>
      url.includes("sheet=")
        ? new Promise<Response>((resolve) => pending.set(new URL(url, "http://x").searchParams.get("sheet")!, resolve))
        : Promise.resolve(json(workbook("临床信息")));
    const xlsx: ArtifactRow = { ...row, filename: "cohort_两张表.xlsx", content_type: null };
    const container = page();
    renderTablePreview(host(container), xlsx, URL_EXACT);
    await vi.waitFor(() => expect(byTag(container, "TABLE")).toHaveLength(1));
    const tabs = walk(container).filter((node) => node.attrs.role === "tab");
    expect(tabs.map((tab) => [tab.textContent, tab.attrs["aria-selected"]])).toEqual([
      ["临床信息", "true"],
      ["标志物与疗效", "false"],
    ]);
    expect(byTag(container, "TH").map((node) => node.textContent)).toEqual(["#", "A", "B"]);
    tabs[1]!.onclick?.();
    expect(calls.at(-1)).toContain("sheet=%E6%A0%87%E5%BF%97%E7%89%A9%E4%B8%8E%E7%96%97%E6%95%88");
    // Re-render for the first sheet before the second answers: the older answer is stale.
    renderTablePreview(host(container), xlsx, URL_EXACT, { sheet: "临床信息" });
    pending.get("临床信息")!(json(workbook("临床信息")));
    await vi.waitFor(() => expect(byTag(container, "TABLE")).toHaveLength(1));
    pending.get("标志物与疗效")?.(json(workbook("标志物与疗效")));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(texts(container)).toContain("临床信息 title");
    expect(texts(container)).not.toContain("标志物与疗效 title");
  });

  it("states a refusal plainly, without a pointless retry, and keeps the download", async () => {
    reply = async () => json({ error: "not a table", code: "not_tabular" }, 415);
    const gz: ArtifactRow = { ...row, filename: "small.txt.gz", content_type: "application/gzip" };
    const container = page();
    renderTablePreview(host(container), gz, URL_EXACT);
    await vi.waitFor(() => expect(byClass(container, "renderer-fallback")).toHaveLength(1));
    expect(texts(container)).toContain(previewT("preview.error.not_tabular.gzip"));
    expect(byTag(container, "BUTTON")).toHaveLength(0);
    expect(byTag(container, "A").map((node) => node.href)).toEqual([URL_EXACT]);
  });

  it("offers a retry after a server failure, and the retry can succeed", async () => {
    reply = async () => json({ error: "boom" }, 500);
    const container = page();
    renderTablePreview(host(container), row, URL_EXACT);
    await vi.waitFor(() => expect(byClass(container, "renderer-fallback")).toHaveLength(1));
    reply = async () => json(csvHead());
    byTag(container, "BUTTON")[0]!.onclick?.();
    await vi.waitFor(() => expect(byTag(container, "TABLE")).toHaveLength(1));
    expect(byClass(container, "renderer-fallback")).toHaveLength(0);
  });

  it("ends a stalled request at its deadline instead of loading forever", async () => {
    vi.useFakeTimers();
    reply = () => new Promise<Response>(() => {});
    const container = page();
    renderTablePreview(host(container), row, URL_EXACT);
    await vi.advanceTimersByTimeAsync(TABLE_PREVIEW_TIMEOUT_MS + 10);
    expect(texts(container)).toContain(filesT("viewer.read.timeout"));
    expect(byTag(container, "BUTTON")).toHaveLength(1);
  });

  it("is cancelled with its view and then paints nothing", async () => {
    let seen: AbortSignal | undefined;
    setArtifactsFetch((url, init) => {
      calls.push(url);
      seen = init?.signal ?? undefined;
      return new Promise<Response>(() => {});
    });
    const root = new LiveNode("div");
    root.page = true;
    const container = root.appendChild(new LiveNode("div"));
    beginPreviewRead(host(root));
    renderTablePreview(host(container), row, URL_EXACT);
    expect(seen?.aborted).toBe(false);
    abortPreviewReads(host(root));
    expect(seen?.aborted).toBe(true);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(byClass(container, "renderer-fallback")).toHaveLength(0);
  });

  it("the default table viewer goes straight to the head when the row is over the read budget", async () => {
    const container = page();
    renderLegacyTable(host(container), { ...row, size_bytes: PREVIEW_READ_MAX_BYTES + 1 }, URL_EXACT);
    await vi.waitFor(() => expect(byTag(container, "TABLE")).toHaveLength(1));
    // The doomed full read was never started.
    expect(calls).toEqual(["/api/v1/artifacts/a-1/preview-table?version_id=v-1"]);
    expect(texts(container).join(" ")).toContain(bytes(PREVIEW_READ_MAX_BYTES));
  });

  it("the default table viewer asks the server about binary bytes, and keeps its card for a non-table", async () => {
    const gzipAsText = "\u001f\u008b\u0008\u0000" + "\u0000\u0001\u0002\u0003".repeat(400);
    reply = async (url) =>
      url.includes("/preview-table")
        ? json(csvHead({ compression: "gzip", inner_filename: "data.csv" }))
        : new Response(gzipAsText);
    const container = page();
    renderLegacyTable(host(container), { ...row, filename: "data.csv", size_bytes: 1600 }, URL_EXACT);
    await vi.waitFor(() => expect(byTag(container, "TABLE")).toHaveLength(1));
    expect(texts(container).join(" ")).toContain(previewT("preview.reason.gzip", "data.csv"));

    reply = async (url) =>
      url.includes("/preview-table") ? json({ error: "binary", code: "not_tabular" }, 415) : new Response(gzipAsText);
    const other = page();
    renderLegacyTable(host(other), { ...row, filename: "blob.csv", size_bytes: 1600 }, URL_EXACT);
    await vi.waitFor(() => expect(byClass(other, "download-artifact")).toHaveLength(1));
    expect(byClass(other, "renderer-fallback")).toHaveLength(0);
  });

  it("a gzip tile is a labelled glyph and never reads the compressed bytes as text", async () => {
    const tile = tileThumb({ id: "g", filename: "scrna_counts.csv.gz", content_type: "text/csv", version_id: "v1" });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(calls).toEqual([]);
    expect(texts(tile as unknown as LiveNode)).toContain(previewT("tile.compressed"));
  });
});
