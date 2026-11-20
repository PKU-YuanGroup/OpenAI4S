import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { editorKeywords } from "../md/highlight";
import {
  AC_LIMIT,
  artifactToAcItem,
  harvestBufferIdentifiers,
  mergeArtifactCandidates,
  rankComposerItems,
  rankEditorItems,
  rankSkillItems,
  skillToAcItem,
} from "./rank";

const here = dirname(fileURLToPath(import.meta.url));

describe("skill picker ranking", () => {
  const CATALOG = [
    {
      name: "is-qualitative-analysis",
      description: "信息系统访谈、案例、质性编码与编码者一致性。Information Systems interview analysis.",
    },
    {
      name: "literature-review",
      description:
        "Find, verify, and synthesize scientific literature — from the seminal paper through full multi-source reviews.",
    },
    { name: "druggability-screen", description: "Target druggability screening." },
    { name: "figure-style", description: "Publication-grade figure rules." },
    {
      name: "bio-metabarcoding-amplicon",
      description: "Amplicon metabarcoding pipelines.",
      collection: "bioskills",
    },
    {
      name: "bio-clinical-bayesian-trials",
      description: "Bayesian trials with meta-analytic-predictive priors.",
      collection: "bioskills",
    },
    { name: "metadata-audit", description: "Audit dataset metadata." },
  ].map(skillToAcItem);

  const names = (query: string): string[] =>
    rankSkillItems(CATALOG, query).map((item) => item.insert);

  it("puts a name match ahead of a substring inside another word", () => {
    // `lit` is inside qua-lit-ative and druggabi-lit-y; the skill named for it comes first.
    expect(names("lit")[0]).toBe("literature-review");
    expect(names("lit")).toEqual(
      expect.arrayContaining(["is-qualitative-analysis", "druggability-screen"]),
    );
  });

  it("finds a skill by its description, Chinese keywords and spelling variants", () => {
    expect(names("synthesize")).toEqual(["literature-review"]);
    expect(names("文献")[0]).toBe("literature-review");
    expect(names("综述")[0]).toBe("literature-review");
    expect(names("文献综述")[0]).toBe("literature-review");
    expect(names("pubmed")).toEqual(["literature-review"]);
    expect(names("literature_review")[0]).toBe("literature-review");
    expect(names("Literature-Review")[0]).toBe("literature-review");
    // Chinese in a description matches directly.
    expect(names("访谈")[0]).toBe("is-qualitative-analysis");
  });

  it("reaches description-only matches, curated skills first on a tie", () => {
    const meta = names("meta");
    expect(meta).toContain("bio-clinical-bayesian-trials");
    expect(meta.indexOf("metadata-audit")).toBeLessThan(meta.indexOf("bio-metabarcoding-amplicon"));
    expect(names("荟萃")).toEqual(["bio-clinical-bayesian-trials"]);
  });

  it("reaches a collection's members by the collection's own name, after any direct match", () => {
    expect(names("bioskills")).toEqual(["bio-metabarcoding-amplicon", "bio-clinical-bayesian-trials"]);
    expect(names("Bio Skills")).toEqual(["bio-metabarcoding-amplicon", "bio-clinical-bayesian-trials"]);
    expect(names("bio_skill")).toEqual(["bio-metabarcoding-amplicon", "bio-clinical-bayesian-trials"]);
    // A name match still comes first; two letters name no collection.
    expect(names("bio")[0]).toBe("bio-metabarcoding-amplicon");
    expect(names("bi")).not.toContain("literature-review");
  });

  it("returns every skill for an empty query and nothing for a miss", () => {
    expect(names("")).toHaveLength(CATALOG.length);
    expect(names("")[0]).toBe("is-qualitative-analysis");
    expect(names("zzzz-no-such-skill")).toEqual([]);
  });
});

