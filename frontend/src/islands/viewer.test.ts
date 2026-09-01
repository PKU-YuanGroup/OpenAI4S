import { describe, expect, it } from "vitest";
import { isTextEditable } from "./viewer";

describe("isTextEditable (app.js:9458-9461)", () => {
  it("rejects images, structures, and PDFs", () => {
    expect(isTextEditable({ id: "1", filename: "plot.png" })).toBe(false);
    expect(isTextEditable({ id: "1", filename: "struct.pdb" })).toBe(false);
    expect(isTextEditable({ id: "1", filename: "paper.pdf" })).toBe(false);
    expect(isTextEditable({ id: "1", filename: "x.mol" })).toBe(false);
    expect(isTextEditable({ id: "1", content_type: "image/png", filename: "x" })).toBe(false);
  });

  it("accepts text-like extensions and content types", () => {
    expect(isTextEditable({ id: "1", filename: "notes.md" })).toBe(true);
    expect(isTextEditable({ id: "1", filename: "table.csv" })).toBe(true);
    expect(isTextEditable({ id: "1", filename: "run.py" })).toBe(true);
    expect(isTextEditable({ id: "1", filename: "blob", content_type: "text/plain" })).toBe(true);
    expect(isTextEditable({ id: "1", filename: "blob", content_type: "application/json" })).toBe(true);
  });
});
