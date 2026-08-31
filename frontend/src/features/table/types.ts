export type { ArtifactRow } from "../artifacts/types";

/** B-07 `GET .../table/profile` column histogram. Bins are ≤ 50. */
export type NumericHistogramBin = {
  start: number;
  end: number;
  count: number;
};

export type CategoryHistogramBin = {
  value: string;
  count: number;
};

export type HistogramBin = NumericHistogramBin | CategoryHistogramBin;

export type TableProfileColumn = {
  name: string;
  type: string;
  missing: number;
  unique: number;
  min: number | null;
  max: number | null;
  mean: number | null;
  histogram: HistogramBin[];
};

/** B-07 profile JSON. `approximate` is pass-through — never coerced to exact. */
export type TableProfile = {
  artifact_id?: string;
  version_id?: string;
  checksum?: string;
  filtered_rows?: number;
  approximate?: boolean | string;
  schema_version?: number;
  columns?: TableProfileColumn[];
  filters?: Record<string, string>;
};

export type TablePagePayload = {
  artifact_id?: string;
  version_id?: string;
  filename?: string;
  columns?: string[];
  column_types?: string[];
  rows?: unknown[][];
  total_rows?: number;
  offset?: number;
  limit?: number;
  sorted_by?: string;
  descending?: boolean;
  filters?: Record<string, string>;
};

export type TableWorkbenchState = {
  sort: string;
  dir: string;
  filters: Record<string, string>;
  offset: number;
  limit: number;
};

export type TableRendererOptions = {
  workbenchOn?: boolean;
  capabilities?: readonly string[] | null;
};

export type TableCatalogPosture = {
  workbenchOn: boolean;
  view: boolean;
  sort: boolean;
  filter: boolean;
  profile: boolean;
  export: boolean;
  parquet: boolean;
  advertised: string[];
};

export type TableViewerPlan = {
  mode: "legacy-sheet" | "workbench";
  schema: boolean;
  distribution: boolean;
  export: boolean;
  parquet: boolean;
};
