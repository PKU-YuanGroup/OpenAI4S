import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { artifactWorkbench } from "../../stores/notebook";
import { rendererDescriptors } from "../../stores/artifacts";
import { resetStoreFields } from "../../stores/signal-field";
import {
  bytes,
  CHEMISTRY_SOURCE_PREVIEW_CHARACTERS,
  LATEX_SOURCE_PREVIEW_CHARACTERS,
  MARKDOWN_PREVIEW_CHARACTERS,
  MOLECULE_FALLBACK_PREVIEW_CHARACTERS,
  PREVIEW_READ_MAX_BYTES,
  PREVIEW_READ_TIMEOUT_MS,
  TEXT_SOURCE_PREVIEW_CHARACTERS,
  abortPreviewReads,
  setArtifactsFetch,
  translate,
} from "./api";
import { artUrl } from "./cache";
import { compatibilityRendererDescriptor } from "./catalog";
import { filesT } from "./copy";
import { closeNotebookPreview } from "./notebook";
import {
  renderArtifactBody,
  renderArtifactDescriptor,
  renderDownloadArtifact,
  renderSequenceArtifact,
  renderTextArtifact,
} from "./renderers";
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

  it("rejects an over-budget structure file from its Content-Length without parsing it", async () => {
    const parseSequence = vi.fn();
    vi.stubGlobal("OpenAI4SScientificRenderers", { parseSequence });
    let readers = 0;
    let cancelled = false;
    setArtifactsFetch(
      async () =>
        ({
          ok: true,
          status: 200,
          headers: new Headers({ "content-length": String(9 * 1024 * 1024) }),
          body: {
            cancel: async () => {
              cancelled = true;
            },
            getReader: () => {
              readers += 1;
              throw new Error("the body must not be read");
            },
          },
        }) as unknown as Response,
    );
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
    expect(readers).toBe(0);
    expect(cancelled).toBe(true);
    expect(parseSequence).not.toHaveBeenCalled();
    const card = fallbackCards(container)[0]!;
    expect(
      walk(card).some((node) => node.textContent === filesT("viewer.read.limit", bytes(PREVIEW_READ_MAX_BYTES))),
    ).toBe(true);
    // The exact-version download is always offered, and retry is one click.
    expect(walk(card).filter((node) => node.tagName === "a").map((node) => node.href)).toContain(
      "/api/v1/artifacts/big",
    );
    expect(walk(card).some((node) => node.tagName === "button" && node.textContent === filesT("viewer.read.retry"))).toBe(true);
  });

  it("stops a forged small Content-Length while streaming and never parses the prefix", async () => {
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
        { headers: { "content-length": "16" } },
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
    expect(
      walk(card).some((node) => node.textContent === filesT("viewer.read.failedStatus", 500)),
    ).toBe(true);
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


describe("visible truncation and frozen preview version (A3)", () => {
  class Dom3 {
    children: Dom3[] = [];
    parent: Dom3 | null = null;
    page = false;
    className = "";
    textContent = "";
    html = "";
    href = "";
    attrs: Record<string, string> = {};
    dataset: Record<string, string> = {};
    style: Record<string, string> = {};
    classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
    onclick: (() => void) | null = null;
    tagName: string;
    constructor(tag = "div") {
      this.tagName = tag.toUpperCase();
    }
    remove(): void {
      if (this.parent) this.parent.children = this.parent.children.filter((node) => node !== this);
      this.parent = null;
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
    appendChild(child: Dom3): Dom3 {
      child.parent = this;
      this.children.push(child);
      return child;
    }
    setAttribute(name: string, value: string): void {
      this.attrs[name] = value;
    }
  }
  const walk = (node: Dom3): Dom3[] => [node, ...node.children.flatMap(walk)];
  const host = (node: Dom3) => node as unknown as HTMLElement;
  const partials = (root: Dom3): Dom3[] =>
    walk(root).filter(
      (node) =>
        (node.className || "").includes("renderer-completeness") &&
        node.attrs["data-state"] === "partial",
    );
  const links = (root: Dom3): Dom3[] => walk(root).filter((node) => node.tagName === "A");
  const descriptor = (rendererId: string, versionId?: string) => ({
    artifact_id: "a",
    version_id: versionId,
    matched_by: "extension",
    renderer: { renderer_id: rendererId, label: rendererId },
  });
  const contentClass: Record<string, string> = {
    markdown: "md renderer-markdown",
    "chemistry-2d": "chemistry-view",
    latex: "latex-view",
    "molecule-3d": "",
  };

  beforeEach(() => {
    resetStoreFields();
    abortPreviewReads();
    vi.stubGlobal("document", { createElement: (tag: string) => new Dom3(tag) });
  });
  afterEach(() => {
    abortPreviewReads();
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
    resetStoreFields();
  });

  async function renderBody(
    rendererId: string,
    filename: string,
    body: string,
    extras: Record<string, unknown> = {},
    versionId = "v1",
  ): Promise<Dom3> {
    setArtifactsFetch(async () => new Response(body));
    const root = new Dom3("div");
    root.page = true;
    renderArtifactDescriptor(
      host(root),
      { id: "a", filename, version_id: versionId, ...extras },
      descriptor(rendererId, versionId),
    );
    const expected = contentClass[rendererId] ?? "";
    await vi.waitFor(
      () =>
        expect(
          walk(root).some(
            (node) =>
              expected === "" ? node.tagName === "PRE" : (node.className || "").includes(expected),
          ),
        ).toBe(true),
      { timeout: 3000 },
    );
    return root;
  }

  const lengths: Array<[string, number, boolean]> = [
    ["below", -1, false],
    ["at", 0, false],
    ["above", 1, true],
  ];

  it.each(lengths)("markdown %s its 1,000,000 code-unit limit", async (_label, offset, truncated) => {
    const length = MARKDOWN_PREVIEW_CHARACTERS + offset;
    const root = await renderBody("markdown", "r.md", "a".repeat(length));
    const notices = partials(root);
    if (!truncated) {
      expect(notices).toHaveLength(0);
      expect(links(root)).toHaveLength(0);
      return;
    }
    // Two notices: above and below the rendered body, both persistent.
    expect(notices).toHaveLength(2);
    expect(notices.every((note) => note.attrs.role === "status")).toBe(true);
    expect(notices[0]!.textContent).toBe(
      filesT("viewer.preview.partial", MARKDOWN_PREVIEW_CHARACTERS.toLocaleString()),
    );
    const order = walk(root);
    const first = order.indexOf(notices[0]!);
    const md = order.findIndex((node) => (node.className || "").includes("renderer-markdown"));
    const last = order.indexOf(notices[1]!);
    expect(first).toBeLessThan(md);
    expect(md).toBeLessThan(last);
    expect(links(root).map((node) => node.href)).toEqual(["/api/v1/artifacts/versions/v1"]);
  });

  it("keeps exactly the limit on a markdown cut and never splits an emoji", async () => {
    const body = "a".repeat(MARKDOWN_PREVIEW_CHARACTERS - 1) + "🙂" + "tail";
    const root = await renderBody("markdown", "r.md", body);
    const notices = partials(root);
    expect(notices).toHaveLength(2);
    // The high surrogate sits at limit-1, so the shown prefix is one unit shorter.
    const shown = MARKDOWN_PREVIEW_CHARACTERS - 1;
    expect(notices[0]!.textContent).toBe(filesT("viewer.preview.partial", shown.toLocaleString()));
    const markdown = walk(root).find((node) => (node.className || "").includes("renderer-markdown"))!;
    expect(markdown.innerHTML).not.toMatch(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/);
  });

  it("does not invent a closed code fence when markdown is cut", async () => {
    const prefix = "```\n";
    const body = prefix + "a".repeat(MARKDOWN_PREVIEW_CHARACTERS + 1 - prefix.length);
    const root = await renderBody("markdown", "r.md", body);
    const notices = partials(root);
    expect(notices).toHaveLength(2);
    expect(notices[0]!.textContent).toBe(
      filesT("viewer.preview.partial", MARKDOWN_PREVIEW_CHARACTERS.toLocaleString()),
    );
  });

  it.each(lengths)("chemistry source %s its 300,000 code-unit limit", async (_label, offset, truncated) => {
    vi.stubGlobal("OpenAI4SScientificRenderers", {
      parseMolfile: () => null,
      smilesLines: () => [],
    });
    const root = await renderBody("chemistry-2d", "m.mol", "C".repeat(CHEMISTRY_SOURCE_PREVIEW_CHARACTERS + offset));
    const notices = partials(root);
    expect(notices).toHaveLength(truncated ? 1 : 0);
    if (truncated) {
      expect(notices[0]!.attrs.role).toBe("status");
      expect(notices[0]!.textContent).toBe(
        filesT("viewer.preview.partial", CHEMISTRY_SOURCE_PREVIEW_CHARACTERS.toLocaleString()),
      );
      expect(links(root).map((node) => node.href)).toContain("/api/v1/artifacts/versions/v1");
    } else {
      expect(links(root)).toHaveLength(0);
    }
  });

  it.each(lengths)("LaTeX source %s its 500,000 code-unit limit", async (_label, offset, truncated) => {
    vi.stubGlobal("OpenAI4SScientificRenderers", { latexPreview: () => [] });
    const root = await renderBody("latex", "p.tex", "x".repeat(LATEX_SOURCE_PREVIEW_CHARACTERS + offset));
    const notices = partials(root);
    expect(notices).toHaveLength(truncated ? 1 : 0);
    if (truncated) {
      expect(notices[0]!.textContent).toBe(
        filesT("viewer.preview.partial", LATEX_SOURCE_PREVIEW_CHARACTERS.toLocaleString()),
      );
      expect(links(root).map((node) => node.href)).toContain("/api/v1/artifacts/versions/v1");
    } else {
      expect(links(root)).toHaveLength(0);
    }
  });

  it.each(lengths)("the 3D fallback %s its 8,000 code-unit limit", async (_label, offset, truncated) => {
    const root = await renderBody("molecule-3d", "s.pdb", "A".repeat(MOLECULE_FALLBACK_PREVIEW_CHARACTERS + offset));
    const notices = partials(root);
    expect(notices).toHaveLength(truncated ? 1 : 0);
    if (truncated) {
      expect(notices[0]!.textContent).toBe(
        filesT("viewer.preview.partial", MOLECULE_FALLBACK_PREVIEW_CHARACTERS.toLocaleString()),
      );
      expect(links(root).map((node) => node.href)).toContain("/api/v1/artifacts/versions/v1");
    } else {
      expect(links(root)).toHaveLength(0);
    }
  });

  it.each([
    ["below", -1, false],
    ["at", 0, false],
    ["above", 1, true],
  ])("raw text source %s its 300,000 code-unit limit", async (_label, offset, truncated) => {
    const length = TEXT_SOURCE_PREVIEW_CHARACTERS + offset;
    const root = await renderBody("text", "notes.txt", "z".repeat(length));
    const pre = walk(root).find((node) => (node.className || "").includes("renderer-source"))!;
    expect(pre.textContent.length).toBe(truncated ? TEXT_SOURCE_PREVIEW_CHARACTERS : length);
    const notices = partials(root);
    if (!truncated) {
      expect(notices).toHaveLength(0);
      return;
    }
    expect(notices).toHaveLength(1);
    expect(notices[0]!.textContent).toBe(
      filesT("viewer.preview.partial", TEXT_SOURCE_PREVIEW_CHARACTERS.toLocaleString()),
    );
    // The raw-source preview keeps its "show complete text" expansion.
    expect(
      walk(root).some(
        (node) => node.tagName === "BUTTON" && node.textContent === filesT("viewer.text.expand"),
      ),
    ).toBe(true);
    expect(links(root).map((node) => node.href)).toContain("/api/v1/artifacts/versions/v1");
  });

  it("freezes the preview and download to the row's version, not the descriptor's", async () => {
    const atLimit = "a".repeat(MARKDOWN_PREVIEW_CHARACTERS + 1);
    const root = new Dom3("div");
    root.page = true;
    setArtifactsFetch(async () => new Response(atLimit));
    // The row remembers v1; the descriptor (a later paint source) reports v2.
    renderArtifactDescriptor(
      host(root),
      { id: "a", filename: "r.md", version_id: "v1" },
      descriptor("markdown", "v2"),
    );
    await vi.waitFor(() => expect(links(root)).toHaveLength(1), { timeout: 3000 });
    const firstDownload = links(root)[0]!;
    expect(firstDownload.href).toBe("/api/v1/artifacts/versions/v1");
    const version = walk(root).find((node) => (node.className || "") === "renderer-version");
    expect(version?.textContent).toBe(translate("viewer.renderer.version", "v1"));
    // A new version published later does not move this paint's download.
    renderArtifactDescriptor(
      host(root),
      { id: "a", filename: "r.md", version_id: "v2" },
      descriptor("markdown", "v2"),
    );
    await vi.waitFor(
      () => expect(links(root)[0]?.href).toBe("/api/v1/artifacts/versions/v2"),
      { timeout: 3000 },
    );
    expect(firstDownload.href).toBe("/api/v1/artifacts/versions/v1");
  });

  it("reads latest and says so for a row with no version identity", async () => {
    const atLimit = "a".repeat(MARKDOWN_PREVIEW_CHARACTERS + 1);
    setArtifactsFetch(async () => new Response(atLimit));
    const root = new Dom3("div");
    root.page = true;
    renderArtifactDescriptor(host(root), { id: "a", filename: "r.md" }, descriptor("markdown", "v9"));
    await vi.waitFor(() => expect(links(root)).toHaveLength(1), { timeout: 3000 });
    const version = walk(root).find((node) => (node.className || "") === "renderer-version");
    expect(version?.textContent).toBe(filesT("viewer.preview.latest"));
    expect(links(root)[0]!.href).toBe("/api/v1/artifacts/a");
  });

  it("does not paint a late markdown result into the replaced preview", async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    setArtifactsFetch(async (url) => {
      if (url.endsWith("/artifacts/A")) {
        await held;
        return new Response("# artifact A\n");
      }
      return new Response("# artifact B\n");
    });
    const viewer = new Dom3("div");
    viewer.page = true;
    const body = viewer.appendChild(new Dom3("div"));
    renderArtifactDescriptor(
      host(body),
      { id: "A", filename: "a.md", version_id: "v1" },
      descriptor("markdown", "v1"),
    );
    body.remove();
    const bodyB = viewer.appendChild(new Dom3("div"));
    renderArtifactDescriptor(
      host(bodyB),
      { id: "B", filename: "b.md", version_id: "v1" },
      descriptor("markdown", "v1"),
    );
    await vi.waitFor(
      () => expect(walk(bodyB).some((node) => node.className === "md renderer-markdown")).toBe(true),
      { timeout: 3000 },
    );
    release();
    for (let i = 0; i < 8; i++) await new Promise<void>((resolve) => setTimeout(resolve, 0));
    expect(walk(body).some((node) => node.className === "md renderer-markdown")).toBe(false);
    expect(walk(bodyB).some((node) => node.className === "md renderer-markdown")).toBe(true);
  });
});


describe("notebook artifact routing (A3)", () => {
  class Dom3 {
    children: Dom3[] = [];
    parent: Dom3 | null = null;
    page = false;
    className = "";
    textContent = "";
    href = ""; html = ""; src = ""; alt = "";
    attrs: Record<string, string> = {};
    dataset: Record<string, string> = {};
    style: Record<string, string> = {};
    classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
    onclick: (() => void) | null = null;
    constructor(readonly tagName = "div") {}
    get isConnected(): boolean {
      return this.page || (!!this.parent && this.parent.isConnected);
    }
    get innerHTML(): string { return this.html; }
    set innerHTML(value: string) {
      for (const child of this.children) child.parent = null;
      this.children = [];
      this.html = value;
    }
    appendChild(child: Dom3): Dom3 {
      child.parent = this;
      this.children.push(child);
      return child;
    }
    setAttribute(name: string, value: string): void { this.attrs[name] = value; }
  }
  const walk3 = (node: Dom3): Dom3[] => [node, ...node.children.flatMap(walk3)];
  const host3 = (node: Dom3) => node as unknown as HTMLElement;

  beforeEach(() => {
    resetStoreFields();
    vi.stubGlobal("document", { createElement: (tag: string) => new Dom3(tag) });
  });
  afterEach(() => {
    closeNotebookPreview();
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
    resetStoreFields();
  });

  it("routes a notebook descriptor to the read-only preview with one exact-version read", async () => {
    const calls: string[] = [];
    setArtifactsFetch(async (url) => {
      calls.push(String(url));
      return new Response(
        JSON.stringify({
          nbformat: 4,
          metadata: { kernelspec: { language: "python" } },
          cells: [
            { cell_type: "markdown", source: "# Saved report" },
            { cell_type: "code", source: "1 + 1", outputs: [{ output_type: "stream", name: "stdout", text: "2\n" }] },
          ],
        }),
      );
    });
    const body = new Dom3();
    body.page = true;
    renderArtifactDescriptor(
      host3(body),
      { id: "nb", filename: "session.python.ipynb", version_id: "v1" },
      { artifact_id: "nb", version_id: "v1", matched_by: "extension", renderer: { renderer_id: "notebook" } },
    );
    await vi.waitFor(
      () => expect(walk3(body).some((node) => (node.className || "").includes("notebook-preview"))).toBe(true),
      { timeout: 3000 },
    );
    expect(calls).toEqual(["/api/v1/artifacts/versions/v1"]);
    expect(calls.some((url) => /kernel|execute|\/host|\.ipynb\?/.test(url))).toBe(false);
    const markdown = walk3(body).find((node) => (node.className || "").includes("notebook-markdown"));
    expect(markdown?.innerHTML).toContain("<h1>Saved report</h1>");
    expect(filesT("notebook.readonly")).toBeTruthy();
  });

  it("falls back to the notebook renderer for an .ipynb compatibility descriptor", () => {
    const descriptor = compatibilityRendererDescriptor({ id: "nb", filename: "a.ipynb" });
    expect(descriptor.renderer?.renderer_id).toBe("notebook");
  });
});


describe("saved science-search rendering (A4)", () => {
  class Dom4 {
    children: Dom4[] = [];
    parent: Dom4 | null = null;
    page = false;
    className = "";
    textContent = "";
    html = "";
    href = "";
    hidden = false;
    alt = "";
    target = "";
    rel = "";
    attrs: Record<string, string> = {};
    dataset: Record<string, string> = {};
    style: Record<string, string> = {};
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
    get isConnected(): boolean {
      return this.page || (!!this.parent && this.parent.isConnected);
    }
    get innerHTML(): string { return this.html; }
    set innerHTML(value: string) {
      for (const child of this.children) child.parent = null;
      this.children = [];
      this.html = value;
    }
    appendChild(child: Dom4): Dom4 {
      child.parent = this;
      this.children.push(child);
      return child;
    }
    setAttribute(name: string, value: string): void { this.attrs[name] = value; }
  }
  const walk4 = (node: Dom4): Dom4[] => [node, ...node.children.flatMap(walk4)];
  const host4 = (node: Dom4) => node as unknown as HTMLElement;
  const envelope = JSON.stringify({
    query: "tp53",
    endpoint: "https://api.example.org/search?q=tp53&key=SECRET",
    retrieved_at: "2026-10-20T10:00:00Z",
    source_checksum: "a".repeat(64),
    result: {
      database: "uniprot",
      results: [{ id: "P04637", title: "p53", url: "https://www.uniprot.org/uniprotkb/P04637" }],
    },
  });

  beforeEach(() => {
    resetStoreFields();
    vi.stubGlobal("document", { createElement: (tag: string) => new Dom4(tag) });
  });
  afterEach(() => {
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
    resetStoreFields();
  });

  it("routes a saved search to the read-only result view on the exact version", async () => {
    setArtifactsFetch(async () => new Response(envelope));
    const container = new Dom4();
    container.page = true;
    renderTextArtifact(
      host4(container),
      { id: "search", filename: "science-uniprot.json", version_id: "v1", _exactVersion: true },
      "/api/v1/artifacts/versions/v1",
    );
    await vi.waitFor(
      () => expect(walk4(container).some((node) => node.className === "science-results")).toBe(true),
      { timeout: 3000 },
    );
    expect(walk4(container).some((node) => node.className.includes("science-json"))).toBe(false);
    const jsonTab = walk4(container).find((node) => node.textContent === filesT("science.tab.json"))!;
    jsonTab.onclick?.();
    expect(walk4(container).some((node) => node.className.includes("science-json"))).toBe(true);
    expect(walk4(container).filter((node) => node.tagName === "a").map((node) => node.href)).toContain(
      "/api/v1/artifacts/versions/v1",
    );
  });

  it("keeps unrelated JSON with a result property on the generic source path", async () => {
    setArtifactsFetch(async () => new Response(JSON.stringify({ result: { a: 1 } })));
    const container = new Dom4();
    container.page = true;
    renderTextArtifact(
      host4(container),
      { id: "other", filename: "other.json", version_id: "v1", _exactVersion: true },
      "/api/v1/artifacts/versions/v1",
    );
    await vi.waitFor(
      () => expect(walk4(container).some((node) => node.className === "renderer-source")).toBe(true),
      { timeout: 3000 },
    );
    expect(walk4(container).some((node) => node.className === "science-results")).toBe(false);
  });
});

describe("every preview entry fails visibly and retries (A1)", () => {
  class PreviewNode {
    children: PreviewNode[] = [];
    parent: PreviewNode | null = null;
    page = false;
    className = "";
    textContent = "";
    html = "";
    href = "";
    disabled = false;
    style: Record<string, string> = {};
    attrs: Record<string, string> = {};
    dataset: Record<string, string> = {};
    classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
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
    appendChild(child: PreviewNode): PreviewNode {
      child.parent = this;
      this.children.push(child);
      return child;
    }
    setAttribute(name: string, value: string): void {
      this.attrs[name] = value;
    }
  }
  const walk = (node: PreviewNode): PreviewNode[] => [node, ...node.children.flatMap(walk)];
  const texts = (root: PreviewNode): string[] =>
    walk(root).map((node) => node.textContent).filter(Boolean);
  const host = (node: PreviewNode) => node as unknown as HTMLElement;
  const row: ArtifactRow = { id: "a", filename: "artifact.dat", version_id: "v1" };
  const descriptor = (rendererId: string) => ({
    artifact_id: "a",
    version_id: "v1",
    matched_by: "extension",
    trusted_html: false,
    renderer: { renderer_id: rendererId, label: rendererId },
  });
  const flush = async (rounds = 12): Promise<void> => {
    for (let i = 0; i < rounds; i++) await Promise.resolve();
  };
  const successNode = (root: PreviewNode, predicate: (node: PreviewNode) => boolean): boolean =>
    walk(root).some(predicate);
  const cases: Array<{
    id: string;
    body: string;
    success: (node: PreviewNode) => boolean;
  }> = [
    { id: "markdown", body: "# hello\n", success: (n) => (n.className || "").includes("renderer-markdown") },
    { id: "text", body: "hello\n", success: (n) => (n.className || "").includes("renderer-source") },
    { id: "sequence", body: ">a\nACD\n", success: (n) => (n.className || "").includes("bio-summary") },
    { id: "msa", body: ">a\nACD\n", success: (n) => (n.className || "").includes("msa-viewport") },
    { id: "genome-track", body: "chr1\t0\t10\n", success: (n) => (n.className || "").includes("genome-tracks") },
    { id: "chemistry-2d", body: "C\n", success: (n) => (n.className || "").includes("chemistry-view") },
    { id: "latex", body: "\\section{x}\n", success: (n) => (n.className || "").includes("latex-view") },
    { id: "molecule-3d", body: "ATOM      1  CA  ALA A   1      1.000   2.000   3.000", success: (n) => n.tagName === "PRE" },
    { id: "table", body: "a,b\n1,2\n", success: (n) => n.tagName === "TABLE" },
  ];

  beforeEach(() => {
    resetStoreFields();
    // Direct renderer calls bypass renderArtifactBody; drop any View-era read
    // controller a previous test left behind so its aborted signal is not reused.
    abortPreviewReads();
    vi.stubGlobal("document", {
      createElement: (tag: string) => new PreviewNode(tag),
      createElementNS: (_ns: string, tag: string) => new PreviewNode(tag),
      createDocumentFragment: () => new PreviewNode("#fragment"),
      createTextNode: (text: string) => {
        const node = new PreviewNode("#text");
        node.textContent = text;
        return node;
      },
    });
    vi.stubGlobal("OpenAI4SScientificRenderers", {
      parseSequence: (text: string) => ({
        format: "fasta",
        alphabet: "protein",
        total_length: text.length,
        records: [{ name: "a", sequence: "ACD" }],
      }),
      parseAlignment: () => ({
        format: "a3m",
        columns: 3,
        alphabet: "protein",
        records: [{ name: "a", sequence: "ACD" }],
      }),
      parseGenome: () => ({
        format: "bed",
        invalid: 0,
        features: [{ chrom: "chr1", start: 0, end: 10, label: "g", type: "gene" }],
        chromosomes: [{ chrom: "chr1", start: 0, end: 10, count: 1 }],
      }),
      parseMolfile: () => null,
      smilesLines: () => [],
      latexPreview: () => [{ kind: "paragraph", text: "x" }],
      residueClass: () => "other",
      rendererIdFromDescriptor: () => "download",
    });
  });
  afterEach(() => {
    vi.useRealTimers();
    abortPreviewReads();
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
    resetStoreFields();
  });

  const mount = (rendererId: string): PreviewNode => {
    const root = new PreviewNode("div");
    root.page = true;
    renderArtifactDescriptor(host(root), row, descriptor(rendererId));
    return root;
  };
  const card = (root: PreviewNode): PreviewNode | undefined | null =>
    walk(root).find((node) => node.className === "renderer-fallback");
  const retryButton = (root: PreviewNode): PreviewNode | undefined =>
    walk(root).find((node) => node.tagName === "BUTTON" && node.textContent === filesT("viewer.read.retry"));
  const downloadHref = (root: PreviewNode): string | undefined => {
    const link = walk(root).find(
      (node) => node.tagName === "A" && (node.href || node.attrs.href),
    );
    return link ? link.href || link.attrs.href : undefined;
  };

  it.each(cases)("$id reports a timeout with retry and the exact-version download", async ({ id, body, success }) => {
    vi.useFakeTimers();
    setArtifactsFetch(
      (_url, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    const root = mount(id);
    await flush();
    await vi.advanceTimersByTimeAsync(PREVIEW_READ_TIMEOUT_MS + 50);
    await flush();
    const failure = card(root);
    expect(failure, `${id} has no failure card`).toBeTruthy();
    expect(texts(failure!)).toContain(filesT("viewer.read.timeout"));
    expect(downloadHref(root)).toBe("/api/v1/artifacts/versions/v1");
    const retry = retryButton(root);
    expect(retry, `${id} has no retry`).toBeTruthy();
    setArtifactsFetch(async () => new Response(body));
    retry!.onclick?.();
    await flush();
    expect(successNode(root, success), `${id} did not recover`).toBe(true);
  });

  it.each(cases)("$id reports the read limit with retry and the exact-version download", async ({ id, body, success }) => {
    setArtifactsFetch(
      async () =>
        ({
          ok: true,
          status: 200,
          headers: new Headers({ "content-length": String(9 * 1024 * 1024) }),
          body: {
            cancel: async () => {},
            getReader: () => {
              throw new Error("the body must not be read");
            },
          },
        }) as unknown as Response,
    );
    const root = mount(id);
    await flush();
    const failure = card(root);
    expect(failure, `${id} has no failure card`).toBeTruthy();
    expect(texts(failure!)).toContain(
      filesT("viewer.read.limit", bytes(PREVIEW_READ_MAX_BYTES)),
    );
    expect(downloadHref(root)).toBe("/api/v1/artifacts/versions/v1");
    const retry = retryButton(root);
    expect(retry, `${id} has no retry`).toBeTruthy();
    setArtifactsFetch(async () => new Response(body));
    retry!.onclick?.();
    await flush();
    expect(successNode(root, success), `${id} did not recover`).toBe(true);
  });
});

describe("renderer catalog recovery in the viewer (A2)", () => {
  class CatNode {
    children: CatNode[] = [];
    parent: CatNode | null = null;
    page = false;
    className = "";
    textContent = "";
    html = "";
    href = "";
    disabled = false;
    title = "";
    style: Record<string, string> = {};
    attrs: Record<string, string> = {};
    dataset: Record<string, string> = {};
    onclick: (() => void) | null = null;
    onchange: (() => void) | null = null;
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
    appendChild(child: CatNode): CatNode {
      child.parent = this;
      this.children.push(child);
      return child;
    }
    setAttribute(name: string, value: string): void {
      this.attrs[name] = value;
    }
  }
  const walk = (node: CatNode): CatNode[] => [node, ...node.children.flatMap(walk)];
  const host = (node: CatNode) => node as unknown as HTMLElement;
  const compatText = translate("viewer.renderer.compat");

  const runtime = {
    rendererIdFromDescriptor: (descriptor: { renderer?: { renderer_id?: string } }) =>
      descriptor.renderer?.renderer_id || "download",
    parseSequence: () => ({
      format: "fasta",
      alphabet: "protein",
      total_length: 3,
      records: [{ name: "a", sequence: "ACD" }],
    }),
    residueClass: () => "other",
  };

  beforeEach(() => {
    resetStoreFields();
    abortPreviewReads();
    vi.stubGlobal("document", {
      createElement: (tag: string) => new CatNode(tag),
      createElementNS: (_ns: string, tag: string) => new CatNode(tag),
      createDocumentFragment: () => new CatNode("#fragment"),
      createTextNode: (text: string) => {
        const node = new CatNode("#text");
        node.textContent = text;
        return node;
      },
    });
    vi.stubGlobal("OpenAI4SScientificRenderers", runtime);
  });
  afterEach(() => {
    abortPreviewReads();
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
    resetStoreFields();
  });

  const retryButton = (root: CatNode) =>
    walk(root).find(
      (node) => node.tagName === "BUTTON" && node.textContent === filesT("viewer.renderer.retry"),
    );
  const detailText = (root: CatNode) =>
    walk(root).find((node) => node.className === "renderer-detail")?.textContent;

  it("retries the catalog and descriptor from the compatibility info row without a reload", async () => {
    let catalogCalls = 0;
    let descriptorCalls = 0;
    let catalogDown = true;
    setArtifactsFetch(async (url) => {
      if (url === "/api/v1/renderers") {
        catalogCalls += 1;
        if (catalogDown) return new Response(JSON.stringify({ error: "unavailable" }), { status: 503 });
        return new Response(
          JSON.stringify({
            renderers: [{ renderer_id: "sequence", label: "Sequence", capabilities: ["view", "copy"] }],
          }),
        );
      }
      if (url === "/api/v1/artifacts/A/renderer?version=v1") {
        descriptorCalls += 1;
        return new Response(
          JSON.stringify({
            artifact_id: "A",
            version_id: "v1",
            matched_by: "extension",
            renderer: { renderer_id: "sequence", label: "Sequence" },
          }),
        );
      }
      if (url === "/api/v1/artifacts/versions/v1") {
        return new Response(">a\nACD\n");
      }
      throw new Error(`unexpected ${url}`);
    });
    const root = new CatNode("div");
    root.page = true;
    const artifact: ArtifactRow = { id: "A", filename: "pep.fasta", version_id: "v1" };
    renderArtifactBody(host(root), artifact);
    await vi.waitFor(() => expect(detailText(root)).toBe(compatText), { timeout: 3000 });
    // The failed catalog is not cached and the degraded descriptor is not kept.
    expect(catalogCalls).toBe(1);
    expect(Object.keys(rendererDescriptors.value)).toEqual([]);
    expect(retryButton(root), "compatibility row has no Retry").toBeTruthy();
    catalogDown = false;
    retryButton(root)!.onclick?.();
    await vi.waitFor(() => expect(detailText(root)).not.toBe(compatText), { timeout: 3000 });
    expect(catalogCalls).toBe(2);
    expect(descriptorCalls).toBe(2);
    // The retried server descriptor (and its declared capabilities) is cached.
    expect(Object.keys(rendererDescriptors.value)).toContain("A:v1");
    await vi.waitFor(
      () => expect(walk(root).some((node) => (node.className || "").includes("bio-summary"))).toBe(true),
      { timeout: 3000 },
    );
  });

  it("restores the table workbench Schema, Distribution and Export after a catalog retry", async () => {
    artifactWorkbench.value = true;
    let catalogDown = true;
    const profile = {
      artifact_id: "T",
      version_id: "v1",
      filtered_rows: 2,
      approximate: false,
      schema_version: 1,
      columns: [
        {
          name: "a",
          type: "integer",
          missing: 0,
          unique: 2,
          min: 0,
          max: 1,
          mean: 0.5,
          histogram: [
            { start: 0, end: 1, count: 1 },
            { start: 1, end: 2, count: 1 },
          ],
        },
        {
          name: "b",
          type: "integer",
          missing: 0,
          unique: 2,
          min: 0,
          max: 1,
          mean: 0.5,
          histogram: [
            { start: 0, end: 1, count: 1 },
            { start: 1, end: 2, count: 1 },
          ],
        },
      ],
    };
    setArtifactsFetch(async (url) => {
      if (url === "/api/v1/renderers") {
        if (catalogDown) return new Response(JSON.stringify({ error: "unavailable" }), { status: 503 });
        return new Response(
          JSON.stringify({
            renderers: [
              {
                renderer_id: "table",
                label: "Data table",
                capabilities: ["view", "sort", "filter", "profile", "export"],
              },
            ],
          }),
        );
      }
      if (url === "/api/v1/artifacts/T/renderer?version=v1") {
        return new Response(
          JSON.stringify({
            artifact_id: "T",
            version_id: "v1",
            matched_by: "extension",
            renderer: { renderer_id: "table", label: "Data table" },
          }),
        );
      }
      if (url.includes("/artifacts/T/table/profile?")) {
        return new Response(JSON.stringify(profile));
      }
      if (new URL(url, "http://localhost").pathname === "/api/v1/artifacts/T/table") {
        return new Response(
          JSON.stringify({
            artifact_id: "T",
            version_id: "v1",
            columns: ["a", "b"],
            rows: [["1", "2"]],
            total_rows: 1,
            offset: 0,
          }),
        );
      }
      throw new Error(`unexpected ${url}`);
    });
    const root = new CatNode("div");
    root.page = true;
    renderArtifactBody(host(root), { id: "T", filename: "counts.csv", version_id: "v1" });
    await vi.waitFor(() => expect(detailText(root)).toBe(compatText), { timeout: 3000 });
    expect(walk(root).some((node) => node.className === "wb-table-schema")).toBe(false);
    catalogDown = false;
    retryButton(root)!.onclick?.();
    await vi.waitFor(
      () => expect(walk(root).some((node) => node.className === "wb-table-schema")).toBe(true),
      { timeout: 3000 },
    );
    const classes = walk(root).map((node) => node.className);
    expect(classes).toContain("wb-table-distribution");
    expect(classes).toContain("wb-table-export-zone");
  });
});
