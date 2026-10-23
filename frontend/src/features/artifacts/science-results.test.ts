import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreFields } from "../../stores/signal-field";
import { setArtifactsFetch, TEXT_SOURCE_PREVIEW_CHARACTERS } from "./api";
import { filesT } from "./copy";
import {
  parseScienceSearch,
  renderScienceResults,
  scienceRecordPage,
  scienceSourceStatus,
  SCIENCE_RECORD_MAX,
  SCIENCE_RECORD_PAGE,
  type ScienceSearch,
} from "./science-results";

const DIGEST = "a".repeat(64);

function envelope(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    query: "tp53",
    endpoint: "https://api.example.org/search?q=tp53&key=SECRET",
    retrieved_at: "2026-10-20T10:00:00Z",
    source_checksum: DIGEST,
    accessions: ["P04637"],
    result: {
      database: "uniprot",
      provenance: { retrieved_at: "2026-10-20T10:00:00Z", response_sha256: DIGEST },
      results: [
        {
          id: "P04637",
          title: "Cellular tumor antigen p53",
          url: "https://www.uniprot.org/uniprotkb/P04637",
          type: "protein",
          organism: "Homo sapiens",
          description: "Tumor suppressor",
        },
        { id: "P04638", title: "p53 isoform", url: "javascript:alert(1)", type: "protein" },
      ],
    },
    ...overrides,
  });
}

describe("strict saved-search envelope validation (A4)", () => {
  it("accepts the canonical envelope and the historical alias envelope", () => {
    const canonical = JSON.stringify({
      query: "brca1",
      request_url: "https://api.example.org/search?q=brca1",
      response_sha256: DIGEST,
      retrieved_at: "2026-10-20T10:00:00Z",
      result: { database: "uniprot", results: [] },
    });
    const parsed = parseScienceSearch(canonical);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.search.database).toBe("uniprot");
    expect(parsed.search.requestUrl).toBe("https://api.example.org/search?q=brca1");
    expect(parsed.search.responseSha256).toBe(DIGEST);
    expect(parsed.search.aliasesUsed).toEqual([]);
    expect(parsed.search.conflicts).toEqual([]);

    const legacy = parseScienceSearch(envelope());
    expect(legacy.ok).toBe(true);
    if (!legacy.ok) return;
    // The envelope's result.provenance still carries the canonical digest, so
    // only the request URL is served by a legacy alias.
    expect(legacy.search.aliasesUsed).toEqual(["endpoint"]);
    expect(legacy.search.database).toBe("uniprot");
    const legacyOnly = parseScienceSearch(
      envelope({ result: { database: "uniprot", results: [] } }),
    );
    expect(legacyOnly.ok).toBe(true);
    if (!legacyOnly.ok) return;
    expect(legacyOnly.search.aliasesUsed).toEqual(["endpoint", "source_checksum"]);
  });

  it("does not accept any JSON that merely has a result property", () => {
    for (const body of [
      '{"result": 42}',
      '{"result": {}}',
      '{"result": {"results": "no"}}',
      '{"result": {"results": []}}',
      '{"result": {"foo": [1, 2]}}',
      "[1, 2, 3]",
      '{"foo": "bar"}',
      "not json",
    ]) {
      expect(parseScienceSearch(body).ok, body).toBe(false);
    }
  });

  it("reports a conflict when canonical and legacy values disagree", () => {
    const parsed = parseScienceSearch(
      envelope({
        request_url: "https://api.example.org/other",
        response_sha256: "b".repeat(64),
        endpoint: "https://api.example.org/search?q=tp53",
        source_checksum: DIGEST,
      }),
    );
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.search.conflicts).toEqual([
      "request_url ↔ endpoint",
      "response_sha256 ↔ source_checksum",
    ]);
    // Canonical values are kept; no silent choice between the two.
    expect(parsed.search.requestUrl).toBe("https://api.example.org/other");
    expect(parsed.search.responseSha256).toBe("b".repeat(64));
  });

  it("marks a deliberately hidden response digest", () => {
    for (const body of [
      envelope({ response_sha256_hidden: true }),
      envelope({ source_checksum: "<redacted:credential>", response_sha256: "" }),
      envelope({ hidden_fields: ["response_sha256"], source_checksum: "" }),
      JSON.stringify({
        query: "q",
        request_url: "https://api.example.org/x",
        response_sha256: "<redacted:credential>",
        result: { database: "db", results: [] },
      }),
    ]) {
      const parsed = parseScienceSearch(body);
      expect(parsed.ok, body).toBe(true);
      if (!parsed.ok) continue;
      expect(parsed.search.responseSha256Hidden).toBe(true);
      expect(parsed.search.responseSha256).toBe("");
    }
  });

  it("extracts only the allowed record fields and counts malformed records", () => {
    const parsed = parseScienceSearch(
      JSON.stringify({
        query: "q",
        request_url: "https://api.example.org/x",
        result: {
          database: "db",
          results: [
            {
              id: 7,
              title: "<img onerror=alert(1)>",
              url: "https://example.org/record",
              type: "paper",
              organism: "human",
              method: "cryo-em",
              date: "2026-01-01",
              length: 100,
              secret_extra: "must not be shown",
            },
            "not a record",
            { unrelated: true },
          ],
        },
      }),
    );
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.search.malformedRecords).toBe(2);
    expect(parsed.search.records).toHaveLength(1);
    const record = parsed.search.records[0]!;
    expect(record.id).toBe("7");
    expect(record.title).toBe("<img onerror=alert(1)>");
    expect(record.url).toBe("https://example.org/record");
    expect(record.type).toBe("paper");
    expect(record.attributes.map(([key]) => key)).toEqual(["organism", "method", "date"]);
  });
});

