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
} from "./rank";

const here = dirname(fileURLToPath(import.meta.url));

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

  it("pins the version on insert and marks a file from another session", () => {
    const item = artifactToAcItem(
      {
        filename: "plot.png",
        version_id: "v-abcdef",
        root_frame_id: "other",
        content_type: "image/png",
      },
      "here",
      "from another session — copied in on send",
    );
    expect(item.insert).toBe("plot.png#v-abcdef");
    expect(item.sub).toContain("from another session");
    expect(item.sub).toContain("abcdef".slice(0, 6));
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
