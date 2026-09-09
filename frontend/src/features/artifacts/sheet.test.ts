import { afterEach, describe, expect, it, vi } from "vitest";
import { renderStructuredText, renderTextArtifact } from "./renderers";
import { setArtifactsFetch } from "./api";
import { renderSheet, sheetCap, sheetShape, SHEET_MAX_COLUMNS, SHEET_MAX_ROWS } from "./sheet";

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
