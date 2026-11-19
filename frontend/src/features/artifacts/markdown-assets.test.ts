import { describe, expect, it } from "vitest";
import { findSibling, siblingPath } from "./markdown-assets";
import type { ArtifactRow } from "./types";

describe("siblingPath (ART-2)", () => {
  it("resolves against the report's own directory", () => {
    expect(siblingPath("report.md", "growth.png")).toBe("growth.png");
    expect(siblingPath("report.md", "./figures/growth.png")).toBe("figures/growth.png");
    expect(siblingPath("reports/summary.md", "../figures/a.png")).toBe("figures/a.png");
    expect(siblingPath("reports/summary.md", "b.png")).toBe("reports/b.png");
  });

  it("decodes, drops a query or fragment, and accepts backslashes", () => {
    expect(siblingPath("r.md", "my%20figure.png?raw=1#x")).toBe("my figure.png");
    expect(siblingPath("r.md", "figures\\c.png")).toBe("figures/c.png");
    expect(siblingPath("r.md", "bad%zz.png")).toBe("bad%zz.png");
  });

  it("never climbs above the workspace", () => {
    expect(siblingPath("report.md", "../secret.png")).toBeNull();
    expect(siblingPath("a/report.md", "../../x.png")).toBeNull();
    expect(siblingPath("report.md", "")).toBeNull();
  });
});

describe("findSibling (ART-2)", () => {
  const report: ArtifactRow = { id: "r", filename: "report.md", root_frame_id: "f1" };
  const rows: ArtifactRow[] = [
    report,
    { id: "g", filename: "growth.png", root_frame_id: "f1" },
    { id: "n", filename: "figures/nested.png", root_frame_id: "f1" },
    { id: "d1", filename: "a/dup.png", root_frame_id: "f1" },
    { id: "d2", filename: "b/dup.png", root_frame_id: "f1" },
    { id: "other", filename: "elsewhere.png", root_frame_id: "f2" },
  ];

  it("prefers the exact relative filename", () => {
    expect(findSibling(rows, report, "growth.png")?.id).toBe("g");
    expect(findSibling(rows, report, "figures/nested.png")?.id).toBe("n");
  });

  it("falls back to a basename only one Artifact carries", () => {
    expect(findSibling(rows, report, "figs/growth.png")?.id).toBe("g");
    expect(findSibling(rows, report, "c/dup.png")).toBeNull();
  });

  it("stays inside the report's session and never answers with the report", () => {
    expect(findSibling(rows, report, "elsewhere.png")).toBeNull();
    expect(findSibling(rows, report, "report.md")).toBeNull();
  });
});
