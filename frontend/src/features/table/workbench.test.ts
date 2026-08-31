import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setArtifactsFetch } from "../artifacts/api";
import { jsonResponse } from "../artifacts/http-stub";
import { planTableViewer, tableCatalogPosture } from "./catalog";
import { clampHistogram, MAX_TABLE_PROFILE_BINS, readApproximate } from "./histogram";
import {
  PROFILE_FORBIDDEN,
  tableExportPath,
  tableExportSearch,
  tableProfilePath,
  tableProfileSearch,
} from "./query";
import type { TableProfile, TableProfileColumn } from "./types";
import { renderTableArtifact } from "./workbench";
import { renderTableZones } from "./zones";

class FakeClassList {
  readonly tokens = new Set<string>();
  constructor(initial?: string) {
    if (initial) for (const t of initial.split(/\s+/).filter(Boolean)) this.tokens.add(t);
  }
  add(...names: string[]): void {
    for (const n of names) this.tokens.add(n);
  }
  contains(name: string): boolean {
    return this.tokens.has(name);
  }
  get value(): string {
    return [...this.tokens].join(" ");
  }
}

class FakeEl {
  tagName: string;
  classList: FakeClassList;
  children: FakeEl[] = [];
  parent: FakeEl | null = null;
  dataset: Record<string, string> = {};
  style: Record<string, string> = {};
  attrs: Record<string, string> = {};
  _text = "";
  _html = "";
  ownerDocument: FakeDoc;
  isConnected = true;
  disabled = false;
  placeholder = "";
  value = "";
  href = "";
  title = "";
  onclick: ((ev?: unknown) => void) | null = null;
  onchange: ((ev?: unknown) => void) | null = null;

  constructor(tag: string, doc: FakeDoc) {
    this.tagName = tag.toUpperCase();
    this.ownerDocument = doc;
    this.classList = new FakeClassList();
  }

  get className(): string {
    return this.classList.value;
  }
  set className(value: string) {
    this.classList = new FakeClassList(value);
  }

  get textContent(): string {
    if (this.children.length) return this.children.map((c) => c.textContent).join("");
    return this._text;
  }
  set textContent(value: string) {
    this.children = [];
    this._text = value == null ? "" : String(value);
  }

  get innerHTML(): string {
    return this._html || this.textContent;
  }
  set innerHTML(value: string) {
    this._html = value;
    this.children = [];
    // To a fixed point: one pass leaves `<<b>script>` as `<script>`, so a
    // test asserting on `.textContent` would read markup this double claims
    // to have stripped. Nothing here is a sanitiser -- it only has to not
    // lie about what it removed.
    let text = value,
      previous: string;
    do {
      previous = text;
      text = text.replace(/<[^>]+>/g, "");
    } while (text !== previous);
    this._text = text;
  }

  appendChild(child: FakeEl): FakeEl {
    child.parent = this;
    this.children.push(child);
    return child;
  }

