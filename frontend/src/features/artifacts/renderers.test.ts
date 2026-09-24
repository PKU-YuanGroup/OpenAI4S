import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setArtifactsFetch } from "./api";
import { renderDownloadArtifact, renderTextArtifact } from "./renderers";
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
