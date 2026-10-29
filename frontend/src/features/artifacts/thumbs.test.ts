import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { _thumbCache } from "../../stores/artifacts";
import { resetStoreFields } from "../../stores/signal-field";
import {
  setArtifactsFetch,
  PREVIEW_READ_MAX_BYTES,
  THUMB_READ_CONCURRENCY,
  THUMB_READ_MAX_BYTES,
} from "./api";
import { filesT } from "./copy";
import { pruneThumbReads, THUMB_CACHE_LIMIT, tileThumb } from "./thumbs";
import type { ArtifactRow } from "./types";

class Node {
  children: Node[] = [];
  className = "";
  textContent = "";
  title = "";
  src = "";
  html = "";
  isConnected = true;
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
const texts = (tile: HTMLElement) =>
  walk(tile as unknown as Node)
    .map((node) => node.textContent)
    .filter(Boolean);
const settle = async (rounds = 6) => {
  for (let i = 0; i < rounds; i++) await new Promise<void>((resolve) => setTimeout(resolve, 0));
};
const firstChild = (tile: HTMLElement): Node => (tile as unknown as Node).children[0]!;

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
    pruneThumbReads();
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
    resetStoreFields();
  });

  const table: ArtifactRow = {
    id: "t",
    filename: "de.csv",
    content_type: "text/csv",
    version_id: "v1",
    size_bytes: 33,
  };

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
    const notes: ArtifactRow = {
      id: "n",
      filename: "notes.txt",
      content_type: "text/plain",
      version_id: "v1",
    };
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

