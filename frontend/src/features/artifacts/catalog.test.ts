import { afterEach, describe, expect, it, vi } from "vitest";
import { _rendererCatalogPromise, rendererCatalog, rendererDescriptors } from "../../stores/artifacts";
import { resetStoreFields } from "../../stores/signal-field";
import { ApiError, setArtifactsFetch } from "./api";
import { artifactRendererDescriptor, compatibilityRendererDescriptor, loadRendererCatalog, scientificRenderers } from "./catalog";
import { parseMolPoints } from "./thumbs";

describe("scientificRenderers empty-value defense (app.js:8578)", () => {
  afterEach(() => {
    delete (globalThis as { OpenAI4SScientificRenderers?: unknown }).OpenAI4SScientificRenderers;
  });

  it("returns null when the UMD global is missing", () => {
    delete (globalThis as { OpenAI4SScientificRenderers?: unknown }).OpenAI4SScientificRenderers;
    expect(scientificRenderers()).toBeNull();
  });

  it("returns the runtime object when present, including a non-function placeholder", () => {
    const api = { rendererIdFromDescriptor: () => "sequence" };
    (globalThis as { OpenAI4SScientificRenderers?: unknown }).OpenAI4SScientificRenderers = api;
    expect(scientificRenderers()).toBe(api);
  });
});

describe("compatibilityRendererDescriptor (app.js:8593-8609)", () => {
  it("maps scientific extensions onto the ten renderer ids", () => {
    const cases: Array<[string, string]> = [
      ["plot.png", "image"],
      ["paper.pdf", "pdf"],
      ["report.html", "html-preview"],
      ["struct.pdb", "molecule-3d"],
      ["mol.sdf", "chemistry-2d"],
      ["peaks.bed", "genome-track"],
      ["aln.sto", "msa"],
      ["reads.fasta", "sequence"],
      ["notes.md", "markdown"],
      ["paper.tex", "latex"],
      ["table.csv", "table"],
      ["log.txt", "text"],
      ["report.python.ipynb", "notebook"],
      ["blob.bin", "download"],
    ];
    for (const [filename, id] of cases) {
      const desc = compatibilityRendererDescriptor({ id: "a", filename });
      expect(desc.renderer?.renderer_id, filename).toBe(id);
      expect(desc.matched_by).toBe("compatibility");
    }
  });
});

describe("parseMolPoints (app.js:8455-8473)", () => {
  it("prefers CA backbone columns from PDB ATOM lines", () => {
    const pdb = [
      "ATOM      1  N   ALA A   1      11.000  12.000  13.000",
      "ATOM      2  CA  ALA A   1      21.000  22.000  23.000",
      "ATOM      3  C   ALA A   1      31.000  32.000  33.000",
      "ATOM      4  CA  ALA A   2      41.000  42.000  43.000",
      "ATOM      5  CA  ALA A   3      51.000  52.000  53.000",
    ].join("\n");
    const pts = parseMolPoints(pdb);
    expect(pts).toHaveLength(3);
    expect(pts[0]).toEqual({ x: 21, y: 22, z: 23 });
  });
});


