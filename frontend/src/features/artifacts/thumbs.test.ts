import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { _thumbCache } from "../../stores/artifacts";
import { resetStoreFields } from "../../stores/signal-field";
import { bytes, setArtifactsFetch, THUMB_MAX_BYTES, THUMB_MAX_CONCURRENCY, THUMB_READ_TIMEOUT_MS } from "./api";
import { filesT } from "./copy";
import {
  beginThumbPaint,
  endThumbPaint,
  releaseThumbReads,
  THUMB_CACHE_LIMIT,
  tileThumb,
} from "./thumbs";
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


describe("thumbnail read budget and lifecycle (R1)", () => {
  class Node2 {
    children: Node2[] = [];
    className = "";
    textContent = "";
    title = "";
    src = "";
    html = "";
    get innerHTML(): string {
      return this.html;
    }
    set innerHTML(value: string) {
      this.children = [];
      this.html = value;
    }
    appendChild(child: Node2): Node2 {
      this.children.push(child);
      return child;
    }
  }

  const settle = async (rounds = 6): Promise<void> => {
    for (let i = 0; i < rounds; i++) await new Promise<void>((resolve) => setTimeout(resolve, 0));
  };
  const firstChild = (tile: HTMLElement): Node2 => (tile as unknown as Node2).children[0]!;
  const row = (id: string, filename = "log.txt"): ArtifactRow => ({
    id,
    filename,
    content_type: "text/plain",
    version_id: "v1",
  });

  beforeEach(() => {
    resetStoreFields();
    vi.stubGlobal("document", { createElement: () => new Node2() });
  });
  afterEach(() => {
    releaseThumbReads();
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
    expect(peak).toBeLessThanOrEqual(THUMB_MAX_CONCURRENCY);
    expect(tiles.length).toBe(50);
  });

  it("cancels reads that leave the Files paint and no other view owns", async () => {
    let aborted = 0;
    let started = 0;
    setArtifactsFetch((_url, init) => {
      started += 1;
      const signal = init?.signal;
      return new Promise<Response>((_resolve, reject) => {
        signal?.addEventListener("abort", () => {
          aborted += 1;
          reject(new DOMException("aborted", "AbortError"));
        });
        if (signal?.aborted) {
          aborted += 1;
          reject(new DOMException("aborted", "AbortError"));
        }
      });
    });
    const paint = beginThumbPaint();
    for (let i = 0; i < 8; i++) tileThumb(row(`c${i}`));
    releaseThumbReads();
    await settle();
    expect(started).toBeGreaterThan(0);
    expect(aborted).toBeGreaterThanOrEqual(4);
    expect(paint).toBeGreaterThan(0);
  });

  it("does not cancel a shared read that another view still shows", async () => {
    let cancelled = 0;
    setArtifactsFetch(async (_url, init) => {
      const signal = init?.signal;
      return new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode("alpha\nbeta\n"));
            controller.close();
          },
          cancel() {
            cancelled += 1;
          },
        }),
      );
      void signal;
    });
    const live = tileThumb(row("shared"));
    const paint = beginThumbPaint();
    const inFiles = tileThumb(row("shared"));
    endThumbPaint(paint);
    await settle();
    expect(texts(live)).toContain("alpha\nbeta");
    expect(texts(inFiles)).toContain("alpha\nbeta");
    expect(cancelled).toBe(0);
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
    expect(d.textContent.length).toBeLessThan(THUMB_MAX_BYTES);
  });

  it("explains why a structured tile fell back instead of hiding the reason", async () => {
    const body = "gene,logFC\n" + "TP53,1.0\n".repeat(40000);
    setArtifactsFetch(async () => new Response(body));
    const tile = tileThumb({ id: "csv2", filename: "big.csv", content_type: "text/csv", version_id: "v1" });
    await vi.waitFor(() => expect(texts(tile)).toContain("data"), { timeout: 2000 });
    expect(firstChild(tile).title).toBe(filesT("viewer.read.limit", bytes(THUMB_MAX_BYTES)));
  });

  it("keeps a structured tile whole-or-fallback rather than drawing half a table", async () => {
    const body = "gene,logFC\n" + "TP53,1.0\n".repeat(40000);
    setArtifactsFetch(async () => new Response(body));
    const tile = tileThumb({ id: "csv", filename: "big.csv", content_type: "text/csv", version_id: "v1" });
    // A structured tile that could not be read whole falls back to a generic
    // data tile; it must never show a row count derived from a prefix.
    await vi.waitFor(() => expect(texts(tile)).toContain("data"), { timeout: 2000 });
    expect(texts(tile).some((text) => text.includes(" row"))).toBe(false);
  });

  it("stops a tile read that outlives the fifteen-second deadline", async () => {
    vi.useFakeTimers();
    setArtifactsFetch(
      async () =>
        new Response(
          new ReadableStream<Uint8Array>({
            pull() {
              /* never delivers */
            },
          }),
        ),
    );
    const tile = tileThumb(row("slow"));
    await vi.advanceTimersByTimeAsync(THUMB_READ_TIMEOUT_MS + 100);
    await vi.advanceTimersByTimeAsync(0);
    expect(THUMB_READ_TIMEOUT_MS).toBe(15_000);
    expect(firstChild(tile).className).toBe("big");
  });
});
