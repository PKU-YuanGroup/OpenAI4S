import { describe, expect, it } from "vitest";
import { buildSkillBody, procedureSteps, reusableFunctions, stepSummary } from "./skill-draft";

describe("Save as Skill from the executed cells (M-84)", () => {
  it("describes a step by its first comment, else by what it wrote, else by its language", () => {
    expect(stepSummary({ source: "# Fit the growth curve\nfit()" })).toBe("Fit the growth curve");
    expect(stepSummary({ source: "\n  # padded comment \nx = 1" })).toBe("padded comment");
    expect(stepSummary({ source: "#!/usr/bin/env python\nx = 1" })).toBe("Run a Python cell");
    expect(stepSummary({ source: "x = 1", files_written: ["a.csv", "b.png"] })).toBe("Writes a.csv, b.png");
    expect(stepSummary({ source: "x <- 1", language: "r" })).toBe("Run an R cell");
  });

  it("keeps only the latest successful attempt of each cell, in order", () => {
    const steps = procedureSteps([
      { producing_cell_id: "a", status: "error", is_latest_attempt: false, source: "bad(" },
      { producing_cell_id: "b", status: "ok", is_latest_attempt: true, source: "good()" },
      { producing_cell_id: "c", status: "ok", is_latest_attempt: true, source: "   " },
      { producing_cell_id: "d", status: "error", source: "raise" },
      { producing_cell_id: "e", status: "ok", language: "r", source: "summary(fit)" },
    ]);
    expect(steps.map((step) => [step.id, step.language])).toEqual([["b", "python"], ["e", "r"]]);
  });

  it("collects top-level Python functions, decorators included, the last definition winning", () => {
    const functions = reusableFunctions([
      { id: "1", language: "python", summary: "", code: "def f(x):\n    return x\n\nf(1)" },
      { id: "2", language: "r", summary: "", code: "def <- function() 1" },
      {
        id: "3",
        language: "python",
        summary: "",
        code: "@cache\ndef g(y):\n    if y:\n\n        return 2\n    return 3\nprint(g(1))\ndef f(x):\n    return x + 1",
      },
    ]);
    expect(functions).toEqual(["def f(x):\n    return x + 1", "@cache\ndef g(y):\n    if y:\n\n        return 2\n    return 3"]);
  });

  it("builds the body from the kept steps and falls back to the generic Procedure with none", () => {
    const parts = {
      purpose: "Titrate the buffer",
      outcome: "pH 7.2",
      steps: [
        { id: "1", language: "python", summary: "Load", code: "df = load()" },
        { id: "2", language: "python", summary: "Plot", code: "plot(df)" },
      ],
    };
    const both = buildSkillBody(parts, new Set(["1", "2"]));
    expect(both).toContain("# Purpose\n\nTitrate the buffer");
    expect(both).toContain("1. Load\n\n```python\ndf = load()\n```");
    expect(both).toContain("2. Plot\n\n```python\nplot(df)\n```");
    expect(both).toContain("# Example outcome\n\npH 7.2");
    expect(both).not.toContain("kernel.py");
    const second = buildSkillBody(parts, new Set(["2"]));
    expect(second).toContain("1. Plot");
    expect(second).not.toContain("df = load()");
    const none = buildSkillBody(parts, new Set());
    expect(none).toContain("1. Reproduce the evidence-gathering and analysis workflow.");
  });

  it("fences code that itself contains a fence without breaking out of it", () => {
    const body = buildSkillBody(
      { purpose: "", outcome: "", steps: [{ id: "1", language: "python", summary: "Doc", code: 'print("```")' }] },
      new Set(["1"]),
    );
    expect(body).toContain('````python\nprint("```")\n````');
  });
});
