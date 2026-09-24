import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { _thumbCache } from "../../stores/artifacts";
import { resetStoreFields } from "../../stores/signal-field";
import { setArtifactsFetch } from "./api";
import { THUMB_CACHE_LIMIT, tileThumb } from "./thumbs";
import type { ArtifactRow } from "./types";

class Node {
  children: Node[] = [];
  className = "";
  textContent = "";
  src = "";
  html = "";
  get innerHTML(): string {
    return this.html;
  }
  set innerHTML(value: string) {
    this.children = [];
    this.html = value;
  }
  appendChild(child: Node): Node {
    this.children.push(child);
    return child;
  }
}

const walk = (node: Node): Node[] => [node, ...node.children.flatMap(walk)];
const texts = (tile: HTMLElement) => walk(tile as unknown as Node).map((node) => node.textContent).filter(Boolean);
const settle = async () => {
  for (let i = 0; i < 3; i++) await new Promise<void>((resolve) => setTimeout(resolve, 0));
};

describe("Files tile previews (AUDIT P06)", () => {
  let reads: string[];
  let reply: (url: string) => Response;

  beforeEach(() => {
    resetStoreFields();
    reads = [];
    reply = () => new Response("gene,logFC\nTP53,2.4\nBRCA1,-1.1\n");
    vi.stubGlobal("document", { createElement: () => new Node() });
    setArtifactsFetch(async (url) => {
      reads.push(url);
      return reply(url);
    });
  });
  afterEach(() => {
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
    resetStoreFields();
  });

  const table: ArtifactRow = { id: "t", filename: "de.csv", content_type: "text/csv", version_id: "v1", size_bytes: 33 };

  it("reads a data tile once per version, however often the grid repaints", async () => {
    const first = tileThumb(table);
    tileThumb(table);
    await settle();
    expect(reads).toHaveLength(1);
    expect(texts(first)).toEqual(["2 rows · 2 columns", "gene", "logFC"]);
    tileThumb(table);
    await settle();
    expect(reads).toHaveLength(1);
    // A new version of the same length is a new preview.
    tileThumb({ ...table, version_id: "v2" });
    await settle();
    expect(reads).toHaveLength(2);
  });

  it("neither shows nor keeps an error page or a failed read", async () => {
    const notes: ArtifactRow = { id: "n", filename: "notes.txt", content_type: "text/plain", version_id: "v1" };
    reply = () => new Response("Internal Server Error", { status: 500 });
    const failed = tileThumb(notes);
    await settle();
    expect(texts(failed)).not.toContain("Internal Server Error");
    reply = () => new Response("first line\nsecond line\n");
    const retried = tileThumb(notes);
    await settle();
    expect(reads).toHaveLength(2);
    expect(texts(retried)).toContain("first line\nsecond line");
  });

  it("keeps at most THUMB_CACHE_LIMIT previews, dropping the least recently shown", async () => {
    tileThumb({ ...table, id: "kept" });
    for (let i = 0; i < THUMB_CACHE_LIMIT + 6; i++) {
      tileThumb({ ...table, id: `t${i}` });
      if (i % 10 === 0) tileThumb({ ...table, id: "kept" });
    }
    await settle();
    const keys = Object.keys(_thumbCache.value);
    expect(keys).toHaveLength(THUMB_CACHE_LIMIT);
    expect(keys.some((key) => key.includes("kept:"))).toBe(true);
    expect(keys.some((key) => key.includes("|t0:"))).toBe(false);
  });
});