  setAttribute(name: string, value: string): void {
    this.attrs[name] = String(value);
    if (name === "href") this.href = String(value);
    if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value);
  }
  getAttribute(name: string): string | null {
    if (name === "href") return this.href || this.attrs.href || null;
    const value = this.attrs[name];
    return value != null ? value : null;
  }

  querySelector(sel: string): FakeEl | null {
    return this.querySelectorAll(sel)[0] || null;
  }
  querySelectorAll(sel: string): FakeEl[] {
    const out: FakeEl[] = [];
    const walk = (node: FakeEl): void => {
      for (const child of node.children) {
        if (matchSel(child, sel)) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
}

function matchSel(node: FakeEl, sel: string): boolean {
  const parts = sel.split(".");
  const tag = parts[0];
  const classes = parts.slice(1).filter(Boolean);
  if (tag && node.tagName !== tag.toUpperCase()) return false;
  return classes.every((cls) => node.classList.contains(cls));
}

class FakeDoc {
  body: FakeEl;
  constructor() {
    this.body = new FakeEl("body", this);
  }
  createElement(tag: string): FakeEl {
    return new FakeEl(tag, this);
  }
  createTextNode(text: string): FakeEl {
    const node = new FakeEl("#text", this);
    node.textContent = text;
    return node;
  }
}

function installDom(): FakeEl {
  const doc = new FakeDoc();
  vi.stubGlobal("document", doc);
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  return host;
}

function numericBins(count: number, lo = 0, hi = 50): TableProfileColumn["histogram"] {
  if (count <= 1) return [{ start: lo, end: hi, count: 1 }];
  const width = (hi - lo) / count;
  const bins = [];
  for (let i = 0; i < count; i++) {
    bins.push({ start: lo + i * width, end: lo + (i + 1) * width, count: 1 });
  }
  return bins;
}

function profileFixture(partial: Partial<TableProfile> = {}): TableProfile {
  return {
    artifact_id: "art-1",
    version_id: "v1",
    checksum: "abc",
    filtered_rows: 51,
    approximate: false,
    schema_version: 1,
    columns: [
      {
        name: "n",
        type: "integer",
        missing: 0,
        unique: 51,
        min: 0,
        max: 50,
        mean: 25,
        histogram: numericBins(50, 0, 50),
      },
      {
        name: "label",
        type: "text",
        missing: 1,
        unique: 3,
        min: null,
        max: null,
        mean: null,
        histogram: [
          { value: "a", count: 10 },
          { value: "b", count: 5 },
        ],
      },
    ],
    filters: {},
    ...partial,
  };
}

const WORKBENCH_CAPS = ["view", "sort", "filter", "profile", "export"];

async function flush(): Promise<void> {
  for (let i = 0; i < 16; i++) await Promise.resolve();
}

describe("B-07 profile/export query contract", () => {
  it("profile requires version_id and never sends sort/dir/offset/limit", () => {
    expect(tableProfileSearch({ versionId: "", filters: { name: "a" } })).toBeNull();
    const search = tableProfileSearch({ versionId: "v1", filters: { name: "Al", n: "7" } });
    expect(search).not.toBeNull();
    expect(search!.get("version_id")).toBe("v1");
    expect(search!.get("q_name")).toBe("Al");
    expect(search!.get("q_n")).toBe("7");
    for (const name of PROFILE_FORBIDDEN) expect(search!.has(name)).toBe(false);
    const path = tableProfilePath("art-1", search!);
    expect(path.startsWith("/artifacts/art-1/table/profile?")).toBe(true);
    expect(path).toContain("version_id=v1");
    expect(path).not.toMatch(/[?&](sort|dir|offset|limit)=/);
  });

  it("export requires version_id, keeps sort/dir/q_, refuses offset/limit", () => {
    expect(tableExportSearch({ versionId: "", filters: {} })).toBeNull();
    const search = tableExportSearch({
      versionId: "v1",
      sort: "n",
      dir: "desc",
      filters: { name: "Al" },
    });
    expect(search!.get("version_id")).toBe("v1");
    expect(search!.get("sort")).toBe("n");
    expect(search!.get("dir")).toBe("desc");
    expect(search!.get("q_name")).toBe("Al");
    expect(search!.has("offset")).toBe(false);
    expect(search!.has("limit")).toBe(false);
    const path = tableExportPath("art-1", search!);
    expect(path).toContain("/table/export.csv?");
    expect(path).toContain("version_id=v1");
    expect(path).not.toMatch(/[?&](offset|limit)=/);
  });
});

describe("histogram bounds + approximate pass-through", () => {
  it("clamps bins at 50 and keeps first-start / last-end", () => {
    const fifty = numericBins(50, 0, 50);
    const kept = clampHistogram(fifty);
    expect(kept.bins).toHaveLength(MAX_TABLE_PROFILE_BINS);
    expect(kept.clipped).toBe(false);
    expect(kept.bins[0]).toMatchObject({ start: 0 });
    expect(kept.bins[kept.bins.length - 1]).toMatchObject({ end: 50 });

    const overflow = numericBins(51, 0, 51);
    const clipped = clampHistogram(overflow);
    expect(clipped.bins.length).toBe(MAX_TABLE_PROFILE_BINS);
    expect(clipped.clipped).toBe(true);
    expect(clipped.bins[0]).toMatchObject({ start: 0 });
  });

  it("treats only true/'true' as approximate", () => {
    expect(readApproximate({ approximate: true })).toBe(true);
    expect(readApproximate({ approximate: "true" })).toBe(true);
    expect(readApproximate({ approximate: false })).toBe(false);
    expect(readApproximate({ approximate: "false" })).toBe(false);
    expect(readApproximate({ approximate: 1 })).toBe(false);
    expect(readApproximate({})).toBe(false);
  });
});

describe("three-zone rendering", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setArtifactsFetch(null);
  });

  it("renders Schema, Distribution, Export; approximate is explicit; histogram stays ≤50", () => {
    const host = installDom();
    const posture = tableCatalogPosture({ capabilities: WORKBENCH_CAPS }, { workbenchOn: true });
    const plan = planTableViewer(posture);
    const profile = profileFixture({
      approximate: true,
      columns: [
        {
          name: "n",
          type: "integer",
          missing: 0,
          unique: 2,
          min: 0,
          max: 50,
          mean: 25,
          histogram: numericBins(51, 0, 51),
        },
        {
          name: "label",
          type: "text",
          missing: 0,
          unique: 2,
          min: null,
          max: null,
          mean: null,
          histogram: [{ value: "a", count: 3 }],
        },
      ],
    });
    renderTableZones(host as unknown as HTMLElement, profile, posture, {
      plan,
      exportHref: "/api/v1/artifacts/art-1/table/export.csv?version_id=v1&q_name=Al",
      versionId: "v1",
    });

    expect(host.querySelector(".wb-table-schema")).toBeTruthy();
    expect(host.querySelector(".wb-table-distribution")).toBeTruthy();
    expect(host.querySelector(".wb-table-export-zone")).toBeTruthy();

    const banner = host.querySelector(".wb-table-approx");
    expect(banner).toBeTruthy();
    expect(banner!.dataset.approximate).toBe("true");
    expect(banner!.textContent).toMatch(/近似|Approximate/i);
    expect(host.querySelector(".wb-table-zones")!.dataset.approximate).toBe("true");
    const unique = host.querySelector(".wb-table-unique-approx");
    expect(unique).toBeTruthy();
    expect(unique!.textContent).toMatch(/近似|approx/i);
    expect(unique!.textContent).not.toBe("2");

    const nCard = host.querySelectorAll(".wb-table-dist-col")[0];
    expect(nCard).toBeTruthy();
    expect(nCard!.dataset.column).toBe("n");
    const bars = nCard!.querySelectorAll(".wb-hist-bar");
    expect(bars.length).toBeGreaterThan(0);
    expect(bars.length).toBeLessThanOrEqual(MAX_TABLE_PROFILE_BINS);
    const chart = nCard!.querySelector(".wb-hist");
    expect(chart!.dataset.clipped).toBe("true");
    expect(Number(chart!.dataset.bins)).toBeLessThanOrEqual(MAX_TABLE_PROFILE_BINS);
    expect(bars[0]!.dataset.start).toBe("0");

    const link = host.querySelector("a.wb-table-export-link");
    expect(link).toBeTruthy();
    expect(link!.getAttribute("href")).toContain("/table/export.csv");
    expect(link!.getAttribute("href")).toContain("version_id=v1");
    expect(host.querySelector(".wb-table-parquet")).toBeNull();
  });

  it("does not show 近似 when approximate is false, and keeps histogram min/max bounds", () => {
    const host = installDom();
    const posture = tableCatalogPosture({ capabilities: WORKBENCH_CAPS }, { workbenchOn: true });
    renderTableZones(host as unknown as HTMLElement, profileFixture({ approximate: false }), posture, {
      plan: planTableViewer(posture),
      exportHref: "/api/v1/artifacts/art-1/table/export.csv?version_id=v1",
      versionId: "v1",
    });
    expect(host.querySelector(".wb-table-approx")).toBeNull();
    expect(host.querySelector(".wb-table-zones")!.dataset.approximate).toBe("false");
    expect(host.querySelector(".wb-table-unique-approx")).toBeNull();
    const unique = host.querySelector(".wb-table-unique");
    expect(unique!.textContent).toBe("51");
    const chart = host.querySelector(".wb-hist");
    expect(chart!.dataset.start).toBe("0");
    expect(chart!.dataset.end).toBe("50");
    expect(chart!.dataset.clipped).toBeUndefined();
    expect(Number(chart!.dataset.bins)).toBe(50);
  });

  it("claims parquet only when the catalog advertised it", () => {
    const host = installDom();
    const posture = tableCatalogPosture(
      { capabilities: [...WORKBENCH_CAPS, "parquet"] },
      { workbenchOn: true },
    );
    renderTableZones(host as unknown as HTMLElement, profileFixture(), posture, {
      plan: planTableViewer(posture),
      exportHref: "/api/v1/artifacts/art-1/table/export.csv?version_id=v1",
      versionId: "v1",
    });
    expect(host.querySelector(".wb-table-parquet")).toBeTruthy();
  });
});