describe("thumbnail read budget, concurrency and consumers (A1)", () => {
  const row = (id: string, filename = "log.txt"): ArtifactRow => ({
    id,
    filename,
    content_type: "text/plain",
    version_id: "v1",
  });

  beforeEach(() => {
    resetStoreFields();
    vi.stubGlobal("document", { createElement: () => new Node() });
  });
  afterEach(() => {
    pruneThumbReads();
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
    vi.useRealTimers();
    resetStoreFields();
  });

  it("keeps at most four reads in flight across fifty cards", async () => {
    let active = 0;
    let peak = 0;
    let reads = 0;
    setArtifactsFetch(async () => {
      reads += 1;
      active += 1;
      peak = Math.max(peak, active);
      await new Promise<void>((resolve) => setTimeout(resolve, 2));
      active -= 1;
      return new Response("first line\nsecond line\n");
    });
    const tiles = Array.from({ length: 50 }, (_, i) => tileThumb(row(`t${i}`)));
    await vi.waitFor(() => expect(reads).toBe(50), { timeout: 5000 });
    expect(peak).toBeLessThanOrEqual(THUMB_READ_CONCURRENCY);
    expect(tiles.length).toBe(50);
  });

  it("drops a queued read that nobody shows any more without issuing a request", async () => {
    let started = 0;
    setArtifactsFetch((_url, init) => {
      started += 1;
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("aborted", "AbortError"));
        });
      });
    });
    const tiles = Array.from({ length: THUMB_READ_CONCURRENCY + 2 }, (_, i) =>
      tileThumb(row(`q${i}`)),
    );
    await settle(2);
    expect(started).toBe(THUMB_READ_CONCURRENCY);
    for (const tile of tiles) firstChild(tile).isConnected = false;
    pruneThumbReads();
    await settle();
    // The two queued reads never reached the network.
    expect(started).toBe(THUMB_READ_CONCURRENCY);
  });

  it("aborts an in-flight read whose only consumer left, and retries on the next paint", async () => {
    let aborted = 0;
    let reads = 0;
    let reply: "pending" | "ok" = "pending";
    setArtifactsFetch((_url, init) => {
      reads += 1;
      if (reply === "ok") return Promise.resolve(new Response("first line\nsecond line\n"));
      return new Promise<Response>((_resolve, reject) => {
        const signal = init?.signal;
        signal?.addEventListener("abort", () => {
          aborted += 1;
          reject(new DOMException("aborted", "AbortError"));
        });
      });
    });
    const first = tileThumb(row("pruned"));
    const consumer = firstChild(first);
    await settle(2);
    expect(reads).toBe(1);
    consumer.isConnected = false;
    pruneThumbReads();
    await settle();
    expect(aborted).toBe(1);
    // A cancelled read is evicted, and the detached tile was never written.
    expect(consumer.textContent).toBe("");
    reply = "ok";
    const retried = tileThumb(row("pruned"));
    await settle();
    expect(reads).toBe(2);
    expect(texts(retried)).toContain("first line\nsecond line");
  });

  it("keeps a shared read alive while one consumer is still mounted", async () => {
    let aborted = 0;
    setArtifactsFetch((_url, init) => {
      return new Promise<Response>((_resolve, reject) => {
        const signal = init?.signal;
        signal?.addEventListener("abort", () => {
          aborted += 1;
          reject(new DOMException("aborted", "AbortError"));
        });
      });
    });
    const first = tileThumb(row("shared"));
    const second = tileThumb(row("shared"));
    const firstConsumer = firstChild(first);
    const secondConsumer = firstChild(second);
    await settle(2);
    firstConsumer.isConnected = false;
    pruneThumbReads();
    await settle();
    expect(aborted).toBe(0);
    secondConsumer.isConnected = false;
    pruneThumbReads();
    await settle();
    expect(aborted).toBe(1);
  });

  it("marks a prefix-only text tile as a partial preview", async () => {
    const body = ("row,value\n" + "x".repeat(64) + "\n").repeat(5000);
    setArtifactsFetch(async () => new Response(body));
    const tile = tileThumb(row("big", "notes.txt"));
    const d = firstChild(tile);
    await vi.waitFor(
      () => expect(d.textContent.startsWith(filesT("files.thumb.partial"))).toBe(true),
      { timeout: 2000 },
    );
    expect(d.className).toBe("txt");
    expect(d.textContent.length).toBeLessThan(THUMB_READ_MAX_BYTES);
  });

  it("shows only columns and a partial mark for a truncated data tile, never a row count", async () => {
    const body = "gene,logFC\n" + "TP53,1.0\n".repeat(40000);
    setArtifactsFetch(async () => new Response(body));
    const tile = tileThumb({
      id: "csv",
      filename: "big.csv",
      content_type: "text/csv",
      version_id: "v1",
    });
    await vi.waitFor(() => expect(texts(tile)).toContain(filesT("files.thumb.partial")), {
      timeout: 2000,
    });
    expect(texts(tile)).toContain("gene");
    expect(texts(tile)).toContain("logFC");
    expect(texts(tile).some((text) => / row|rows/.test(text))).toBe(false);
  });

  it("marks a truncated molecule tile as a partial preview", async () => {
    const atoms = Array.from({ length: 12000 }, (_, i) =>
      `ATOM  ${String(i).padStart(5)}  CA  ALA A${String(i).padStart(4)}      ${(i * 0.1).toFixed(3)}   1.000   2.000  1.00  0.00           C`,
    ).join("\n");
    setArtifactsFetch(async () => new Response(atoms));
    const tile = tileThumb({
      id: "mol",
      filename: "big.pdb",
      content_type: "chemical/x-pdb",
      version_id: "v1",
    });
    await vi.waitFor(
      () => expect(texts(tile)).toContain(filesT("files.thumb.partial")),
      { timeout: 2000 },
    );
  });

  it("loads an image tile through the byte budget and never assigns the artifact URL", async () => {
    const created: string[] = [];
    vi.stubGlobal("URL", {
      createObjectURL: (blob: Blob) => {
        created.push(blob.type);
        return "blob:preview";
      },
      revokeObjectURL: () => undefined,
    });
    const fetched: string[] = [];
    setArtifactsFetch(async (url) => {
      fetched.push(url);
      return new Response(new Uint8Array([1, 2, 3, 4]));
    });
    const tile = tileThumb({
      id: "pic",
      filename: "plot.png",
      content_type: "image/png",
      version_id: "v1",
    });
    await vi.waitFor(() => expect(created).toEqual(["image/png"]));
    const sources = walk(tile as unknown as Node)
      .map((node) => node.src)
      .filter(Boolean);
    expect(sources).toEqual(["blob:preview"]);
    expect(fetched.some((url) => url.includes("/api/v1/artifacts"))).toBe(true);
    expect(sources.some((src) => src.includes("/api/"))).toBe(false);
    (tile as unknown as Node).isConnected = false;
  });

  it("does not fall back to the artifact URL when an image is over the preview cap", async () => {
    vi.stubGlobal("URL", {
      createObjectURL: () => {
        throw new Error("must not build a URL for an over-cap image");
      },
      revokeObjectURL: () => undefined,
    });
    setArtifactsFetch(
      async () =>
        new Response(new Uint8Array([1]), {
          headers: { "content-length": String(PREVIEW_READ_MAX_BYTES + 1) },
        }),
    );
    const tile = tileThumb({
      id: "huge",
      filename: "huge.png",
      content_type: "image/png",
      version_id: "v1",
    });
    await vi.waitFor(() =>
      expect(walk(tile as unknown as Node).some((node) => node.className === "big")).toBe(true),
    );
    const sources = walk(tile as unknown as Node)
      .map((node) => node.src)
      .filter(Boolean);
    expect(sources).toEqual([]);
    (tile as unknown as Node).isConnected = false;
  });
});
