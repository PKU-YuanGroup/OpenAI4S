import { afterEach, describe, expect, it, vi } from "vitest";
import { renderStructuredText, renderTextArtifact } from "./renderers";
import { setArtifactsFetch } from "./api";
import {
  compareSheetCells,
  filterSheetRows,
  renderSheet,
  sheetCap,
  sheetShape,
  sheetShapeText,
  SHEET_MAX_CELLS,
  SHEET_MAX_COLUMNS,
  SHEET_MAX_ROWS,
} from "./sheet";

describe("renderSheet shape (app.js:8771-8802)", () => {
  it("counts the union of keys, not just rows[0]", () => {
    expect(sheetShape([{ a: 1 }, { a: 2, late: 3 }])).toEqual({
      rows: 2,
      columns: 2,
      keys: ["a", "late"],
    });
  });

  it("caps at 5000 rows and 100 columns and reports what was hidden", () => {
    const tall: Record<string, unknown>[] = [];
    for (let r = 0; r < 5001; r++) tall.push({ a: r, b: r, c: r });
    const tallCap = sheetCap(tall);
    expect(tallCap.safeRows).toHaveLength(SHEET_MAX_ROWS);
    expect(tallCap.hiddenRows).toBe(1);
    expect(tallCap.shape.rows).toBe(5001);

    const wide: Record<string, unknown> = {};
    for (let c = 0; c < 101; c++) wide["c" + c] = c;
    const wideCap = sheetCap([wide, { ...wide }]);
    expect(wideCap.columns).toHaveLength(SHEET_MAX_COLUMNS);
    expect(wideCap.hiddenColumns).toBe(1);
    expect(wideCap.shape.columns).toBe(101);
  });
});

class SheetNode {
  children: SheetNode[] = [];
  isConnected = true;
  textContent = "";
  className = "";
  href = "";
  attrs: Record<string, string> = {};
  onclick: (() => void) | null = null;
  constructor(public tag: string) {}
  set innerHTML(_value: string) {
    throw new Error("source must be rendered as text");
  }
  appendChild(child: SheetNode) {
    this.children.push(child);
    return child;
  }
  setAttribute(key: string, value: string) {
    this.attrs[key] = value;
  }
  remove() {}
}
function setupSheetDom() {
  vi.stubGlobal("document", {
    createElement: (tag: string) => new SheetNode(tag),
    createTextNode: (text: string) => Object.assign(new SheetNode("#text"), { textContent: text }),
  });
  return new SheetNode("div");
}
const childrenByTag = (node: SheetNode, tag: string): SheetNode[] => [
  ...(node.tag === tag ? [node] : []),
  ...node.children.flatMap((child) => childrenByTag(child, tag)),
];
afterEach(() => {
  vi.unstubAllGlobals();
  setArtifactsFetch(null);
});

it("renders late fields, zeros and false using safe text and blank missing cells", () => {
  const host = setupSheetDom();
  const rows = JSON.parse(
    '[{}, {"a":0,"late":false,"constructor":"<img src=x onerror=alert(1)>","__proto__":"text"}]',
  );
  renderSheet(host as unknown as HTMLElement, rows);
  expect(childrenByTag(host, "th").map((n) => n.textContent)).toEqual([
    "a", "late", "constructor", "__proto__",
  ]);
  expect(childrenByTag(host, "td").map((n) => n.textContent)).toEqual([
    "", "", "", "", "0", "false", "<img src=x onerror=alert(1)>", "text",
  ]);
});

it("takes stable columns from the full schema including the first hidden row", () => {
  const rows = Array.from({ length: 5000 }, (_, i) => ({ a: i })) as Record<string, unknown>[];
  rows.push({ a: 5000, hidden_row_key: 1 });
  const cap = sheetCap(rows);
  expect(cap.columns).toEqual(["a", "hidden_row_key"]);
  expect(cap.hiddenRows).toBe(1);
  expect(cap.hiddenColumns).toBe(0);
  expect(cap.safeRows).toHaveLength(5000);
  const wide = [{}, Object.fromEntries(Array.from({ length: 101 }, (_, i) => ["k" + i, i]))];
  expect(sheetCap(wide).columns).toEqual(Array.from({ length: 100 }, (_, i) => "k" + i));
  expect(sheetCap(wide).hiddenColumns).toBe(1);
});

