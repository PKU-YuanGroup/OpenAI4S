import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreFields } from "../../stores/signal-field";
import { setArtifactsFetch } from "./api";
import { filesT } from "./copy";
import {
  readScienceSearchEnvelope,
  renderScienceResults,
  scienceRecordPage,
  scienceSourceStatus,
  SCIENCE_RECORD_MAX,
  SCIENCE_RECORD_PAGE,
  sanitizeExternalUrl,
} from "./science-results";
import type { ArtifactRow } from "./types";

const DIGEST = "a".repeat(64);

function envelope(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    query: "tp53",
    request_url: "https://api.example.org/search?q=tp53",
    retrieved_at: "2026-10-20T10:00:00Z",
    response_sha256: DIGEST,
    accessions: ["P04637"],
    result: {
      database: "uniprot",
      next_cursor: "",
      results: [
        {
          id: "P04637",
          title: "Cellular tumor antigen p53",
          url: "https://www.uniprot.org/uniprotkb/P04637",
          type: "protein",
          attributes: { organism: "Homo sapiens", length: 393, nested: { x: 1 } },
        },
        { id: "P04638", title: "p53 isoform", url: "javascript:alert(1)", type: "protein" },
      ],
    },
    ...overrides,
  });
}

describe("strict saved-search envelope reader (A5)", () => {
  it("accepts the canonical and historical envelopes", () => {
    const canonical = readScienceSearchEnvelope(envelope());
    expect(canonical).not.toBeNull();
    expect(canonical!.query).toBe("tp53");
    expect(canonical!.records).toHaveLength(2);
    expect(canonical!.records[0]).toMatchObject({
      id: "P04637",
      title: "Cellular tumor antigen p53",
      type: "protein",
    });
    expect(canonical!.nextCursor).toBe("");

    const legacy = readScienceSearchEnvelope(
      JSON.stringify({
        query: "brca1",
        endpoint: "https://api.example.org/search?q=brca1",
        retrieved_at: "2026-10-20T10:00:00Z",
        source_checksum: DIGEST,
        result: { database: "uniprot", results: [] },
      }),
    );
    expect(legacy).not.toBeNull();
    expect(legacy!.records).toEqual([]);
    expect(legacy!.requestUrl).toContain("brca1");
  });

  it("rejects every document that is not exactly this shape", () => {
    const cases: string[] = [
      "not json",
      "[]",
      '{"foo":"bar"}',
      // A JSON document that merely has a result property is not a search.
      '{"result":{"results":[]}}',
      '{"result":42}',
      '{"result":{"results":"no"}}',
      '{"result":{"results":[42]}}',
      // Results array with a record field of the wrong type.
      '{"query":"q","result":{"results":[{"id":7}]}}',
      '{"query":"q","result":{"results":[{"title":null}]}}',
      '{"query":"q","result":{"results":[{"attributes":[]}]}}',
      // Any extra top-level key means this reader does not own the document.
      JSON.stringify({
        query: "q",
        request_url: "https://api.example.org/x",
        result: { results: [] },
        extra: 1,
      }),
      JSON.stringify({
        query: "q",
        kind: "science_search",
        result: { results: [] },
      }),
    ];
    for (const body of cases) expect(readScienceSearchEnvelope(body), body).toBeNull();
  });

  it("names canonical/legacy disagreements instead of silently picking one", () => {
    const search = readScienceSearchEnvelope(
      JSON.stringify({
        query: "q",
        request_url: "https://api.example.org/canonical",
        endpoint: "https://api.example.org/legacy",
        response_sha256: "b".repeat(64),
        source_checksum: "c".repeat(64),
        result: { results: [] },
      }),
    );
    expect(search).not.toBeNull();
    expect(search!.conflicts).toEqual([
      "request_url ↔ endpoint",
      "response_sha256 ↔ source_checksum",
    ]);
    // Agreeing spellings are one fact under two names: no conflict.
    const agreeing = readScienceSearchEnvelope(
      JSON.stringify({
        query: "q",
        request_url: "https://api.example.org/same",
        endpoint: "https://api.example.org/same",
        result: { results: [] },
      }),
    );
    expect(agreeing).not.toBeNull();
    expect(agreeing!.conflicts).toEqual([]);
  });

  it("reports a saved next cursor but never claims a database total", () => {
    const search = readScienceSearchEnvelope(
      JSON.stringify({
        query: "q",
        request_url: "https://api.example.org/x",
        result: { next_cursor: "page-2", results: [] },
      }),
    );
    expect(search).not.toBeNull();
    expect(search!.nextCursor).toBe("page-2");
  });
});

