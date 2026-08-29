import { describe, expect, it } from "vitest";
import { sheetCap, sheetShape, SHEET_MAX_COLUMNS, SHEET_MAX_ROWS } from "./sheet";

describe("renderSheet shape (app.js:8771-8802)", () => {
  it("counts the union of keys, not just rows[0]", () => {
    expect(sheetShape([{ a: 1 }, { a: 2, late: 3 }])).toEqual({
      rows: 2,
      columns: 2,
      keys: ["a", "late"],
    });
  });

  it("caps at 5000 rows and 100 columns and reports what was hidden", () => {
    const tall: Record<string, unknown>[] = [];
    for (let r = 0; r < 5001; r++) tall.push({ a: r, b: r, c: r });
    const tallCap = sheetCap(tall);
    expect(tallCap.safeRows).toHaveLength(SHEET_MAX_ROWS);
    expect(tallCap.hiddenRows).toBe(1);
    expect(tallCap.shape.rows).toBe(5001);

    const wide: Record<string, unknown> = {};
    for (let c = 0; c < 101; c++) wide["c" + c] = c;
    const wideCap = sheetCap([wide, { ...wide }]);
    expect(wideCap.columns).toHaveLength(SHEET_MAX_COLUMNS);
    expect(wideCap.hiddenColumns).toBe(1);
    expect(wideCap.shape.columns).toBe(101);
  });
});
