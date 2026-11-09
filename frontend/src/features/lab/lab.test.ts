import { describe, expect, it } from "vitest";
import { latestRead } from "./LabPanel";
import { categoryLabel, plainIntro } from "./MarketPanel";

describe("lab panel helpers", () => {
  it("keeps only the most recent read of the dose-response curves", () => {
    const curve = (read_id: string, row: string) => ({
      read_id,
      row,
      compound_id: row,
      plate_id: "PL-1",
      concentrations_uM: [1],
      activity_pct: [50],
    });
    expect(latestRead([curve("RD-1", "A"), curve("RD-2", "A"), curve("RD-2", "B")]).map((c) => c.row)).toEqual(["A", "B"]);
    expect(latestRead([])).toEqual([]);
  });

  it("shows market introductions without code backticks", () => {
    expect(plainIntro("It reads `x` and `y`.")).toBe("It reads x and y.");
  });

  it("passes unknown categories through", () => {
    expect(categoryLabel("Something new")).toBe("Something new");
  });
});