describe("external link sanitization (A5)", () => {
  it("keeps safe http(s) and strips credential query parameters", () => {
    expect(sanitizeExternalUrl("https://example.org/a?q=1&token=SECRET&x=2")).toBe(
      "https://example.org/a?q=1&x=2",
    );
    expect(sanitizeExternalUrl("http://example.org/a#frag")).toBe("http://example.org/a");
  });

  it("refuses non-http schemes and embedded credentials", () => {
    for (const value of [
      "javascript:alert(1)",
      "data:text/html,<script>x</script>",
      "https://user:password@example.org/x",
      "file:///etc/passwd",
      "not a url",
      "",
      42,
    ]) {
      expect(sanitizeExternalUrl(value), String(value)).toBe("");
    }
  });
});

describe("bounded record pages (A5)", () => {
  function many(count: number) {
    const search = readScienceSearchEnvelope(
      JSON.stringify({
        query: "q",
        request_url: "https://api.example.org/x",
        result: {
          results: Array.from({ length: count }, (_, index) => ({
            id: "id" + index,
            title: "record " + index,
            url: "https://example.org/" + index,
          })),
        },
      }),
    );
    if (!search) throw new Error("fixture");
    return search;
  }

  it("pages fifty records at a time and never reads past the five-hundredth", () => {
    const search = many(600);
    const first = scienceRecordPage(search, 0);
    expect(first.records).toHaveLength(SCIENCE_RECORD_PAGE);
    expect(first.hasMore).toBe(true);
    expect(first.capped).toBe(true);
    expect(scienceRecordPage(search, 9).records).toHaveLength(50);
    expect(scienceRecordPage(search, 10).records).toHaveLength(0);
    expect(SCIENCE_RECORD_MAX).toBe(500);
  });

  it("walks every saved page without duplication", () => {
    const search = many(120);
    const ids: string[] = [];
    let page = 0;
    for (;;) {
      const result = scienceRecordPage(search, page);
      ids.push(...result.records.map((record) => record.id));
      if (!result.hasMore) break;
      page += 1;
      if (page > 20) throw new Error("paging did not terminate");
    }
    expect(ids).toHaveLength(120);
    expect(new Set(ids).size).toBe(120);
  });
});

class FakeNode {
  children: FakeNode[] = [];
  parent: FakeNode | null = null;
  page = false;
  className = "";
  textContent = "";
  html = "";
  href = "";
  target = "";
  rel = "";
  hidden = false;
  style: Record<string, string> = {};
  attrs: Record<string, string> = {};
  dataset: Record<string, string> = {};
  classList = (() => {
    const tokens = new Set<string>();
    return {
      add: (...names: string[]) => names.forEach((name) => tokens.add(name)),
      remove: (...names: string[]) => names.forEach((name) => tokens.delete(name)),
      toggle: (name: string, force?: boolean) => {
        const next = force === undefined ? !tokens.has(name) : force;
        if (next) tokens.add(name);
        else tokens.delete(name);
        return next;
      },
      contains: (name: string) => tokens.has(name),
    };
  })();
  onclick: (() => void) | null = null;
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
  appendChild(child: FakeNode): FakeNode {
    child.parent = this;
    this.children.push(child);
    return child;
  }
  setAttribute(name: string, value: string): void {
    this.attrs[name] = value;
  }
}

const walk = (node: FakeNode): FakeNode[] => [node, ...node.children.flatMap(walk)];
const texts = (node: FakeNode): string[] => walk(node).map((item) => item.textContent).filter(Boolean);
const withText = (root: FakeNode, text: string): FakeNode[] =>
  walk(root).filter((node) => node.textContent === text);