it("preserves mixed JSON as raw text and expands fetched long source without another request", () => {
  const host = setupSheetDom();
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const source = JSON.stringify([{ text: "<b>unsafe</b>" + "x".repeat(300010) }, null, "END"]);
  renderStructuredText(
    host as unknown as HTMLElement,
    { id: "a", filename: "mixed.json", version_id: "v1", _exactVersion: true },
    source,
  );
  const pre = childrenByTag(host, "pre")[0];
  expect(pre?.textContent).toBe(source.slice(0, 300000));
  const expand = childrenByTag(host, "button")[0];
  expect(expand).toBeDefined();
  expect(childrenByTag(host, "a")[0]?.href).toBe("/api/v1/artifacts/versions/v1");
  expand?.onclick?.();
  expect(pre?.textContent).toBe(source);
  expect(fetch).not.toHaveBeenCalled();
});

it("routes fetched long mixed JSON to source preview and expands without another GET", async () => {
  const host = setupSheetDom();
  const source = JSON.stringify([{ text: "x".repeat(300010) }, null, "END"]);
  const url = "/api/v1/artifacts/versions/v1";
  const get = vi.fn(async () => new Response(source, { status: 200 }));
  setArtifactsFetch(get);
  renderTextArtifact(
    host as unknown as HTMLElement,
    { id: "a", filename: "mixed.json", version_id: "v1", _exactVersion: true },
    url,
  );
  await vi.waitFor(() => expect(childrenByTag(host, "pre")).toHaveLength(1));
  const pre = childrenByTag(host, "pre")[0];
  expect(pre?.textContent).toBe(source.slice(0, 300000));
  const expand = childrenByTag(host, "button")[0];
  expect(expand?.attrs["aria-expanded"]).toBe("false");
  expand?.onclick?.();
  expect(pre?.textContent).toBe(source);
  expect(expand?.attrs["aria-expanded"]).toBe("true");
  expect(childrenByTag(host, "a")[0]?.href).toBe(url);
  expect(get.mock.calls).toHaveLength(1);
});

it("shows complete mixed source for every supported JSON wrapper", () => {
  for (const wrapper of [null, "rows", "data", "candidates", "items"]) {
    for (const invalid of [null, [], 0, false, "text"]) {
      const host = setupSheetDom();
      const rows = [{ html: "<b>unsafe</b>" }, invalid, { last: 1 }];
      const source = JSON.stringify(wrapper ? { [wrapper]: rows } : rows);
      renderStructuredText(
        host as unknown as HTMLElement, { id: "a", filename: "mixed.json" }, source,
      );
      expect(childrenByTag(host, "pre")[0]?.textContent).toBe(source);
      expect(childrenByTag(host, "table")).toHaveLength(0);
    }
  }
});

it("a .json name over binary bytes gets the download tile, not 300k of garbage source", () => {
  // The tile carries an SVG icon, so this DOM permits innerHTML on the icon span only.
  class TileNode extends SheetNode {
    set innerHTML(value: string) {
      if (this.tag !== "span") throw new Error("only the icon span may use innerHTML");
      this.textContent = value;
    }
  }
  vi.stubGlobal("document", { createElement: (tag: string) => new TileNode(tag) });
  const host = new TileNode("div");
  const bytes = String.fromCharCode(0, 1, 2, 3, 0xff, 0xfe).repeat(200);
  renderStructuredText(
    host as unknown as HTMLElement,
    { id: "a", filename: "payload.json", content_type: "application/json", version_id: "v1", _exactVersion: true },
    bytes,
  );
  expect(childrenByTag(host, "pre")).toHaveLength(0);
  expect(childrenByTag(host, "button")).toHaveLength(0);
  expect(childrenByTag(host, "a")[0]?.href).toBe("/api/v1/artifacts/versions/v1");
  expect(childrenByTag(host, "strong")[0]?.textContent).toBe("payload.json");
  // A parse failure on ordinary text is still source, never a download tile.
  const text = setupSheetDom();
  renderStructuredText(text as unknown as HTMLElement, { id: "a", filename: "notes.json" }, "not json at all");
  expect(childrenByTag(text, "pre")[0]?.textContent).toBe("not json at all");
});

