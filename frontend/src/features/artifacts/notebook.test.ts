import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreFields } from "../../stores/signal-field";
import { abortPreviewReads, beginPreviewRead, setArtifactsFetch } from "./api";
import { filesT } from "./copy";
import {
  inspectPng,
  NOTEBOOK_CELL_BATCH,
  NOTEBOOK_MAX_CELLS,
  NOTEBOOK_MAX_OUTPUTS_PER_CELL,
  NOTEBOOK_MAX_PNG_PIXELS,
  NOTEBOOK_PNG_MAX_BYTES,
  NOTEBOOK_TEXT_LIMIT_UNITS,
  parseNotebook,
  renderNotebookArtifact,
} from "./notebook";
import type { ArtifactRow } from "./types";

const { createObjectURL, revokeObjectURL } = vi.hoisted(() => {
  let created = 0;
  return {
    createObjectURL: vi.fn((_blob: unknown) => {
      created += 1;
      return "blob:notebook-" + created;
    }),
    revokeObjectURL: vi.fn(),
  };
});

let decodeCalls = 0;
let decodeActive = 0;
let decodePeak = 0;
let manualDecode = false;
const decodeResolvers: Array<() => void> = [];

function png(width: number, height: number, extra = 0): Uint8Array {
  const bytes = new Uint8Array(33 + extra);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 0);
  const view = new DataView(bytes.buffer);
  view.setUint32(8, 13);
  bytes.set([0x49, 0x48, 0x44, 0x52], 12);
  view.setUint32(16, width);
  view.setUint32(20, height);
  bytes[24] = 8;
  bytes[25] = 6;
  return bytes;
}

function base64(bytes: Uint8Array): string {
  let raw = "";
  for (const byte of bytes) raw += String.fromCharCode(byte);
  return btoa(raw);
}

function notebookDoc(cells: unknown[], metadata: unknown = {}): string {
  return JSON.stringify({ nbformat: 4, nbformat_minor: 5, metadata, cells });
}

const markdownCell = (source: unknown) => ({ cell_type: "markdown", source, metadata: {} });
const rawCell = (source: unknown) => ({ cell_type: "raw", source, metadata: {} });
const codeCell = (source: unknown, outputs: unknown[] = []) => ({
  cell_type: "code",
  source,
  outputs,
  metadata: {},
  execution_count: 1,
});
const streamOutput = (text: unknown, name = "stdout") => ({ output_type: "stream", name, text });
const errorOutput = (ename: string, evalue: string, traceback: string[] = []) => ({
  output_type: "error",
  ename,
  evalue,
  traceback,
});
const pngOutput = (width: number, height: number, extra = 0) => ({
  output_type: "display_data",
  data: { "image/png": base64(png(width, height, extra)) },
  metadata: {},
});
const displayOf = (data: Record<string, unknown>) => ({
  output_type: "display_data",
  data,
  metadata: {},
});

class FakeNode {
  children: FakeNode[] = [];
  parent: FakeNode | null = null;
  page = false;
  className = "";
  textContent = "";
  html = "";
  href = "";
  src = "";
  alt = "";
  hidden = false;
  disabled = false;
  onchange: (() => void) | null = null;
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
  appendChild(child: FakeNode): FakeNode {
    child.parent = this;
    this.children.push(child);
    return child;
  }
  setAttribute(name: string, value: string): void {
    this.attrs[name] = value;
  }
  remove(): void {
    if (this.parent) this.parent.children = this.parent.children.filter((node) => node !== this);
    this.parent = null;
  }
  contains(node: FakeNode | null): boolean {
    for (let at = node; at; at = at.parent) if (at === this) return true;
    return false;
  }
  decode(): Promise<void> {
    decodeCalls += 1;
    if (!manualDecode) return Promise.resolve();
    decodeActive += 1;
    decodePeak = Math.max(decodePeak, decodeActive);
    return new Promise<void>((resolve) => {
      decodeResolvers.push(() => {
        decodeActive -= 1;
        resolve();
      });
    });
  }
}