describe("saved-search result view (A5)", () => {
  const host = (node: FakeNode) => node as unknown as HTMLElement;
  const artifact: ArtifactRow = {
    id: "search",
    filename: "science-uniprot.json",
    version_id: "v1",
    _exactVersion: true,
  };
  const url = "/api/v1/artifacts/versions/v1";
  let fetches: string[];

  beforeEach(() => {
    resetStoreFields();
    fetches = [];
    vi.stubGlobal("document", { createElement: (tag: string) => new FakeNode(tag) });
    setArtifactsFetch(async (target) => {
      fetches.push(String(target));
      return new Response("unexpected");
    });
  });
  afterEach(() => {
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
    resetStoreFields();
  });

  function mount(body: string, renderRaw?: (target: HTMLElement) => void): FakeNode {
    const search = readScienceSearchEnvelope(body);
    if (!search) throw new Error("fixture is not an envelope");
    const root = new FakeNode("div");
    root.page = true;
    renderScienceResults(host(root), artifact, url, body, search, renderRaw);
    return root;
  }

  it("shows the saved-record count, the four columns and the record rows", () => {
    const root = mount(envelope());
    const values = texts(root);
    expect(values).toContain(filesT("science.tab.records"));
    expect(values).toContain(filesT("science.tab.json"));
    expect(values).toContain(filesT("science.records.saved", "2"));
    for (const key of ["science.column.id", "science.column.title", "science.column.type", "science.column.link"]) {
      expect(values).toContain(filesT(key));
    }
    expect(values).toContain("P04637");
    expect(values).toContain("Cellular tumor antigen p53");
    expect(values).toContain("protein");
    expect(fetches).toEqual([]);
  });

  it("renders only http(s) links, scrubs their displayed text and keeps other URLs as text", () => {
    const body = JSON.stringify({
      query: "q",
      request_url: "https://api.example.org/x",
      result: {
        results: [
          {
            id: "r1",
            title: "safe",
            url: "https://example.org/record?api_key=SECRET&x=1",
            type: "paper",
          },
          { id: "r2", title: "js", url: "javascript:alert(1)", type: "paper" },
          { id: "r3", title: "data", url: "data:text/html,<script>x</script>", type: "paper" },
          { id: "r4", title: "broken", url: "not a url", type: "paper" },
        ],
      },
    });
    const root = mount(body);
    const anchors = walk(root).filter((node) => node.tagName === "A");
    expect(anchors).toHaveLength(1);
    expect(anchors[0]!.target).toBe("_blank");
    expect(anchors[0]!.rel).toBe("noopener noreferrer");
    // The href keeps no credential param; the display text is scrubbed.
    expect(anchors[0]!.href).toBe("https://example.org/record?x=1");
    expect(anchors[0]!.textContent).toContain("[redacted]");
    expect(anchors[0]!.textContent).not.toContain("SECRET");
    expect(texts(root)).toContain("javascript:alert(1)");
    expect(texts(root)).toContain("data:text/html,<script>x</script>");
    expect(texts(root)).toContain("not a url");
    expect(walk(root).some((node) => node.tagName === "SCRIPT")).toBe(false);
  });

  it("renders titles and attributes as text, expanding at most eight scalar attributes", () => {
    const body = JSON.stringify({
      query: "q",
      request_url: "https://api.example.org/x",
      result: {
        results: [
          {
            id: "r1",
            title: "<img src=x onerror=alert(1)>",
            type: "paper",
            attributes: {
              organism: "human",
              method: "cryo-em",
              year: 2026,
              peer_reviewed: false,
              object: { nested: true },
              list: [1, 2],
              k5: "v5",
              k6: "v6",
              k7: "v7",
              k8: "v8",
            },
          },
        ],
      },
    });
    const root = mount(body);
    const title = walk(root)
      .filter((node) => (node.className || "") === "science-record-title")
      .at(-1)!;
    expect(title.textContent).toBe("<img src=x onerror=alert(1)>");
    expect(title.innerHTML).toBe("");
    const details = walk(root).find((node) => (node.className || "") === "science-record-attributes")!;
    expect(details).toBeTruthy();
    const lines = walk(details).filter((node) => (node.className || "") === "science-record-attribute");
    expect(lines).toHaveLength(8);
    const joined = lines.map((line) => line.children.map((child) => child.textContent).join("="));
    expect(joined).toContain("organism=human");
    expect(joined).toContain("year=2026");
    expect(joined).toContain("peer_reviewed=false");
    expect(joined.some((line) => line.includes("object"))).toBe(false);
    expect(joined.some((line) => line.includes("list"))).toBe(false);
  });

  it("shows the canonical/legacy conflict notice in the records view", () => {
    const root = mount(
      JSON.stringify({
        query: "q",
        request_url: "https://api.example.org/canonical",
        endpoint: "https://api.example.org/legacy",
        result: { results: [{ id: "r1", title: "record", type: "paper" }] },
      }),
    );
    expect(texts(root)).toContain(
      filesT("science.conflictNote", "request_url ↔ endpoint"),
    );
  });

  it("names an empty result file and a saved next page instead of a database total", () => {
    const empty = mount(
      JSON.stringify({ query: "q", request_url: "https://api.example.org/x", result: { results: [] } }),
    );
    expect(texts(empty)).toContain(filesT("science.records.saved", "0"));
    expect(texts(empty)).toContain(filesT("science.records.empty"));

    const paged = mount(
      JSON.stringify({
        query: "q",
        request_url: "https://api.example.org/x",
        result: { next_cursor: "page-2", results: [] },
      }),
    );
    expect(texts(paged)).toContain(filesT("science.records.nextPage"));
  });

  it("pages the saved records with load more and never fetches anything", () => {
    const body = JSON.stringify({
      query: "q",
      request_url: "https://api.example.org/x",
      result: {
        results: Array.from({ length: 120 }, (_, index) => ({
          id: "r" + index,
          title: "record " + index,
        })),
      },
    });
    const root = mount(body);
    const rows = () =>
      walk(root).filter(
        (node) =>
          (node.className || "") === "science-record" &&
          (node.className || "") !== "science-record-head",
      );
    expect(rows()).toHaveLength(50);
    const more = withText(root, filesT("science.records.loadMore"))[0]!;
    more.onclick?.();
    expect(rows()).toHaveLength(100);
    more.onclick?.();
    expect(rows()).toHaveLength(120);
    expect(more.hidden).toBe(true);
    expect(fetches).toEqual([]);
  });

  it("caps the list at 500 records and offers the exact-version JSON download", () => {
    const body = JSON.stringify({
      query: "q",
      request_url: "https://api.example.org/x",
      result: {
        results: Array.from({ length: 520 }, (_, index) => ({ id: "r" + index, title: "r" + index })),
      },
    });
    const root = mount(body);
    const more = withText(root, filesT("science.records.loadMore"))[0]!;
    for (let page = 0; page < 20 && !more.hidden; page++) more.onclick?.();
    const rows = walk(root).filter((node) => (node.className || "") === "science-record");
    expect(rows).toHaveLength(SCIENCE_RECORD_MAX);
    expect(texts(root)).toContain(filesT("science.records.limit"));
    expect(walk(root).filter((node) => node.tagName === "A").map((node) => node.href)).toContain(url);
  });

  it("keeps the raw JSON tab on the same version and renders through the shared raw-source view", () => {
    const body = envelope();
    let rawCalls = 0;
    const root = mount(body, (target) => {
      rawCalls += 1;
      const pre = new FakeNode("pre");
      pre.className = "renderer-source";
      pre.textContent = body;
      target.appendChild(pre as unknown as Node);
    });
    const jsonTab = withText(root, filesT("science.tab.json"))[0]!;
    jsonTab.onclick?.();
    expect(rawCalls).toBe(1);
    const raw = walk(root).find((node) => (node.className || "") === "renderer-source")!;
    expect(raw.textContent).toBe(body);
    // The records pane is hidden; the raw tab can be switched back.
    const recordsTab = withText(root, filesT("science.tab.records"))[0]!;
    recordsTab.onclick?.();
    const panes = walk(root).filter(
      (node) =>
        (node.className || "").includes("science-pane") ||
        (node.className || "").includes("renderer-source"),
    );
    // The raw pane stays built but hidden when the records tab is active.
    const rawPane = walk(root).find((node) =>
      node.children.some((child) => (child.className || "") === "renderer-source"),
    )!;
    expect(rawPane.classList.contains("hidden")).toBe(true);
    expect(panes.length).toBeGreaterThan(0);
  });

  it("renders a fallback raw view with the download when no shared renderer is passed", () => {
    const body = envelope();
    const root = mount(body);
    withText(root, filesT("science.tab.json"))[0]!.onclick?.();
    const raw = walk(root).find((node) => (node.className || "") === "renderer-source")!;
    expect(raw.textContent).toBe(body);
    expect(walk(root).filter((node) => node.tagName === "A").map((node) => node.href)).toContain(url);
  });

  it("keeps two versions' record lists separate", () => {
    const first = mount(
      JSON.stringify({
        query: "q1",
        request_url: "https://api.example.org/x",
        result: { results: [{ id: "one", title: "first version" }] },
      }),
    );
    const second = mount(
      JSON.stringify({
        query: "q2",
        request_url: "https://api.example.org/x",
        result: { results: [{ id: "two", title: "second version" }] },
      }),
    );
    expect(texts(first)).toContain("first version");
    expect(texts(first)).not.toContain("second version");
    expect(texts(second)).toContain("second version");
    expect(texts(second)).not.toContain("first version");
  });
});

describe("public-source readability status (S1 panel helper)", () => {
  it("reads the current alias_conflicts key and the earlier source_conflicts spelling", () => {
    expect(scienceSourceStatus({ alias_conflicts: ["request_url"] })).toMatchObject({
      conflicts: ["request_url"],
    });
    expect(scienceSourceStatus({ source_conflicts: ["response_sha256"] })).toMatchObject({
      conflicts: ["response_sha256"],
    });
    expect(
      scienceSourceStatus({ alias_conflicts: ["request_url"], source_conflicts: ["request_url"] }),
    ).toMatchObject({ conflicts: ["request_url"] });
    expect(scienceSourceStatus({}).conflicts).toEqual([]);
  });
});