describe("interactive default table (ART-3)", () => {
  class LiveNode extends SheetNode {
    parent: LiveNode | null = null;
    value = "";
    type = "";
    placeholder = "";
    dataset: Record<string, string> = {};
    oninput: (() => void) | null = null;
    onkeydown: ((event: { key: string; preventDefault: () => void }) => void) | null = null;
    appendChild(child: LiveNode) {
      child.parent = this;
      return super.appendChild(child) as LiveNode;
    }
    remove() {
      if (this.parent) this.parent.children = this.parent.children.filter((node) => node !== this);
      this.parent = null;
    }
  }
  function mount(rows: Record<string, unknown>[]) {
    vi.stubGlobal("document", {
      createElement: (tag: string) => new LiveNode(tag),
      createTextNode: (text: string) => Object.assign(new LiveNode("#text"), { textContent: text }),
    });
    const host = new LiveNode("div");
    renderSheet(host as unknown as HTMLElement, rows);
    const all = (tag: string) => childrenByTag(host, tag) as LiveNode[];
    const column = (index: number) =>
      all("tr").slice(1).map((tr) => (tr.children[index] as LiveNode | undefined)?.textContent);
    return { host, all, column };
  }
  const rows = [
    { gene: "TP53", score: "10" },
    { gene: "BRCA1", score: "2" },
    { gene: "egfr", score: "" },
    { gene: "MYC", score: "2.5" },
  ];

  it("sorts numbers numerically, keeps blanks last, and cycles back to the file's order", () => {
    const { all, column } = mount(rows);
    all("th")[1]!.onclick?.();
    expect(column(1)).toEqual(["2", "2.5", "10", ""]);
    // A repaint draws new headers; the sorted one says so.
    expect(all("th")[1]!.attrs["aria-sort"]).toBe("ascending");
    expect(all("th")[1]!.dataset.sort).toBe("asc");
    all("th")[1]!.onclick?.();
    expect(column(1)).toEqual(["10", "2.5", "2", ""]);
    all("th")[1]!.onclick?.();
    expect(column(0)).toEqual(["TP53", "BRCA1", "egfr", "MYC"]);
    all("th")[0]!.onkeydown?.({ key: "Enter", preventDefault() {} });
    expect(column(0)).toEqual(["BRCA1", "egfr", "MYC", "TP53"]);
  });

  it("filters across columns or by column:value, and says how many rows match", async () => {
    const { all, column } = mount(rows);
    const input = all("input")[0]!;
    input.value = "score:2";
    input.oninput?.();
    await vi.waitFor(() => expect(column(0)).toEqual(["BRCA1", "MYC"]));
    expect(all("span").find((n) => n.className === "sheet-status")?.textContent).toContain("2");
    input.value = "EGF";
    input.oninput?.();
    await vi.waitFor(() => expect(column(0)).toEqual(["egfr"]));
    input.value = "nothing-like-this";
    input.oninput?.();
    await vi.waitFor(() => expect(all("tr")).toHaveLength(1));
  });

  it("shows every column of a wide table on request, within the same cell budget", () => {
    const wide = Array.from({ length: 3 }, (_, r) =>
      Object.fromEntries(Array.from({ length: 121 }, (_, c) => ["c" + c, String(r * 1000 + c)])),
    );
    const { all } = mount(wide);
    expect(all("th")).toHaveLength(SHEET_MAX_COLUMNS);
    const toggle = all("button").find((n) => n.textContent.includes("121"))!;
    const note = all("div").find((n) => n.className === "renderer-note")!;
    const allShown = sheetShapeText({ rows: 3, columns: 121 }, 3, 121);
    expect(note.textContent).not.toBe(allShown);
    expect(note.textContent).toBe(sheetShapeText({ rows: 3, columns: 121 }, 3, 100));
    toggle.onclick?.();
    expect(all("th")).toHaveLength(121);
    expect(all("th")[120]?.textContent).toBe("c120");
    // Every column is drawn now, so the note stops claiming 21 are hidden.
    expect(note.textContent).toBe(allShown);
    toggle.onclick?.();
    expect(all("th")).toHaveLength(SHEET_MAX_COLUMNS);
    expect(SHEET_MAX_CELLS).toBe(SHEET_MAX_ROWS * SHEET_MAX_COLUMNS);
  });

  it("pure helpers: comparison and filtering", () => {
    expect(compareSheetCells("9", "10", 1)).toBeLessThan(0);
    expect(compareSheetCells("", "1", -1)).toBeGreaterThan(0);
    expect(compareSheetCells("a2", "a10", 1)).toBeLessThan(0);
    expect(filterSheetRows(rows, ["gene", "score"], "gene:tp")).toEqual([rows[0]]);
    expect(filterSheetRows(rows, ["gene", "score"], "  ")).toBe(rows);
    expect(filterSheetRows(rows, ["gene", "score"], "unknown:x")).toEqual([]);
  });
});