describe("renderer catalog failure recovery (R1)", () => {
  afterEach(() => {
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
    resetStoreFields();
  });

  it("does not cache a failed catalog as an empty success and retries on the next call", async () => {
    resetStoreFields();
    let requests = 0;
    setArtifactsFetch(async (url) => {
      requests += 1;
      expect(url).toBe("/api/v1/renderers");
      if (requests === 1) return new Response(JSON.stringify({ error: "unavailable" }), { status: 503 });
      return new Response(JSON.stringify({ renderers: [{ renderer_id: "sequence", label: "Sequence" }] }));
    });
    const failure = await loadRendererCatalog().catch((error: unknown) => error);
    // The original error is rethrown, not swallowed into an empty directory.
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(503);
    // A failure is not a successful empty directory, and no in-flight promise
    // is kept that would answer every retry with the same failure.
    expect(rendererCatalog.value).toBeNull();
    expect(_rendererCatalogPromise.value).toBeNull();
    await expect(loadRendererCatalog()).resolves.toEqual([{ renderer_id: "sequence", label: "Sequence" }]);
    expect(requests).toBe(2);
  });

  it("shares one /renderers request between concurrent callers and caches a success", async () => {
    resetStoreFields();
    let requests = 0;
    setArtifactsFetch(async (url) => {
      requests += 1;
      expect(url).toBe("/api/v1/renderers");
      await new Promise<void>((resolve) => setTimeout(resolve, 1));
      return new Response(
        JSON.stringify({ renderers: [{ renderer_id: "sequence", label: "Sequence" }] }),
      );
    });
    const [first, second] = await Promise.all([loadRendererCatalog(), loadRendererCatalog()]);
    expect(requests).toBe(1);
    expect(first).toEqual([{ renderer_id: "sequence", label: "Sequence" }]);
    expect(second).toEqual(first);
    // A successful catalog stays cached: later callers issue no request.
    await expect(loadRendererCatalog()).resolves.toEqual(first);
    expect(requests).toBe(1);
    expect(_rendererCatalogPromise.value).toBeNull();
  });

  it("re-resolves an artifact descriptor after a catalog failure without a page reload", async () => {
    resetStoreFields();
    let catalogCalls = 0;
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
        return new Response(
          JSON.stringify({
            artifact_id: "A",
            version_id: "v1",
            matched_by: "extension",
            renderer: { renderer_id: "sequence", label: "Sequence" },
          }),
        );
      }
      throw new Error(`unexpected ${url}`);
    });
    (globalThis as { OpenAI4SScientificRenderers?: unknown }).OpenAI4SScientificRenderers = {
      rendererIdFromDescriptor: (descriptor: { renderer?: { renderer_id?: string } }) =>
        descriptor.renderer?.renderer_id || "download",
    };
    const a = { id: "A", filename: "pep.fasta", version_id: "v1" };
    await expect(artifactRendererDescriptor(a)).rejects.toBeTruthy();
    // Descriptors resolved from the failed catalog are not left cached.
    expect(Object.keys(rendererDescriptors.value)).toEqual([]);
    catalogDown = false;
    const descriptor = await artifactRendererDescriptor(a);
    expect(descriptor.renderer?.renderer_id).toBe("sequence");
    expect(descriptor.renderer?.capabilities).toEqual(["view", "copy"]);
    expect(catalogCalls).toBe(2);
  });

  it("keeps the capability whitelist after a failed catalog and an undeclared renderer", async () => {
    resetStoreFields();
    let catalogDown = true;
    setArtifactsFetch(async (url) => {
      if (url === "/api/v1/renderers") {
        if (catalogDown) return new Response(JSON.stringify({ error: "unavailable" }), { status: 503 });
        return new Response(JSON.stringify({ renderers: [] }));
      }
      if (url === "/api/v1/artifacts/B/renderer") {
        return new Response(JSON.stringify({ artifact_id: "B", matched_by: "extension", renderer: { renderer_id: "notebook" } }));
      }
      throw new Error(`unexpected ${url}`);
    });
    (globalThis as { OpenAI4SScientificRenderers?: unknown }).OpenAI4SScientificRenderers = {
      rendererIdFromDescriptor: () => "notebook",
    };
    // First attempt: the catalog read fails, so the descriptor rejects and the
    // viewer falls back to the compatibility descriptor.
    await expect(artifactRendererDescriptor({ id: "B", filename: "report.ipynb" })).rejects.toBeTruthy();
    expect(Object.keys(rendererDescriptors.value)).toEqual([]);
    catalogDown = false;
    const descriptor = await artifactRendererDescriptor({ id: "B", filename: "report.ipynb" });
    // A retry whose catalog does not declare the renderer id may only degrade to
    // the conservative view capability; it must never invent copy/edit rights.
    expect(descriptor.renderer?.capabilities).toEqual(["view"]);
  });
});
