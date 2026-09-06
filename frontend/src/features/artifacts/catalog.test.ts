import { afterEach, describe, expect, it } from "vitest";
import { compatibilityRendererDescriptor, scientificRenderers } from "./catalog";
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
