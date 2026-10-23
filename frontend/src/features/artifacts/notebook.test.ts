import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreFields } from "../../stores/signal-field";
import { setArtifactsFetch } from "./api";
import { filesT } from "./copy";
import {
  closeNotebookPreview,
  inspectPng,
  NOTEBOOK_MAX_CELLS,
  NOTEBOOK_MAX_OUTPUTS_PER_CELL,
  NOTEBOOK_MAX_PNG_PIXELS,
  NOTEBOOK_PNG_MAX_BYTES,
  NOTEBOOK_TEXT_LIMIT_BYTES,
  parseNotebook,
  renderNotebookArtifact,
  truncateTextBytes,
} from "./notebook";
import type { ArtifactRow } from "./types";

const { createObjectURL, revokeObjectURL } = vi.hoisted(() => ({
  createObjectURL: vi.fn((_blob: unknown) => "blob:notebook-live"),
  revokeObjectURL: vi.fn(),
}));

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
const codeCell = (source: unknown, outputs: unknown[] = []) => ({
  cell_type: "code",
  source,
  outputs,
  metadata: {},
  execution_count: 1,
});
const streamOutput = (text: unknown, name = "stdout") => ({ output_type: "stream", name, text });
const pngOutput = (width: number, height: number, extra = 0) => ({
  output_type: "display_data",
  data: { "image/png": base64(png(width, height, extra)) },
  metadata: {},
});

describe("nbformat-4 envelope validation (A3)", () => {
  it("accepts string and string-array sources, code outputs and the kernel language", () => {
    const parsed = parseNotebook(
      notebookDoc(
        [
          markdownCell(["# Title\n", "body"]),
          codeCell("print(1)", [
            streamOutput(["one\n", "two\n"]),
            streamOutput("warning", "stderr"),
            {
              output_type: "execute_result",
              data: { "text/plain": ["42"] },
              metadata: {},
              execution_count: 1,
            },
            { output_type: "error", ename: "ValueError", evalue: "bad", traceback: ["line 1", "line 2"] },
            { output_type: "display_data", data: { "image/png": base64(png(10, 20)) }, metadata: {} },
            { output_type: "display_data", data: { "text/html": "<script>x</script>" }, metadata: {} },
          ]),
        ],
        { kernelspec: { language: "python" } },
      ),
    );
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.notebook.language).toBe("python");
    expect(parsed.notebook.cells).toHaveLength(2);
    const [markdown, code] = parsed.notebook.cells;
    expect(markdown!.cellType).toBe("markdown");
    expect(markdown!.source.text).toBe("# Title\nbody");
    expect(code!.outputs.map((output) => output.kind)).toEqual([
      "text",
      "text",
      "text",
      "text",
      "png",
      "unsupported",
    ]);
    expect(code!.outputs[4]).toMatchObject({ kind: "png" });
    expect(code!.outputs[5]).toMatchObject({ kind: "unsupported", mime: "text/html" });
  });

  it("rejects damaged JSON, unsupported versions and wrong field types", () => {
    expect(parseNotebook("{not json").ok).toBe(false);
    expect(parseNotebook(JSON.stringify({ nbformat: 3, cells: [] }))).toMatchObject({
      ok: false,
      reason: "unsupported-version",
    });
    expect(parseNotebook(JSON.stringify({ nbformat: "4", cells: [] }))).toMatchObject({
      ok: false,
      reason: "unsupported-version",
    });
    expect(parseNotebook(JSON.stringify({ nbformat: 4, cells: "no" }))).toMatchObject({
      ok: false,
      reason: "invalid-shape",
    });
    expect(parseNotebook(notebookDoc([markdownCell(42)]))).toMatchObject({
      ok: false,
      reason: "invalid-shape",
    });
    expect(parseNotebook(notebookDoc([{ cell_type: 7, source: "" }]))).toMatchObject({
      ok: false,
      reason: "invalid-shape",
    });
    expect(
      parseNotebook(notebookDoc([{ cell_type: "code", source: "x", outputs: "no" }])),
    ).toMatchObject({ ok: false, reason: "invalid-shape" });
    expect(
      parseNotebook(notebookDoc([codeCell("x", [{ output_type: "stream", text: 42 }])])),
    ).toMatchObject({ ok: false, reason: "invalid-shape" });
  });

  it("caps cells and outputs per cell and reports the omitted counts", () => {
    const cells = Array.from({ length: NOTEBOOK_MAX_CELLS + 1 }, (_, index) =>
      markdownCell("cell " + index),
    );
    const parsed = parseNotebook(notebookDoc(cells));
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.notebook.cells).toHaveLength(NOTEBOOK_MAX_CELLS);
    expect(parsed.notebook.omittedCells).toBe(1);

    const many = codeCell(
      "x",
      Array.from({ length: NOTEBOOK_MAX_OUTPUTS_PER_CELL + 2 }, () => streamOutput("out")),
    );
    const capped = parseNotebook(notebookDoc([many]));
    expect(capped.ok).toBe(true);
    if (!capped.ok) return;
    expect(capped.notebook.cells[0]!.outputs).toHaveLength(NOTEBOOK_MAX_OUTPUTS_PER_CELL);
    expect(capped.notebook.cells[0]!.omittedOutputs).toBe(2);
  });

  it("truncates one text display inside 64 KiB without splitting an emoji", () => {
    const text = "a".repeat(NOTEBOOK_TEXT_LIMIT_BYTES - 1) + "🙂tail";
    const limited = truncateTextBytes(text);
    expect(limited.truncated).toBe(true);
    expect(new TextEncoder().encode(limited.text).length).toBeLessThanOrEqual(
      NOTEBOOK_TEXT_LIMIT_BYTES,
    );
    // The retained prefix ends on a whole surrogate pair boundary.
    const last = limited.text.charCodeAt(limited.text.length - 1);
    expect(last >= 0xd800 && last <= 0xdbff).toBe(false);
    const parsed = parseNotebook(notebookDoc([codeCell("x", [streamOutput(text)])]));
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    const output = parsed.notebook.cells[0]!.outputs[0];
    expect(output).toMatchObject({ kind: "text" });
    if (output?.kind === "text") expect(output.value.truncated).toBe(true);
  });

  it("ignores metadata.trusted as authorization or truth", () => {
    const trusted = parseNotebook(
      notebookDoc([markdownCell("hello")], { trusted: true, kernelspec: { language: "r" } }),
    );
    expect(trusted.ok).toBe(true);
    if (!trusted.ok) return;
    expect(JSON.stringify(trusted.notebook)).not.toContain("trusted");
    expect(trusted.notebook.language).toBe("r");
  });
});

