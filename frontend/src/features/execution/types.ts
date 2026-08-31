/** F-16 execution-view records. Shapes match app.js executed-code / lineage / inspector. */

export type ExecFrame = {
  frame_id?: string;
  parent_id?: string | null;
  name?: string;
  depth?: number;
  counts?: { cells?: number; error?: number };
  [key: string]: unknown;
};

export type ExecSourcesState = {
  open: boolean;
  data: { frames?: ExecFrame[] } | null;
  selected: string | null;
  cells: Record<string, unknown[]>;
  loading: boolean;
  error: string;
  request: number;
  cellRequest?: number;
};

export type LineageInteraction = {
  kind?: string;
  source?: string;
  language?: string;
  environment?: string;
  cell_index?: number | string | null;
  exit_status?: string;
  status?: string;
  kernel_id?: string;
  files_written?: unknown;
  files_read?: unknown;
  at?: unknown;
  [key: string]: unknown;
};

export type LineageCapture = {
  capture_kind?: string;
  producing_cell_id?: string;
  cell_index?: number | string | null;
  frame_kind?: string;
  frame_id?: string;
  kernel_id?: string;
  inputs?: unknown;
  [key: string]: unknown;
};

export type LineageProducer = {
  kind?: string;
  producing_cell_id?: string;
  frame_id?: string;
  frame_kind?: string;
  [key: string]: unknown;
};

export type LineagePayload = {
  interactions?: LineageInteraction[];
  dependency_mappings?: { inputs?: unknown };
  capture_observations?: LineageCapture[];
  producer?: LineageProducer | null;
  [key: string]: unknown;
};

export type LineageReviewModel = {
  cell: LineageInteraction | null;
  mappedInputs: string[];
  cellInputs: string[];
  captures: LineageCapture[];
  producer: LineageProducer | null;
  saveAt: unknown;
  empty: boolean;
};

export type EnvSnapshot = {
  source?: string;
  generation_confidence?: string;
  provenance?: string;
  python_version?: string | null;
  implementation?: string;
  kind?: string;
  environment_name?: string;
  package_count?: number;
  packages?: Array<{ name?: string; version?: string }>;
  packages_unavailable?: string;
  interpreter?: string;
  platform?: string;
  remote?: unknown[];
  [key: string]: unknown;
};

export type EnvHonesty = {
  captured: boolean;
  verified: boolean;
  noteKey: "prov.env.liveFallback" | "prov.env.recorded" | "prov.env.recordedUnverified";
  noteClass: "ok" | "warn";
  showProvenanceWhy: boolean;
};

export type EnvPythonChip = { label: string; value: string } | null;
