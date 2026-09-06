import { describe, expect, it } from "vitest";
import { catalogClaimsParquet, planTableViewer, tableCatalogPosture } from "./catalog";

const WORKBENCH_CAPS = ["view", "sort", "filter", "profile", "export"];
const WORKBENCH_PARQUET_CAPS = [...WORKBENCH_CAPS, "parquet"];

describe("table catalog honesty (flag + optional Parquet)", () => {
  it("flag=0 advertises only view even if a stale catalog lists profile/parquet", () => {
    const posture = tableCatalogPosture(
      { capabilities: WORKBENCH_PARQUET_CAPS },
      { workbenchOn: false },
    );
    expect(posture).toEqual({
      workbenchOn: false,
      view: true,
      sort: false,
      filter: false,
      profile: false,
      export: false,
      parquet: false,
      advertised: ["view"],
    });
    expect(planTableViewer(posture)).toEqual({
      mode: "legacy-sheet",
      schema: false,
      distribution: false,
      export: false,
      parquet: false,
    });
  });

  it("does not invent profile/export/parquet when the catalog only has view", () => {
    const posture = tableCatalogPosture({ capabilities: ["view"] }, { workbenchOn: true });
    expect(posture.profile).toBe(false);
    expect(posture.export).toBe(false);
    expect(posture.parquet).toBe(false);
    expect(posture.advertised).toEqual(["view"]);
    const plan = planTableViewer(posture);
    expect(plan.mode).toBe("workbench");
    expect(plan.schema).toBe(false);
    expect(plan.distribution).toBe(false);
    expect(plan.export).toBe(false);
    expect(plan.parquet).toBe(false);
  });

  it("passes through workbench profile/export and only claims parquet when the catalog does", () => {
    const noParquet = tableCatalogPosture(
      { capabilities: WORKBENCH_CAPS },
      { workbenchOn: true },
    );
    expect(noParquet.profile).toBe(true);
    expect(noParquet.export).toBe(true);
    expect(noParquet.parquet).toBe(false);
    expect(noParquet.advertised).toEqual(WORKBENCH_CAPS);
    expect(catalogClaimsParquet({ capabilities: WORKBENCH_CAPS })).toBe(false);

    const withParquet = tableCatalogPosture(
      { capabilities: WORKBENCH_PARQUET_CAPS },
      { workbenchOn: true },
    );
    expect(withParquet.parquet).toBe(true);
    expect(withParquet.advertised).toEqual(WORKBENCH_PARQUET_CAPS);
    expect(catalogClaimsParquet({ capabilities: WORKBENCH_PARQUET_CAPS })).toBe(true);
  });

  it("never infers parquet from a .parquet filename", () => {
    expect(catalogClaimsParquet({ capabilities: WORKBENCH_CAPS })).toBe(false);
    expect(
      tableCatalogPosture({ capabilities: WORKBENCH_CAPS }, { workbenchOn: true }).parquet,
    ).toBe(false);
  });
});
