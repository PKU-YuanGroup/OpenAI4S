/**
 * TEAM-12: the Markdown export reads outside the app -- one section per turn,
 * the code and output that ran, figures embedded, no links into the daemon.
 */
import { describe, expect, it } from "vitest";

import { buildSessionMarkdown, embedImages, markdownT, portableLinks } from "./markdown-export";

describe("session Markdown export", () => {
  const messages = [
    { role: "user", content: "Plot 50 random points", created_at: 1000 },
    { role: "assistant", content: "Writing the data first.", created_at: 2000 },
    { role: "assistant", content: "Saving the plot.", created_at: 4000 },
    { role: "assistant", content: "Done: [scatter.png](/api/v1/artifacts/a-8b750dda8915)", created_at: 6000 },
  ];
  const cells = [
    {
      source: "import random\nrandom.seed(42)\nprint(len(pts))",
      stdout: "50\n",
      created_at: 3000,
      kernel_id: "python",
      files_written: ["points.csv"],
    },
    {
      source: "plot()",
      stdout: "",
      created_at: 5000,
      kernel_id: "python — background",
      files_written: ["scatter.png"],
    },
    { source: "host.submit_output('x')", visibility: "system", created_at: 5500 },
  ];

  const md = buildSessionMarkdown({
    title: "Random points",
    messages,
    cells,
    images: [{ filename: "scatter.png", dataUri: "data:image/png;base64,iVBORw0KGgo=" }],
    otherArtifacts: [{ filename: "points.csv", content_type: "text/csv" }],
  });

  it("keeps one heading per turn instead of one per progress line", () => {
    expect(md.match(/^## 🤖/gm)).toHaveLength(1);
    expect(md.match(/^## 🧑/gm)).toHaveLength(1);
  });

  it("includes the code and output in the order they ran, but not the completion RPC", () => {
    const code = md.indexOf("random.seed(42)");
    expect(code).toBeGreaterThan(md.indexOf("Writing the data first."));
    expect(code).toBeLessThan(md.indexOf("Saving the plot."));
    expect(md).toContain("```python\nimport random");
    expect(md).toContain(markdownT("output") + ":\n\n```\n50\n```");
    expect(md).toContain(markdownT("background"));
    expect(md).not.toContain("submit_output");
  });

  it("embeds a figure under the cell that wrote it and leaves no in-app link", () => {
    expect(md).toContain("![scatter.png](data:image/png;base64,iVBORw0KGgo=)");
    expect(md.indexOf("![scatter.png]")).toBeLessThan(md.indexOf("Done: scatter.png"));
    expect(md).not.toContain("/api/v1/artifacts");
    expect(md).toContain("- points.csv (text/csv)");
  });

  it("bounds a long output", () => {
    const long = buildSessionMarkdown({
      title: "t",
      messages: [],
      cells: [{ source: "x", stdout: "a".repeat(5000) }],
      images: [],
      otherArtifacts: [],
    });
    expect(long).toContain(markdownT("more", 2000));
  });

  it("reduces image and file links into the daemon to their text", () => {
    expect(portableLinks("see ![fig](/api/v1/artifacts/v-1) and [data](/api/v1/artifacts/a-2) or [web](https://x.org)")).toBe(
      "see fig and data or [web](https://x.org)",
    );
  });

  it("embeds only images, within the size budget", async () => {
    const reads: string[] = [];
    const images = await embedImages(
      [
        { id: "a1", filename: "fig.png", size_bytes: 3 },
        { id: "a2", filename: "huge.png", size_bytes: 50 * 1024 * 1024 },
        { id: "a3", filename: "table.csv", size_bytes: 3 },
      ],
      async (id) => {
        reads.push(id);
        return new Uint8Array([1, 2, 3]).buffer;
      },
      (n) => `${n} B`,
    );
    expect(reads).toEqual(["a1"]);
    expect(images).toEqual([
      { filename: "fig.png", dataUri: "data:image/png;base64,AQID" },
      { filename: "huge.png", skipped: `${50 * 1024 * 1024} B` },
    ]);
  });
});
