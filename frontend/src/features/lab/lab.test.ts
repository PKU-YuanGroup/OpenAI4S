import { afterEach, describe, expect, it, vi } from "vitest";
import { labTabHidden, latestRead, setLabTabHidden } from "./LabPanel";
import { categoryLabel, plainIntro } from "./MarketPanel";

// NAV-6: the Lab tab sits in every session; people who never run instrument
// experiments can hide it, and the choice survives a reload.
describe("hiding the Lab tab", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("is remembered per browser and shown on the page", () => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, value),
      removeItem: (key: string) => store.delete(key),
    });
    const classes = new Set<string>();
    vi.stubGlobal("document", {
      body: { classList: { toggle: (name: string, on: boolean) => (on ? classes.add(name) : classes.delete(name)) } },
      querySelector: () => null,
    });
    expect(labTabHidden()).toBe(false);
    setLabTabHidden(true);
    expect(labTabHidden()).toBe(true);
    expect(classes.has("lab-tab-hidden")).toBe(true);
    setLabTabHidden(false);
    expect(labTabHidden()).toBe(false);
    expect(classes.has("lab-tab-hidden")).toBe(false);
  });
});

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
