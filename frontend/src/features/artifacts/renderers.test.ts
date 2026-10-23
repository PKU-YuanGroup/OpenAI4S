import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreFields } from "../../stores/signal-field";
import { bytes, PREVIEW_MAX_BYTES, setArtifactsFetch } from "./api";
import { artUrl } from "./cache";
import { filesT } from "./copy";
import { renderArtifactBody, renderDownloadArtifact, renderSequenceArtifact, renderTextArtifact } from "./renderers";
import type { ArtifactRow } from "./types";

class FakeNode {
  children: FakeNode[] = [];
  className = "";
  textContent = "";
  href = "";
  isConnected = true;
  disabled = false;
  dataset: Record<string, string> = {};
  style: Record<string, string> = {};
  attrs: Record<string, string> = {};
  onclick: (() => void) | null = null;
  constructor(readonly tagName = "div") {}
  get innerHTML(): string {
    return "";
  }
  set innerHTML(_value: string) {
    this.children = [];
  }
  appendChild(child: FakeNode): FakeNode {
    this.children.push(child);
    return child;
  }
  setAttribute(name: string, value: string): void {
    this.attrs[name] = value;
  }
}

const walk = (node: FakeNode): FakeNode[] => [node, ...node.children.flatMap(walk)];
const host = (node: FakeNode) => node as unknown as HTMLElement;
const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

/**
 * Serve `body` for every read, up to a bound. The download/text cycle this
 * guards against refetched without end; the bound turns a regression into a
 * count instead of a hang.
 */
function serve(body: string): { calls: () => number } {
  let calls = 0;
  setArtifactsFetch(async () => {
    calls += 1;
    if (calls > 5) throw new Error("probe bound reached");
    return new Response(body);
  });
  return { calls: () => calls };
}

describe("text artifacts that look binary (AUDIT A01)", () => {
  beforeEach(() => {
    vi.stubGlobal("document", { createElement: (tag: string) => new FakeNode(tag) });
  });
  afterEach(() => {
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
  });

  it("shows the download tile after one read instead of refetching forever", async () => {
    let raw = "";
    for (let i = 0; i < 3000; i++) raw += String.fromCharCode((i * 97 + 13) % 256);
    const probe = serve(btoa(raw));
    const container = new FakeNode();
    const a: ArtifactRow = { id: "blob", filename: "payload.txt", content_type: "text/plain" };
    renderDownloadArtifact(host(container), a, "/api/v1/artifacts/blob");
    await settle();
    await settle();
    expect(probe.calls()).toBe(1);
    const tiles = walk(container).filter((node) => node.className === "download-artifact");
    expect(tiles).toHaveLength(1);
    expect(walk(tiles[0]!).some((node) => node.textContent === "payload.txt")).toBe(true);
  });

  it("renders a one-line protein FASTA as source text", async () => {
    const aa = "ACDEFGHIKLMNPQRSTVWY";
    let seq = "";
    for (let i = 0; i < 1273; i++) seq += aa[(i * 7 + 3) % aa.length];
    const text = ">spike\n" + seq + "\n";
    const probe = serve(text);
    const container = new FakeNode();
    renderTextArtifact(host(container), { id: "s", filename: "spike.fasta" }, "/api/v1/artifacts/s");
    await settle();
    await settle();
    expect(probe.calls()).toBe(1);
    const source = walk(container).find((node) => node.className === "renderer-source");
    expect(source?.textContent).toBe(text);
    expect(walk(container).some((node) => node.className === "download-artifact")).toBe(false);
  });
});

describe("JSON artifacts judge bytes by control characters only (AUDIT S10)", () => {
  beforeEach(() => {
    vi.stubGlobal("document", { createElement: (tag: string) => new FakeNode(tag) });
  });
  afterEach(() => {
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
  });

  it("shows a JSON document with an embedded base64 value as source", async () => {
    let raw = "";
    for (let i = 0; i < 3000; i++) raw += String.fromCharCode((i * 97 + 13) % 256);
    const text = JSON.stringify({ figure: btoa(raw) });
    serve(text);
    const container = new FakeNode();
    renderTextArtifact(host(container), { id: "j", filename: "result.json" }, "/api/v1/artifacts/j");
    await settle();
    expect(walk(container).find((node) => node.className === "renderer-source")?.textContent).toBe(text);
  });

  it("offers a control-dense .json as a download after one read", async () => {
    const probe = serve("\u0000\u0001\u0002\u0003 ".repeat(200));
    const container = new FakeNode();
    renderTextArtifact(host(container), { id: "k", filename: "broken.json" }, "/api/v1/artifacts/k");
    await settle();
    await settle();
    expect(probe.calls()).toBe(1);
    expect(walk(container).some((node) => node.className === "download-artifact")).toBe(true);
  });
});

