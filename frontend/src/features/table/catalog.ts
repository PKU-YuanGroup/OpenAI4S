import type { TableCatalogPosture, TableViewerPlan } from "./types";

/** Capability names the table renderer may advertise. Order is stable for tests. */
export const TABLE_CAPABILITY_NAMES = [
  "view",
  "sort",
  "filter",
  "profile",
  "export",
  "parquet",
] as const;

export type TableCapabilityName = (typeof TABLE_CAPABILITY_NAMES)[number];

/**
 * Honest table-renderer posture.
 *
 * The server catalog (`GET /renderers`) already projects flag + optional
 * Parquet engine onto `table` capabilities. The UI must not invent
 * `profile` / `export` / `parquet`. Flag-off is a local kill switch: even a
 * stale catalog cannot show Schema/Distribution/Export or claim Parquet.
 */
export function tableCatalogPosture(
  renderer: { capabilities?: readonly string[] | null } | null | undefined,
  opts: { workbenchOn: boolean },
): TableCatalogPosture {
  const workbenchOn = !!opts.workbenchOn;
  if (!workbenchOn) {
    return {
      workbenchOn: false,
      view: true,
      sort: false,
      filter: false,
      profile: false,
      export: false,
      parquet: false,
      advertised: ["view"],
    };
  }
  const raw = Array.isArray(renderer?.capabilities) ? renderer.capabilities : [];
  const caps = new Set<string>();
  for (const name of raw) {
    if (typeof name === "string" && name) caps.add(name);
  }
  const advertised = TABLE_CAPABILITY_NAMES.filter((name) => caps.has(name));
  if (!advertised.includes("view")) advertised.unshift("view");
  return {
    workbenchOn: true,
    view: true,
    sort: caps.has("sort"),
    filter: caps.has("filter"),
    profile: caps.has("profile"),
    export: caps.has("export"),
    parquet: caps.has("parquet"),
    advertised,
  };
}

export function planTableViewer(posture: TableCatalogPosture): TableViewerPlan {
  if (!posture.workbenchOn) {
    return {
      mode: "legacy-sheet",
      schema: false,
      distribution: false,
      export: false,
      parquet: false,
    };
  }
  return {
    mode: "workbench",
    schema: posture.profile,
    distribution: posture.profile,
    export: posture.export,
    parquet: posture.parquet,
  };
}

/** True only when the catalog itself advertised `parquet`. Never inferred from filename. */
export function catalogClaimsParquet(
  renderer: { capabilities?: readonly string[] | null } | null | undefined,
): boolean {
  const caps = renderer?.capabilities;
  return Array.isArray(caps) && caps.includes("parquet");
}