const walk = (node: FakeNode): FakeNode[] => [node, ...node.children.flatMap(walk)];
const texts = (node: FakeNode): string[] => walk(node).map((item) => item.textContent).filter(Boolean);
const host = (node: FakeNode) => node as unknown as HTMLElement;
const notebook: ArtifactRow = {
  id: "nb",
  filename: "session.python.ipynb",
  content_type: "application/x-ipynb+json",
  version_id: "v1",
  _exactVersion: true,
};
const url = "/api/v1/artifacts/versions/v1";
const settle = async (rounds = 8): Promise<void> => {
  for (let i = 0; i < rounds; i++) await Promise.resolve();
};

describe("nbformat-4 envelope validation (A4)", () => {
  it("accepts string and string-array sources/text and every supported output type", () => {
    const parsed = parseNotebook(
      notebookDoc(
        [
          markdownCell(["# Title\n", "body"]),
          rawCell("raw text"),
          codeCell("print(1)", [
            streamOutput(["one\n", "two\n"]),
            streamOutput("warning", "stderr"),
            errorOutput("ValueError", "bad", ["line 1"]),
            { output_type: "execute_result", data: { "text/plain": ["42"] }, metadata: {}, execution_count: 1 },
            displayOf({ "image/png": base64(png(10, 20)), "text/plain": "<Figure>" }),
          ]),
        ],
        { language_info: { name: "r" } },
      ),
    );
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.notebook.language).toBe("r");
    expect(parsed.notebook.cells).toHaveLength(3);
    const [markdown, raw, code] = parsed.notebook.cells;
    expect(markdown!.cellType).toBe("markdown");
    expect(markdown!.source).toBe("# Title\nbody");
    expect(raw!.cellType).toBe("raw");
    expect(code!.outputs.map((output) => output.kind)).toEqual([
      "text",
      "text",
      "text",
      "text",
      "png",
    ]);
    expect(code!.language).toBe("r");
  });

  it("takes the kernelspec language only when language_info has none", () => {
    const parsed = parseNotebook(notebookDoc([markdownCell("x")], { kernelspec: { language: "python" } }));
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.notebook.language).toBe("python");
    const none = parseNotebook(notebookDoc([markdownCell("x")]));
    expect(none.ok).toBe(true);
    if (!none.ok) return;
    expect(none.notebook.language).toBe("");
  });

  it("rejects damaged JSON, a wrong version, wrong field types and unknown enums", () => {
    const cases: Array<[string, string]> = [
      ["{not json", "invalid-json"],
      [JSON.stringify({ nbformat: 3, cells: [] }), "unsupported-version"],
      [JSON.stringify({ nbformat: "4", cells: [] }), "unsupported-version"],
      [JSON.stringify({ nbformat: 4, cells: "no" }), "invalid-shape"],
      [notebookDoc([42]), "invalid-shape"],
      [notebookDoc([{ cell_type: "script", source: "" }]), "invalid-shape"],
      [notebookDoc([markdownCell(42)]), "invalid-shape"],
      [notebookDoc([{ cell_type: "code", source: "x", outputs: "no" }]), "invalid-shape"],
      [notebookDoc([codeCell("x", [{ output_type: "bogus" }])]), "invalid-shape"],
      [notebookDoc([codeCell("x", [{ output_type: "stream", text: 42 }])]), "invalid-shape"],
      [notebookDoc([codeCell("x", [{ output_type: "display_data", data: "no" }])]), "invalid-shape"],
      [notebookDoc([codeCell("x", [{ output_type: "error", ename: 1, evalue: "x" }])]), "invalid-shape"],
    ];
    for (const [body, reason] of cases) {
      const parsed = parseNotebook(body);
      expect(parsed.ok, body).toBe(false);
      if (!parsed.ok) expect(parsed.reason).toBe(reason);
    }
  });

  it("caps cells, outputs and single text blocks with visible omission counts", () => {
    const over = parseNotebook(
      notebookDoc([
        codeCell("x", Array.from({ length: NOTEBOOK_MAX_OUTPUTS_PER_CELL + 2 }, () => streamOutput("o"))),
        ...Array.from({ length: NOTEBOOK_MAX_CELLS }, (_, index) => markdownCell("c" + index)),
      ]),
    );
    expect(over.ok).toBe(true);
    if (!over.ok) return;
    expect(over.notebook.cells).toHaveLength(NOTEBOOK_MAX_CELLS);
    expect(over.notebook.omittedCells).toBe(1);
    expect(over.notebook.cells[0]!.outputs).toHaveLength(NOTEBOOK_MAX_OUTPUTS_PER_CELL);
    expect(over.notebook.cells[0]!.omittedOutputs).toBe(2);

    const long = parseNotebook(
      notebookDoc([codeCell("x", [streamOutput("a".repeat(NOTEBOOK_TEXT_LIMIT_UNITS + 100))])]),
    );
    expect(long.ok).toBe(true);
    if (!long.ok) return;
    const output = long.notebook.cells[0]!.outputs[0]!;
    expect(output.kind).toBe("text");
    if (output.kind === "text") {
      expect(output.truncated).toBe(true);
      expect(output.text.length).toBe(NOTEBOOK_TEXT_LIMIT_UNITS);
    }
  });

  it("keeps a text cut from splitting an emoji", () => {
    const text = "a".repeat(NOTEBOOK_TEXT_LIMIT_UNITS - 1) + "🙂tail";
    const parsed = parseNotebook(notebookDoc([codeCell("x", [streamOutput(text)])]));
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    const output = parsed.notebook.cells[0]!.outputs[0]!;
    if (output.kind !== "text") throw new Error("expected text");
    expect(output.truncated).toBe(true);
    expect(output.text.length).toBe(NOTEBOOK_TEXT_LIMIT_UNITS - 1);
    const last = output.text.charCodeAt(output.text.length - 1);
    expect(last >= 0xd800 && last <= 0xdbff).toBe(false);
  });

  it("ignores metadata.trusted as authorization or truth", () => {
    const parsed = parseNotebook(
      notebookDoc([markdownCell("hello")], { trusted: true, kernelspec: { language: "python" } }),
    );
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(JSON.stringify(parsed.notebook)).not.toContain("trusted");
  });
});