describe("late renderer descriptors (AUDIT A32)", () => {
  /** Tracks its parent, so `isConnected` means reachable from the page root. */
  class DomNode {
    children: DomNode[] = [];
    parent: DomNode | null = null;
    page = false;
    className = "";
    textContent = "";
    href = "";
    dataset: Record<string, string> = {};
    constructor(readonly tagName = "div") {}
    get isConnected(): boolean {
      return this.page || (!!this.parent && this.parent.isConnected);
    }
    set innerHTML(_value: string) {
      for (const child of this.children) child.parent = null;
      this.children = [];
    }
    appendChild(child: DomNode): DomNode {
      child.parent = this;
      this.children.push(child);
      return child;
    }
    remove(): void {
      if (this.parent) this.parent.children = this.parent.children.filter((node) => node !== this);
      this.parent = null;
    }
    setAttribute(): void {}
  }

  afterEach(() => {
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
    resetStoreFields();
  });

  it("does not run the previous artifact's glue after the dock replaced its body", async () => {
    resetStoreFields();
    vi.stubGlobal("document", { createElement: (tag: string) => new DomNode(tag) });
    const molecule = vi.fn();
    vi.stubGlobal("molecule", molecule);
    let refuseA!: () => void;
    const heldA = new Promise<void>((resolve) => { refuseA = resolve; });
    // Both descriptor reads fail, so each artifact takes the compatibility
    // descriptor: `.pdb` is molecule-3d, whose glue tears down the live viewer.
    setArtifactsFetch(async (url) => {
      if (url.includes("/artifacts/A/renderer")) await heldA;
      return new Response(JSON.stringify({ error: "unavailable" }), { status: 503 });
    });
    const viewer = new DomNode();
    viewer.page = true;
    const bodyA = viewer.appendChild(new DomNode());
    renderArtifactBody(bodyA as unknown as HTMLElement, { id: "A", filename: "a.pdb" });
    // renderViewer: the old body goes, a fresh one renders the next artifact.
    bodyA.remove();
    const bodyB = viewer.appendChild(new DomNode());
    renderArtifactBody(bodyB as unknown as HTMLElement, { id: "B", filename: "b.pdb" });
    await vi.waitFor(() => expect(molecule).toHaveBeenCalledTimes(1));
    expect(molecule.mock.calls[0]?.[2]).toBe("b.pdb");
    refuseA();
    for (let i = 0; i < 5; i++) await settle();
    expect(molecule).toHaveBeenCalledTimes(1);
  });
});


