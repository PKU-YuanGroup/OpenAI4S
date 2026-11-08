/** Shapes of the /api/v1/mhs-lab/* routes (openai4s/server/mhs_lab_routes.py). */

export interface LabEndpoint {
  id: string;
  title: string;
  device_id?: string;
  /** Agent surface: "mhs" = native mhs_* tools, "mcp" = an MCP connector row. */
  group?: "mhs" | "mcp";
  /** Built-in demo device (cannot be removed) or a market device the operator installed. */
  builtin?: boolean;
  package?: string;
  category?: string | null;
  risk?: string | null;
  connector?: string;
  access: string;
  surface?: string;
  wire?: string;
  provenance?: string;
  url?: string;
  running?: boolean | null;
  latch?: { latched: boolean; reason?: string; since?: string };
  state?: Record<string, unknown> | null;
}

export interface LimsVial {
  vial_id: string;
  compound_id: string;
  concentration_mM: number;
  purity: number;
  location: string;
}

export interface LabStatus {
  available: boolean;
  reason?: string;
  endpoints?: LabEndpoint[];
  lims?: LimsVial[];
  openmhs?: { pinned_ref?: string; checkout?: string | null; searched?: string[] };
}

export interface MarketEntry {
  id: string;
  name: string;
  category: string;
  topic?: string | null;
  mode: string;
  risk: string;
  track: string;
  intro: string;
  tags: string[];
  stars?: number | null;
  measures?: string[];
  adjusts?: string[];
  upstream: { repo_url: string; commit?: string; license?: string | null };
  install: { openai4s: string; mhs: string; mcp: string };
  installed: { id: string; via: "mcp" | "mhs" } | null;
}

export interface MarketPage {
  total: number;
  offset: number;
  limit: number;
  count: number;
  packages: MarketEntry[];
  categories: Record<string, number>;
  website?: string;
}

export interface ActivityItem {
  endpoint: string;
  access: string;
  seq: number;
  ts?: string;
  capability: string;
  status?: string;
  code?: string | null;
  message?: string | null;
  summary?: string;
  digest?: string;
}

export interface DoseCurve {
  read_id: string;
  plate_id?: string | null;
  row: string;
  compound_id: string;
  concentrations_uM: number[];
  activity_pct: number[];
  ic50_estimate_uM?: number | null;
}

export interface ReactorRun {
  experiment_id: string;
  reagent_b: string;
  temperature_C: number;
  residence_time_min: number;
  equiv_b: number;
  yield_pct: number;
}

export interface ArmPoint {
  x: number;
  y: number;
  z: number;
  status: string;
  code?: string | null;
}

export interface LabResults {
  dose_response: DoseCurve[];
  reactor_runs: ReactorRun[];
  arm_path: ArmPoint[];
  vials: unknown[];
}

export interface ChainReport {
  valid: boolean | null;
  checked: number;
  head_digest: string | null;
}

export interface LabVerify {
  chains: Record<string, ChainReport>;
  host_ledger: { valid?: boolean; checked?: number; records?: number; head_digest?: string };
}