describe("flag=0 fallback + workbench profile fetch", () => {
  beforeEach(() => {
    installDom();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    setArtifactsFetch(null);
  });

  it("flag=0 uses the legacy sheet and never hits /table/profile or export.csv", async () => {
    const calls: string[] = [];
    setArtifactsFetch(async (url) => {
      calls.push(String(url));
      return jsonResponse("name,n\na,1\nb,2");
    });
    const host = document.body.children[0] as unknown as FakeEl;
    renderTableArtifact(
      host as unknown as HTMLElement,
      { id: "art-1", filename: "t.csv", version_id: "v1" },
      "/files/t.csv",
      { workbenchOn: false, capabilities: [...WORKBENCH_CAPS, "parquet"] },
    );
    await flush();
    expect(calls.some((u) => u.includes("/table/profile"))).toBe(false);
    expect(calls.some((u) => u.includes("export.csv"))).toBe(false);
    expect(calls.some((u) => u.includes("/table?"))).toBe(false);
    expect(host.querySelector(".wb-table-schema")).toBeNull();
    expect(host.querySelector(".wb-table-approx")).toBeNull();
    expect(host.querySelector("table.sheet")).toBeTruthy();
  });

  it("workbench fetches profile with version_id only and streams export via href", async () => {
    const calls: string[] = [];
    setArtifactsFetch(async (url) => {
      const u = String(url);
      calls.push(u);
      if (u.includes("/table/profile")) {
        expect(u).toContain("version_id=v1");
        expect(u).not.toMatch(/[?&](sort|dir|offset|limit)=/);
        return jsonResponse(profileFixture({ approximate: true }));
      }
      if (u.includes("/table?")) {
        return jsonResponse({
          artifact_id: "art-1",
          version_id: "v1",
          columns: ["n", "label"],
          rows: [[1, "a"]],
          total_rows: 51,
          offset: 0,
          limit: 50,
        });
      }
      throw new Error("unexpected fetch " + u);
    });
    const host = document.body.children[0] as unknown as FakeEl;
    renderTableArtifact(
      host as unknown as HTMLElement,
      { id: "art-1", filename: "t.csv", version_id: "v1" },
      "/files/t.csv",
      { workbenchOn: true, capabilities: WORKBENCH_CAPS },
    );
    await flush();
    expect(calls.some((u) => u.includes("/table/profile"))).toBe(true);
    expect(calls.some((u) => u.includes("export.csv"))).toBe(false);
    expect(host.querySelector(".wb-table-schema")).toBeTruthy();
    expect(host.querySelector(".wb-table-distribution")).toBeTruthy();
    const link = host.querySelector("a.wb-table-export-link");
    expect(link!.getAttribute("href")).toContain("/table/export.csv");
    expect(link!.getAttribute("href")).toContain("version_id=v1");
    expect(host.querySelector(".wb-table-approx")).toBeTruthy();
  });
});