describe("preview read budget in the real preview paths (R1)", () => {
  class Dom {
    children: Dom[] = [];
    parent: Dom | null = null;
    page = false;
    className = "";
    textContent = "";
    href = "";
    disabled = false;
    dataset: Record<string, string> = {};
    style: Record<string, string> = {};
    attrs: Record<string, string> = {};
    onclick: (() => void) | null = null;
    constructor(readonly tagName = "div") {}
    get isConnected(): boolean {
      return this.page || (!!this.parent && this.parent.isConnected);
    }
    set innerHTML(_value: string) {
      for (const child of this.children) child.parent = null;
      this.children = [];
    }
    appendChild(child: Dom): Dom {
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
  }
  const walk = (node: Dom): Dom[] => [node, ...node.children.flatMap(walk)];
  const host = (node: Dom) => node as unknown as HTMLElement;
  const fallbackCards = (root: Dom) => walk(root).filter((node) => node.className === "renderer-fallback");

  beforeEach(() => {
    resetStoreFields();
    vi.stubGlobal("document", { createElement: (tag: string) => new Dom(tag) });
  });
  afterEach(() => {
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
    resetStoreFields();
  });

  it("rejects an over-budget structure file from trusted metadata without parsing it", async () => {
    const parseSequence = vi.fn();
    vi.stubGlobal("OpenAI4SScientificRenderers", { parseSequence });
    let reads = 0;
    setArtifactsFetch(async () => {
      reads += 1;
      return new Response(">spike\nACDEFGHIK\n");
    });
    const container = new Dom("div");
    container.page = true;
    const a: ArtifactRow = {
      id: "big",
      filename: "spike.fasta",
      version_id: "v1",
      size_bytes: 9 * 1024 * 1024,
    };
    renderSequenceArtifact(host(container), a, "/api/v1/artifacts/big");
    await vi.waitFor(() => expect(fallbackCards(container)).toHaveLength(1), { timeout: 2000 });
    expect(reads).toBe(0);
    expect(parseSequence).not.toHaveBeenCalled();
    const card = fallbackCards(container)[0]!;
    expect(
      walk(card).some((node) => node.textContent === filesT("viewer.read.limit", bytes(PREVIEW_MAX_BYTES))),
    ).toBe(true);
    // The exact-version download is always offered, and retry is one click.
    expect(walk(card).filter((node) => node.tagName === "a").map((node) => node.href)).toContain(
      "/api/v1/artifacts/big",
    );
    expect(walk(card).some((node) => node.tagName === "button" && node.textContent === filesT("viewer.read.retry"))).toBe(true);
  });

  it("stops a forged small metadata size while streaming and never parses the prefix", async () => {
    const parseSequence = vi.fn();
    vi.stubGlobal("OpenAI4SScientificRenderers", { parseSequence });
    const chunk = new TextEncoder().encode("A".repeat(1024 * 1024));
    let reads = 0;
    setArtifactsFetch(async () => {
      reads += 1;
      return new Response(
        new ReadableStream<Uint8Array>({
          pull(controller) {
            controller.enqueue(chunk);
          },
        }),
      );
    });
    const container = new Dom("div");
    container.page = true;
    const a: ArtifactRow = {
      id: "forged",
      filename: "spike.fasta",
      version_id: "v1",
      // The trusted metadata understates the body; the streamed budget decides.
      size_bytes: 16,
    };
    renderSequenceArtifact(host(container), a, "/api/v1/artifacts/forged");
    await vi.waitFor(() => expect(fallbackCards(container)).toHaveLength(1), { timeout: 5000 });
    expect(reads).toBe(1);
    expect(parseSequence).not.toHaveBeenCalled();
  });

  it("shows the read failure with retry, and the retry re-reads", async () => {
    let attempts = 0;
    setArtifactsFetch(async () => {
      attempts += 1;
      if (attempts === 1) return new Response("Internal Server Error", { status: 500 });
      return new Response("recovered text\n");
    });
    const container = new Dom("div");
    container.page = true;
    const a: ArtifactRow = { id: "flaky", filename: "notes.txt" };
    renderTextArtifact(host(container), a, "/api/v1/artifacts/flaky");
    await vi.waitFor(() => expect(fallbackCards(container)).toHaveLength(1), { timeout: 2000 });
    const card = fallbackCards(container)[0]!;
    expect(walk(card).some((node) => node.textContent === filesT("viewer.read.failed"))).toBe(true);
    const retry = walk(card).find(
      (node) => node.tagName === "button" && node.textContent === filesT("viewer.read.retry"),
    )!;
    retry.onclick?.();
    await vi.waitFor(
      () => expect(walk(container).some((node) => node.textContent === "recovered text\n")).toBe(true),
      { timeout: 2000 },
    );
    expect(attempts).toBe(2);
  });

  it("downloads the exact pinned version from the failure card", async () => {
    setArtifactsFetch(async () => new Response("boom", { status: 500 }));
    const container = new Dom("div");
    container.page = true;
    const a: ArtifactRow = {
      id: "pinned",
      filename: "paper.md",
      version_id: "v7",
      _exactVersion: true,
    };
    const url = artUrl(a);
    renderTextArtifact(host(container), a, url);
    await vi.waitFor(() => expect(fallbackCards(container)).toHaveLength(1), { timeout: 2000 });
    const card = fallbackCards(container)[0]!;
    const link = walk(card).find((node) => node.tagName === "a")!;
    expect(link.href).toBe("/api/v1/artifacts/versions/v7");
    expect(attrs(link, "download")).toBe("paper.md");
  });

  it("cancels the previous artifact's read when a new artifact opens", async () => {
    let aborted = 0;
    const reads: string[] = [];
    setArtifactsFetch(async (url, init) => {
      if (url === "/api/v1/renderers") {
        return new Response(JSON.stringify({ renderers: [] }));
      }
      if (url.endsWith("/renderer") || url.includes("/renderer?")) {
        const id = url.includes("/artifacts/A/") ? "A" : "B";
        return new Response(
          JSON.stringify({ artifact_id: id, matched_by: "compatibility", renderer: { renderer_id: "text" } }),
        );
      }
      reads.push(url);
      if (url.endsWith("/artifacts/A")) {
        return new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              init?.signal?.addEventListener("abort", () => {
                aborted += 1;
                controller.error(new DOMException("aborted", "AbortError"));
              });
            },
            pull() {
              /* waits for the abort */
            },
          }),
        );
      }
      return new Response("artifact B text\n");
    });
    const viewer = new Dom("div");
    viewer.page = true;
    const body = viewer.appendChild(new Dom("div"));
    renderArtifactBody(host(body), { id: "A", filename: "a.txt" });
    await vi.waitFor(() => expect(reads).toContain("/api/v1/artifacts/A"), { timeout: 2000 });
    renderArtifactBody(host(body), { id: "B", filename: "b.txt" });
    await vi.waitFor(() => expect(aborted).toBe(1), { timeout: 2000 });
    await vi.waitFor(
      () => expect(walk(body).some((node) => node.textContent === "artifact B text\n")).toBe(true),
      { timeout: 2000 },
    );
  });
});

function attrs(node: { attrs: Record<string, string> }, name: string): string {
  return node.attrs[name] || "";
}