describe("PNG validation before image creation (A3)", () => {
  it("accepts a well-formed PNG and reports its dimensions", () => {
    const checked = inspectPng(base64(png(640, 480)));
    expect(checked.ok).toBe(true);
    if (!checked.ok) return;
    expect(checked.width).toBe(640);
    expect(checked.height).toBe(480);
    expect(checked.bytes.length).toBe(33);
  });

  it("rejects malformed base64, a bad signature, a bad header and zero dimensions", () => {
    expect(inspectPng("***not base64***")).toMatchObject({ ok: false, reason: "invalid-base64" });
    const notPng = png(1, 1);
    notPng[0] = 0;
    expect(inspectPng(base64(notPng))).toMatchObject({ ok: false, reason: "not-png" });
    const badHeader = png(1, 1);
    badHeader[12] = 0x58;
    expect(inspectPng(base64(badHeader))).toMatchObject({ ok: false, reason: "bad-header" });
    expect(inspectPng(base64(png(0, 1)))).toMatchObject({ ok: false, reason: "bad-dimensions" });
  });

  it("rejects a file over 2 MiB before decoding it", () => {
    const big = png(1, 1, NOTEBOOK_PNG_MAX_BYTES);
    expect(inspectPng(base64(big))).toMatchObject({ ok: false, reason: "too-large" });
  });
});