describe("external link sanitization (A4)", () => {
  it("keeps safe http(s) and strips credential query parameters", async () => {
    const { sanitizeExternalUrl } = await import("./science-results");
    expect(sanitizeExternalUrl("https://example.org/a?q=1&token=SECRET&x=2")).toBe(
      "https://example.org/a?q=1&x=2",
    );
    expect(sanitizeExternalUrl("http://example.org/a#frag")).toBe("http://example.org/a");
  });

  it("refuses non-http schemes and embedded credentials", async () => {
    const { sanitizeExternalUrl } = await import("./science-results");
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

describe("bounded record pages (A4)", () => {
  const many = (count: number): ScienceSearch => {
    const parsed = parseScienceSearch(
      JSON.stringify({
        request_url: "https://api.example.org/x",
        result: {
          database: "db",
          results: Array.from({ length: count }, (_, index) => ({
            id: "id" + index,
            title: "record " + index,
            url: "https://example.org/" + index,
          })),
        },
      }),
    );
    if (!parsed.ok) throw new Error("fixture");
    return parsed.search;
  };

  it("pages fifty records at a time and never reads past the five-hundredth", () => {
    const search = many(600);
    const first = scienceRecordPage(search, 0);
    expect(first.records).toHaveLength(SCIENCE_RECORD_PAGE);
    expect(first.hasMore).toBe(true);
    expect(first.capped).toBe(true);
    const last = scienceRecordPage(search, 9);
    expect(last.records).toHaveLength(50);
    expect(last.hasMore).toBe(false);
    expect(scienceRecordPage(search, 10).records).toHaveLength(0);
    expect(SCIENCE_RECORD_MAX).toBe(500);
  });

  it("walks every saved page without duplication", () => {
    const search = many(120);
    const ids: string[] = [];
    let page = 0;
    for (;;) {
      const result = scienceRecordPage(search, page);
      ids.push(...result.records.map((record) => record.id!));
      if (!result.hasMore) break;
      page += 1;
      if (page > 20) throw new Error("paging did not terminate");
    }
    expect(ids).toHaveLength(120);
    expect(new Set(ids).size).toBe(120);
  });
});

describe("public-source readability status (A4)", () => {
  it("distinguishes a hidden digest, a shown digest and a missing one", () => {
    expect(scienceSourceStatus({ response_sha256: DIGEST })).toMatchObject({
      digest: DIGEST,
      digestHidden: false,
    });
    expect(scienceSourceStatus({ response_sha256: "<redacted:credential>" })).toMatchObject({
      digest: "",
      digestHidden: true,
    });
    expect(scienceSourceStatus({ hidden_fields: ["response_sha256"] })).toMatchObject({
      digestHidden: true,
    });
    expect(scienceSourceStatus({})).toMatchObject({ digest: "", digestHidden: false });
  });

  it("surfaces conflict and normalized-alias markers", () => {
    expect(
      scienceSourceStatus({ conflict_fields: ["request_url"], normalized_fields: ["endpoint"] }),
    ).toMatchObject({ conflicts: ["request_url"], aliases: ["endpoint"] });
  });
});

class FakeNode {
  children: FakeNode[] = [];
  parent: FakeNode | null = null;
  className = "";
  textContent = "";
  html = "";
  href = "";
  hidden = false;
  target = "";
  rel = "";
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
  constructor(readonly tagName = "div") {}
  get innerHTML(): string {
    return this.html;
  }
  set innerHTML(value: string) {
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

describe("saved-search result view (A4)", () => {
  const host = (node: FakeNode) => node as unknown as HTMLElement;
  const artifact = { id: "search", filename: "science-uniprot.json", version_id: "v1", _exactVersion: true };
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

  it("renders the source area and the saved records without any network read", () => {
    const root = new FakeNode();
    expect(renderScienceResults(host(root), artifact, url, envelope())).toBe(true);
    const values = texts(root);
    expect(values).toContain(filesT("science.tab.records"));
    expect(values).toContain(filesT("science.tab.json"));
    expect(values).toContain("uniprot");
    expect(values).toContain("https://api.example.org/search?q=tp53");
    expect(values).toContain("2026-10-20T10:00:00Z");
    expect(values).toContain(DIGEST);
    expect(values).toContain("Cellular tumor antigen p53");
    expect(values.some((value) => value.includes("P04637"))).toBe(true);
    expect(fetches).toEqual([]);
    // Titles are text: the record title cannot become markup.
    const title = walk(root).find((node) => node.className === "science-record-title")!;
    expect(title.innerHTML).toBe("");
  });

  it("links only a safe record URL and keeps the refused one as text", () => {
    const root = new FakeNode();
    renderScienceResults(host(root), artifact, url, envelope());
    const links = walk(root).filter((node) => node.tagName === "a");
    const hrefs = links.map((node) => node.href);
    expect(hrefs).toContain("https://www.uniprot.org/uniprotkb/P04637");
    expect(hrefs.some((href) => href.startsWith("javascript:"))).toBe(false);
    expect(texts(root).some((text) => text.includes("javascript:"))).toBe(false);
    const link = links.find((node) => node.href.includes("uniprot"))!;
    expect(link.rel).toBe("noopener noreferrer");
    expect(link.target).toBe("_blank");
  });

  it("names hidden digests, aliases and conflicts in the source area", () => {
    const root = new FakeNode();
    renderScienceResults(
      host(root),
      artifact,
      url,
      envelope({
        endpoint: undefined,
        request_url: "https://api.example.org/other",
        source_checksum: undefined,
        response_sha256: "<redacted:credential>",
        conflict_fields: ["request_url"],
        normalized_fields: ["endpoint"],
      }),
    );
    const values = texts(root);
    expect(values).toContain(filesT("science.responseHidden"));
    expect(values).toContain(filesT("science.responseHiddenNote"));
    expect(values).toContain(filesT("science.aliasNote", "endpoint"));
    expect(values).toContain(filesT("science.conflictNote", "request_url"));
  });

  it("pages the saved records with an explicit Load more and no auto-fetch", () => {
    const records = Array.from({ length: 120 }, (_, index) => ({
      id: "r" + index,
      title: "record " + index,
    }));
    const body = JSON.stringify({
      request_url: "https://api.example.org/x",
      result: { database: "db", results: records },
    });
    const root = new FakeNode();
    renderScienceResults(host(root), artifact, url, body);
    expect(walk(root).filter((node) => node.className === "science-record")).toHaveLength(50);
    expect(texts(root)).toContain(filesT("science.records.shown", "50", "120"));
    const more = withText(root, filesT("science.records.loadMore"))[0]!;
    more.onclick?.();
    expect(walk(root).filter((node) => node.className === "science-record")).toHaveLength(100);
    expect(texts(root)).toContain(filesT("science.records.shown", "100", "120"));
    more.onclick?.();
    expect(walk(root).filter((node) => node.className === "science-record")).toHaveLength(120);
    expect(more.hidden).toBe(true);
    expect(fetches).toEqual([]);
  });

  it("caps the list at 500 records and offers the exact-version original", () => {
    const records = Array.from({ length: 520 }, (_, index) => ({ id: "r" + index, title: "r" + index }));
    const root = new FakeNode();
    renderScienceResults(
      host(root),
      artifact,
      url,
      JSON.stringify({ request_url: "https://api.example.org/x", result: { database: "db", results: records } }),
    );
    const more = withText(root, filesT("science.records.loadMore"))[0]!;
    for (let page = 0; page < 20 && !more.hidden; page++) more.onclick?.();
    expect(walk(root).filter((node) => node.className === "science-record")).toHaveLength(SCIENCE_RECORD_MAX);
    expect(texts(root)).toContain(filesT("science.records.limit"));
    const links = walk(root).filter((node) => node.tagName === "a");
    expect(links.map((node) => node.href)).toContain(url);
  });

  it("keeps the raw JSON tab available on the same version", () => {
    const body = envelope();
    const root = new FakeNode();
    renderScienceResults(host(root), artifact, url, body);
    const jsonTab = withText(root, filesT("science.tab.json"))[0]!;
    jsonTab.onclick?.();
    const pre = walk(root).find((node) => node.className.includes("science-json"))!;
    expect(pre.textContent).toBe(body);
    expect(walk(root).filter((node) => node.tagName === "a").map((node) => node.href)).toContain(url);
  });

  it("labels a bounded raw JSON tab when the file is larger than the display limit", () => {
    const body =
      JSON.stringify({
        request_url: "https://api.example.org/x",
        result: {
          database: "db",
          results: [{ id: "r", title: "x".repeat(TEXT_SOURCE_PREVIEW_CHARACTERS + 10) }],
        },
      });
    const root = new FakeNode();
    renderScienceResults(host(root), artifact, url, body);
    withText(root, filesT("science.tab.json"))[0]!.onclick?.();
    const pre = walk(root).find((node) => node.className.includes("science-json"))!;
    expect(pre.textContent.length).toBe(TEXT_SOURCE_PREVIEW_CHARACTERS);
    expect(texts(root)).toContain(
      filesT("science.json.truncated", TEXT_SOURCE_PREVIEW_CHARACTERS.toLocaleString()),
    );
  });

  it("declines unrelated JSON so the caller keeps its generic path", () => {
    const root = new FakeNode();
    expect(renderScienceResults(host(root), artifact, url, '{"result": {"a": 1}}')).toBe(false);
    expect(root.children).toHaveLength(0);
  });
});