describe("composer candidate ranking", () => {
  it("filters by substring on label or insert and caps at 8", () => {
    const items = [
      { label: "results.csv", insert: "results.csv#v-aaa", sub: "" },
      { label: "plot.png", insert: "plot.png#v-bbb", sub: "" },
      { label: "notes.md", insert: "notes.md", sub: "" },
    ];
    expect(rankComposerItems(items, "plot").map((i) => i.label)).toEqual(["plot.png"]);
    expect(rankComposerItems(items, "v-aaa").map((i) => i.insert)).toEqual([
      "results.csv#v-aaa",
    ]);
    const many = Array.from({ length: 20 }, (_, i) => ({
      label: "f" + i,
      insert: "f" + i,
      sub: "",
    }));
    expect(rankComposerItems(many, "").length).toBe(AC_LIMIT);
  });

  it("an empty query keeps insertion order (project list first)", () => {
    const items = [
      { label: "a", insert: "a", sub: "" },
      { label: "b", insert: "b", sub: "" },
    ];
    expect(rankComposerItems(items, "").map((i) => i.label)).toEqual(["a", "b"]);
  });

  it("dedupes artifacts by identity, not filename", () => {
    const merged = mergeArtifactCandidates(
      [
        { filename: "results.csv", artifact_id: "a1", version_id: "v-1" },
        { filename: "results.csv", artifact_id: "a2", version_id: "v-2" },
      ],
      [{ filename: "results.csv", artifact_id: "a1", version_id: "v-1" }],
    );
    expect(merged.map((a) => a.artifact_id)).toEqual(["a1", "a2"]);
  });

  it("keeps the project page's version when the session lists the same artifact", () => {
    const merged = mergeArtifactCandidates(
      [{ filename: "plot.png", artifact_id: "a1", version_id: "v-project" }],
      [{ filename: "plot.png", artifact_id: "a1", version_id: "v-session" }],
    );
    expect(merged).toHaveLength(1);
    expect(merged[0]?.version_id).toBe("v-project");
    const item = artifactToAcItem(merged[0]!, null, "");
    expect(item.insert).toBe("plot.png");
    expect(item.mention?.version_id).toBe("v-project");
  });

  it("pins the version beside the name and marks a file from another session", () => {
    const item = artifactToAcItem(
      {
        filename: "plot.png",
        artifact_id: "a-plot",
        version_id: "v-abcdef",
        root_frame_id: "other",
        content_type: "image/png",
      },
      "here",
      "from another session — copied in on send",
    );
    // ART-16: the text gets the name; the hash never enters what the user reads.
    expect(item.insert).toBe("plot.png");
    expect(item.mention).toEqual({
      filename: "plot.png",
      version_id: "v-abcdef",
      artifact_id: "a-plot",
      root_frame_id: "other",
    });
    expect(item.sub).toContain("from another session");
    expect(item.sub).toContain("abcdef".slice(0, 6));
  });

  it("a row without a version inserts the bare name and pins nothing", () => {
    const item = artifactToAcItem({ filename: "notes.md" }, "here", "");
    expect(item.insert).toBe("notes.md");
    expect(item.mention).toBeUndefined();
  });
});

describe("editor candidate ranking (keywords first)", () => {
  it("puts F-08 keywords ahead of buffer identifiers", () => {
    const items = rankEditorItems(
      editorKeywords("py"),
      ["default_path", "delta"],
      "de",
      "Keywords",
    );
    expect(items.length).toBeGreaterThan(0);
    expect(items[0]?.label).toBe("def");
    expect(items[0]?.sub).toBe("Keywords");
    const labels = items.map((i) => i.label);
    expect(labels.indexOf("def")).toBeLessThan(labels.indexOf("default_path"));
  });

  it("skips the token already typed and matches a prefix, case-insensitive", () => {
    const items = rankEditorItems(
      ["def", "class", "return"],
      ["define_me"],
      "def",
      "Keywords",
    );
    expect(items.map((i) => i.label)).toEqual(["define_me"]);
    const mixed = rankEditorItems(["True", "try"], [], "t", "Keywords");
    expect(mixed.map((i) => i.label)).toEqual(["True", "try"]);
  });

  it("caps at 8 and does not harvest a huge buffer", () => {
    const kw = Array.from({ length: 20 }, (_, i) => "k" + i);
    expect(rankEditorItems(kw, ["kbuffer"], "k", "Keywords").length).toBe(8);
    expect(harvestBufferIdentifiers("ab ".repeat(200000)).length).toBe(0);
    expect(harvestBufferIdentifiers("foo bar foo baz")).toEqual(["foo", "bar", "baz"]);
  });

  it("uses the unified highlight table (py gains self; sh gains alias; ts gains interface)", () => {
    expect(editorKeywords("py")).toContain("self");
    expect(editorKeywords("sh")).toContain("alias");
    expect(editorKeywords("ts")).toContain("interface");
    expect(editorKeywords("bash")).toBe(editorKeywords("sh"));
  });
});

describe("no private EDKW table in this lane", () => {
  it("editor.ts imports editorKeywords from F-08 highlight.ts", () => {
    const src = readFileSync(join(here, "editor.ts"), "utf8");
    expect(src).toContain('from "../md/highlight"');
    expect(src).toContain("editorKeywords");
    expect(src).not.toMatch(/\bconst EDKW\b/);
    expect(src).not.toMatch(/\bEDKW_PY\b/);
  });
});
