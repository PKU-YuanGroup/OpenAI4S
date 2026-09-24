import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreFields } from "../../stores/signal-field";
import { setArtifactsFetch } from "./api";
import { renderArtifactBody, renderDownloadArtifact, renderTextArtifact } from "./renderers";
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