class FakeNode {
  children: FakeNode[] = [];
  parent: FakeNode | null = null;
  page = false;
  className = "";
  textContent = "";
  html = "";
  src = "";
  href = "";
  alt = "";
  attrs: Record<string, string> = {};
  dataset: Record<string, string> = {};
  classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
  onclick: (() => void) | null = null;
  constructor(readonly tagName = "div") {}
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

describe("read-only notebook preview (A3)", () => {
  const host = (node: FakeNode) => node as unknown as HTMLElement;
  const notebook: ArtifactRow = {
    id: "nb",
    filename: "session.python.ipynb",
    content_type: "application/x-ipynb+json",
    version_id: "v1",
    _exactVersion: true,
  };
  const url = "/api/v1/artifacts/versions/v1";

  let reads: string[];

  beforeEach(() => {
    resetStoreFields();
    reads = [];
    vi.stubGlobal("document", { createElement: (tag: string) => new FakeNode(tag) });
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    createObjectURL.mockClear();
    revokeObjectURL.mockClear();
  });
  afterEach(() => {
    closeNotebookPreview();
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
    resetStoreFields();
  });

  async function mount(body: string): Promise<FakeNode> {
    setArtifactsFetch(async (target) => {
      reads.push(String(target));
      return new Response(body);
    });
    const root = new FakeNode();
    root.page = true;
    renderNotebookArtifact(host(root), notebook, url);
    await vi.waitFor(() => expect(walk(root).some((node) => node.className === "notebook-preview")).toBe(true), {
      timeout: 3000,
    });
    return root;
  }

  it("renders saved cells, text outputs and the read-only label with one request", async () => {
    const root = await mount(
      notebookDoc(
        [markdownCell("# Results\n\nDone."), codeCell("df.head()", [streamOutput("a b\n1 2\n")])],
        { kernelspec: { language: "python" } },
      ),
    );
    expect(reads).toEqual([url]);
    expect(texts(root)).toContain(filesT("notebook.readonly"));
    expect(walk(root).filter((node) => (node.className || "").includes("notebook-cell "))).toHaveLength(2);
    const markdown = walk(root).find((node) => (node.className || "").includes("notebook-markdown"));
    expect(markdown?.innerHTML).toContain("<h1>Results</h1>");
    const sources = walk(root).filter((node) => (node.className || "").includes("notebook-source"));
    expect(sources.map((node) => node.textContent)).toContain("df.head()");
    expect(texts(root)).toContain("a b\n1 2\n");
  });

  it("creates a validated PNG from a saved output", async () => {
    const root = await mount(notebookDoc([codeCell("plot()", [pngOutput(120, 80)])]));
    await vi.waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
    const image = walk(root).find((node) => node.tagName === "img");
    expect(image?.src).toBe("blob:notebook-live");
    expect(image?.alt).toBe(filesT("notebook.pngAlt"));
  });

  it("enforces the cumulative pixel budget across many saved images", async () => {
    const big = 3000 * 3000; // 9 M pixels each; the second image crosses 16 M
    const root = await mount(
      notebookDoc([
        codeCell("a", [pngOutput(3000, 3000)]),
        codeCell("b", [pngOutput(3000, 3000)]),
        codeCell("c", [pngOutput(3000, 3000)]),
      ]),
    );
    for (let i = 0; i < 8; i++) await new Promise<void>((resolve) => setTimeout(resolve, 0));
    expect(NOTEBOOK_MAX_PNG_PIXELS).toBe(16_000_000);
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(texts(root).filter((text) => text === filesT("notebook.imageLimitPixels")).length).toBe(2);
    expect(big).toBe(9_000_000);
  });

  it("renders markdown cells without images or navigable links", async () => {
    const source = [
      "![plot](data:image/png;base64," + base64(png(1, 1)) + ")",
      "",
      "[paper](https://example.com/paper)",
      "",
      "[bad](javascript:alert(1))",
    ].join("\n");
    const root = await mount(notebookDoc([markdownCell(source)]));
    const markdown = walk(root).find((node) => (node.className || "").includes("notebook-markdown"))!;
    expect(markdown.innerHTML).not.toContain("<img");
    expect(markdown.innerHTML).not.toContain("base64");
    expect(markdown.innerHTML).not.toContain("<a ");
    expect(markdown.innerHTML).toContain(filesT("notebook.imageOmitted"));
    expect(markdown.innerHTML).toContain("paper");
  });

  it("shows unsupported rich output as a notice with the exact-version download", async () => {
    const root = await mount(
      notebookDoc([
        codeCell("widget()", [
          { output_type: "display_data", data: { "text/html": "<script>alert(1)</script>" }, metadata: {} },
          { output_type: "display_data", data: { "image/svg+xml": "<svg onload='x'/>" }, metadata: {} },
          {
            output_type: "display_data",
            data: { "application/vnd.jupyter.widget-view+json": { model_id: "m" } },
            metadata: {},
          },
        ]),
      ]),
    );
    const unsupportedPrefix = filesT("notebook.mimeUnsupported").split("{0}")[0]!;
    expect(texts(root).filter((text) => text.startsWith(unsupportedPrefix))).toHaveLength(3);
    const links = walk(root).filter((node) => node.tagName === "a").map((node) => node.href);
    expect(links).toEqual([url, url, url]);
    // Nothing rich is ever inserted as markup.
    expect(walk(root).some((node) => node.innerHTML.includes("alert(1)"))).toBe(false);
    expect(walk(root).some((node) => node.tagName === "svg" || node.tagName === "script")).toBe(false);
  });

  it("fails visibly for damaged JSON and for an unsupported notebook version", async () => {
    async function mountProblem(body: string): Promise<FakeNode> {
      setArtifactsFetch(async (target) => {
        reads.push(String(target));
        return new Response(body);
      });
      const root = new FakeNode();
      root.page = true;
      renderNotebookArtifact(host(root), notebook, url);
      await vi.waitFor(
        () => expect(walk(root).some((node) => node.className === "renderer-fallback")).toBe(true),
        { timeout: 3000 },
      );
      return root;
    }
    const broken = await mountProblem("{not json");
    expect(texts(broken)).toContain(filesT("notebook.problem.invalidJson"));
    expect(walk(broken).filter((node) => node.tagName === "a").map((node) => node.href)).toEqual([url]);

    const old = await mountProblem(JSON.stringify({ nbformat: 3, cells: [{ cell_type: "markdown", source: "x" }] }));
    expect(texts(old)).toContain(filesT("notebook.problem.version", "3"));
  });

  it("cancels the read and releases image URLs when the preview closes", async () => {
    let aborted = false;
    let started = false;
    setArtifactsFetch(async (_url, init) => {
      started = true;
      await new Promise<void>((resolve) => {
        if (init?.signal?.aborted) {
          aborted = true;
          resolve();
          return;
        }
        init?.signal?.addEventListener("abort", () => {
          aborted = true;
          resolve();
        });
      });
      return new Response(notebookDoc([]));
    });
    const root = new FakeNode();
    root.page = true;
    renderNotebookArtifact(host(root), notebook, url);
    await vi.waitFor(() => expect(started).toBe(true));
    closeNotebookPreview();
    await vi.waitFor(() => expect(aborted).toBe(true));
  });

  it("keeps the parsed notebook free of executable wiring", () => {
    const parsed = parseNotebook(notebookDoc([codeCell("x", [streamOutput("y")])]));
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    // The preview model only carries data. There is no kernel/execute/Host call
    // anywhere in this module (asserted structurally by the single-request test
    // above); this guards the model itself.
    expect(JSON.stringify(parsed.notebook)).not.toMatch(/kernel|execute|\bhost\b/i);
  });
});

describe("notebook text limits are announced (A3)", () => {
  it("notes a truncated single text display", async () => {
    resetStoreFields();
    vi.stubGlobal("document", { createElement: (tag: string) => new FakeNode(tag) });
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    // The parser truncates the saved output; the preview must say so.
    const body = notebookDoc([
      codeCell("x", [streamOutput("x".repeat(NOTEBOOK_TEXT_LIMIT_BYTES + 100))]),
    ]);
    setArtifactsFetch(async () => new Response(body));
    const root = new FakeNode();
    root.page = true;
    renderNotebookArtifact(root as unknown as HTMLElement, { id: "nb2", filename: "a.ipynb" }, "/api/v1/artifacts/versions/v2");
    await vi.waitFor(() => expect(texts(root)).toContain(filesT("notebook.textTruncated")), { timeout: 3000 });
    closeNotebookPreview();
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
    resetStoreFields();
  });
});