describe("PNG validation before image creation (A4/F6)", () => {
  it("accepts a well-formed PNG and reports its dimensions", () => {
    const checked = inspectPng(base64(png(640, 480)));
    expect(checked.ok).toBe(true);
    if (!checked.ok) return;
    expect([checked.width, checked.height, checked.bytes.length]).toEqual([640, 480, 33]);
  });

  it("rejects malformed base64, a bad signature, a bad header, zero dimensions and >2 MiB", () => {
    expect(inspectPng("***not base64***")).toMatchObject({ ok: false, reason: "invalid-base64" });
    const notPng = png(1, 1);
    notPng[0] = 0;
    expect(inspectPng(base64(notPng))).toMatchObject({ ok: false, reason: "not-png" });
    const badHeader = png(1, 1);
    badHeader[12] = 0x58;
    expect(inspectPng(base64(badHeader))).toMatchObject({ ok: false, reason: "bad-header" });
    expect(inspectPng(base64(png(0, 1)))).toMatchObject({ ok: false, reason: "bad-dimensions" });
    expect(inspectPng(base64(png(1, 1, NOTEBOOK_PNG_MAX_BYTES)))).toMatchObject({
      ok: false,
      reason: "too-large",
    });
  });
});

describe("read-only notebook rendering (A4)", () => {
  let reads: string[];

  beforeEach(() => {
    resetStoreFields();
    reads = [];
    decodeCalls = 0;
    decodeActive = 0;
    decodePeak = 0;
    manualDecode = false;
    decodeResolvers.length = 0;
    vi.stubGlobal("document", {
      createElement: (tag: string) => new FakeNode(tag),
      createElementNS: (_ns: string, tag: string) => new FakeNode(tag),
      createDocumentFragment: () => new FakeNode("#fragment"),
      createTextNode: (text: string) => {
        const node = new FakeNode("#text");
        node.textContent = text;
        return node;
      },
    });
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    createObjectURL.mockClear();
    revokeObjectURL.mockClear();
  });
  afterEach(() => {
    abortPreviewReads();
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
    resetStoreFields();
  });

  async function mount(body: string): Promise<FakeNode> {
    setArtifactsFetch(async (target) => {
      reads.push(String(target));
      return new Response(body);
    });
    const root = new FakeNode("div");
    root.page = true;
    renderNotebookArtifact(host(root), notebook, url);
    await vi.waitFor(
      () =>
        expect(
          walk(root).some(
            (node) => (node.className || "") === "notebook-preview" || (node.className || "") === "renderer-fallback",
          ),
        ).toBe(true),
      { timeout: 3000 },
    );
    return root;
  }

  it("renders the read-only header, saved cells and the kernel language", async () => {
    const root = await mount(
      notebookDoc(
        [markdownCell("# Results"), codeCell("print('saved')", [streamOutput("saved\n")])],
        { language_info: { name: "python" } },
      ),
    );
    expect(reads).toEqual([url]);
    expect(texts(root)).toContain(filesT("notebook.readonly"));
    expect(walk(root).filter((node) => (node.className || "").startsWith("notebook-cell "))).toHaveLength(2);
    expect(texts(root)).toContain("saved\n");
    expect(reads).toHaveLength(1);
  });

  it("keeps the dock notebook and its images when a fullscreen notebook opens and closes", async () => {
    const doc = notebookDoc([codeCell("saved", [displayOf({ "image/png": base64(png(1, 1)) })])]);
    const dock = await mount(doc);
    await vi.waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
    const modal = await mount(doc);
    await vi.waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(2));
    expect(texts(dock)).toContain(filesT("notebook.readonly"));
    expect(revokeObjectURL).not.toHaveBeenCalled();
    abortPreviewReads(host(modal));
    expect(texts(dock)).toContain(filesT("notebook.readonly"));
    expect(revokeObjectURL).toHaveBeenCalledTimes(1);
    abortPreviewReads(host(dock));
    expect(revokeObjectURL).toHaveBeenCalledTimes(2);
  });

  it("renders code with the copy-enabled code block and markdown without resources", async () => {
    const markdown = [
      "# [title](https://example.com/page)",
      "",
      "![plot](data:image/png;base64," + base64(png(1, 1)) + ")",
      "",
      "![external](https://example.com/x.png)",
    ].join("\n");
    const root = await mount(
      notebookDoc([markdownCell(markdown), codeCell("x = 1\nprint(x)")], {
        kernelspec: { language: "python" },
      }),
    );
    const code = walk(root).find((node) => (node.className || "") === "notebook-code")!;
    expect(code.innerHTML).toContain("cb-copy");
    expect(code.innerHTML).toContain("<code>");
    expect(code.innerHTML).toContain("tok-kw");
    const md = walk(root).find((node) => (node.className || "") === "md notebook-markdown")!;
    expect(md.innerHTML).not.toContain("<img");
    expect(md.innerHTML).not.toContain("<a ");
    expect(md.innerHTML).toContain("title (https://example.com/page)");
    expect(md.innerHTML).toContain("plot");
    expect(walk(root).filter((node) => node.tagName === "IMG")).toHaveLength(0);
    expect(walk(root).filter((node) => node.tagName === "A")).toHaveLength(0);
  });

  it("shows plain text/error outputs and the unsupported MIME notice", async () => {
    const root = await mount(
      notebookDoc([
        codeCell("x", [
          streamOutput("[OpenAI4S artifacts] plot.png\n"),
          errorOutput("ValueError", "bad", ["Traceback line"]),
          displayOf({ "text/html": "<script>window.__nb=1</script>" }),
          displayOf({ "image/svg+xml": "<svg onload='x'/>" }),
          displayOf({ "application/javascript": "alert(1)" }),
          displayOf({ "application/vnd.jupyter.widget-view+json": { model_id: "m" } }),
        ]),
      ]),
    );
    expect(texts(root)).toContain("[OpenAI4S artifacts] plot.png\n");
    expect(texts(root)).toContain("ValueError: bad\nTraceback line");
    const prefix = filesT("notebook.mimeUnsupported").split("{0}")[0]!;
    expect(texts(root).filter((text) => text.startsWith(prefix))).toHaveLength(4);
    expect(walk(root).some((node) => node.tagName === "SCRIPT" || node.tagName === "IFRAME" || node.tagName === "SVG")).toBe(false);
    // The artifact line is only text: no request followed it.
    expect(reads).toEqual([url]);
  });

  it("creates a validated PNG with a blob URL and decodes it", async () => {
    const root = await mount(notebookDoc([codeCell("plot()", [pngOutput(120, 80)])]));
    await vi.waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
    const image = walk(root).find((node) => node.tagName === "IMG")!;
    expect(image.alt).toBe(filesT("notebook.pngAlt"));
    expect(image.src).toMatch(/^blob:notebook-\d+$/);
    expect(decodeCalls).toBe(1);
  });

  it("omits every image that fails the PNG rules instead of creating one", async () => {
    const badBase64 = displayOf({ "image/png": "!!!not-base64!!!" });
    const fakeSignature = png(1, 1);
    fakeSignature[0] = 0;
    const zeroWidth = png(0, 5);
    const overBytes = png(1, 1, NOTEBOOK_PNG_MAX_BYTES);
    const root = await mount(
      notebookDoc([
        codeCell("a", [badBase64]),
        codeCell("b", [displayOf({ "image/png": base64(fakeSignature) })]),
        codeCell("c", [displayOf({ "image/png": base64(zeroWidth) })]),
        codeCell("d", [displayOf({ "image/png": base64(overBytes) })]),
      ]),
    );
    await settle();
    expect(walk(root).filter((node) => node.tagName === "IMG")).toHaveLength(0);
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(texts(root).filter((text) => text === filesT("notebook.imageOmitted"))).toHaveLength(4);
  });

  it("omits images past the cumulative 16M pixel budget", async () => {
    const root = await mount(
      notebookDoc([
        codeCell("a", [pngOutput(3000, 3000)]),
        codeCell("b", [pngOutput(3000, 3000)]),
        codeCell("c", [pngOutput(3000, 3000)]),
      ]),
    );
    await vi.waitFor(() => expect(walk(root).filter((node) => node.tagName === "IMG")).toHaveLength(1));
    for (let i = 0; i < 10; i++) await new Promise<void>((resolve) => setTimeout(resolve, 0));
    expect(NOTEBOOK_MAX_PNG_PIXELS).toBe(16_000_000);
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(texts(root).filter((text) => text === filesT("notebook.imageOmitted"))).toHaveLength(2);
  });

  it("decodes at most two saved images at once", async () => {
    manualDecode = true;
    const cells = Array.from({ length: 5 }, (_, index) => codeCell("p" + index, [pngOutput(10, 10)]));
    const root = await mount(notebookDoc(cells));
    await vi.waitFor(() => expect(decodeCalls).toBe(2), { timeout: 3000 });
    expect(decodePeak).toBe(2);
    expect(walk(root).filter((node) => node.tagName === "IMG")).toHaveLength(2);
    // Resolve the first pair: the next pair starts, never more than two.
    decodeResolvers.splice(0).forEach((resolve) => resolve());
    await vi.waitFor(() => expect(decodeCalls).toBe(4), { timeout: 3000 });
    expect(decodePeak).toBe(2);
    decodeResolvers.splice(0).forEach((resolve) => resolve());
    await vi.waitFor(() => expect(decodeCalls).toBe(5), { timeout: 3000 });
    decodeResolvers.splice(0).forEach((resolve) => resolve());
    await vi.waitFor(() => expect(walk(root).filter((node) => node.tagName === "IMG")).toHaveLength(5));
    expect(createObjectURL).toHaveBeenCalledTimes(5);
  });

  it("renders cells in batches of fifty up to two hundred", async () => {
    const cells = Array.from({ length: NOTEBOOK_CELL_BATCH * 2 + 20 }, (_, index) => markdownCell("c" + index));
    const root = await mount(notebookDoc(cells));
    const cellNodes = () => walk(root).filter((node) => (node.className || "").startsWith("notebook-cell "));
    expect(cellNodes()).toHaveLength(NOTEBOOK_CELL_BATCH);
    const more = () =>
      walk(root).find(
        (node) => node.tagName === "BUTTON" && (node.textContent || "").startsWith(filesT("notebook.showMore").split("{0}")[0]!),
      ) as FakeNode;
    expect(more().textContent).toBe(filesT("notebook.showMore", "70"));
    more().onclick?.();
    expect(cellNodes()).toHaveLength(100);
    more().onclick?.();
    expect(cellNodes()).toHaveLength(120);
    expect(more().hidden).toBe(true);
  });

  it("caps the rendered cells at 200 and offers the original download", async () => {
    const cells = Array.from({ length: NOTEBOOK_MAX_CELLS + 5 }, (_, index) => markdownCell("c" + index));
    const root = await mount(notebookDoc(cells));
    for (let batch = 0; batch < 4; batch++) {
      const button = walk(root).find(
        (node) =>
          node.tagName === "BUTTON" &&
          !node.hidden &&
          (node.textContent || "").startsWith(filesT("notebook.showMore").split("{0}")[0]!),
      ) as FakeNode | undefined;
      button?.onclick?.();
    }
    expect(walk(root).filter((node) => (node.className || "").startsWith("notebook-cell "))).toHaveLength(
      NOTEBOOK_MAX_CELLS,
    );
    expect(texts(root)).toContain(filesT("notebook.cellsOmitted", 5));
    expect(walk(root).filter((node) => node.tagName === "A").map((node) => node.href)).toContain(url);
  });

  it("fails visibly for a damaged file and still offers the original text and download", async () => {
    const root = await mount("{not json");
    expect(texts(root)).toContain(filesT("notebook.problem.invalid"));
    expect(texts(root)).toContain(filesT("notebook.originalText"));
    expect(texts(root)).toContain("{not json");
    expect(walk(root).filter((node) => node.tagName === "A").map((node) => node.href)).toContain(url);
  });

  it("shows a classified read failure with retry", async () => {
    let attempts = 0;
    setArtifactsFetch(async () => {
      attempts += 1;
      return attempts === 1
        ? new Response("boom", { status: 500 })
        : new Response(notebookDoc([markdownCell("ok")]));
    });
    const root = new FakeNode("div");
    root.page = true;
    renderNotebookArtifact(host(root), notebook, url);
    await vi.waitFor(() => expect(walk(root).some((node) => node.className === "renderer-fallback")).toBe(true));
    const retry = walk(root).find(
      (node) => node.tagName === "BUTTON" && node.textContent === filesT("viewer.read.retry"),
    )!;
    retry.onclick?.();
    await vi.waitFor(() => expect(walk(root).some((node) => node.className === "notebook-preview")).toBe(true));
    expect(attempts).toBe(2);
  });

  it("aborts the read and revokes image URLs when only the render root is closed", async () => {
    let aborted = false;
    setArtifactsFetch(
      (_url, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            aborted = true;
            reject(new DOMException("aborted", "AbortError"));
          });
        }),
    );
    // The close paths only know the render root (`renderArtifactBody`'s body),
    // never the notebook's own container: one `abortPreviewReads` must do.
    const root = new FakeNode("div");
    root.page = true;
    beginPreviewRead(host(root));
    renderNotebookArtifact(host(root.appendChild(new FakeNode("div"))), notebook, url);
    await settle();
    expect(aborted).toBe(false);
    abortPreviewReads(host(root));
    await settle();
    expect(aborted).toBe(true);

    // A painted preview's image URLs are released by the same call.
    setArtifactsFetch(async () => new Response(notebookDoc([codeCell("plot()", [pngOutput(10, 10)])])));
    const painted = new FakeNode("div");
    painted.page = true;
    beginPreviewRead(host(painted));
    renderNotebookArtifact(host(painted.appendChild(new FakeNode("div"))), notebook, url);
    await vi.waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
    abortPreviewReads(host(painted));
    expect(revokeObjectURL).toHaveBeenCalledTimes(1);
  });
});
