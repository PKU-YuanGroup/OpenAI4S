/** Public Lab projections; internal ledger and evaluation fields stay server-side. */
export type RunStatus = "creating" | "ready" | "busy" | "quarantined" | "ended" | "failed";
export type CommandState =
  | "created" | "awaiting_approval" | "admitted" | "dispatching" | "running"
  | "succeeded" | "failed" | "rejected" | "not_dispatched" | "outcome_unknown"
  | "stop_requested" | "stopped";
export type EndReason =
  | "end_action" | "max_steps" | "env_terminated" | "stopped" | "budget_exhausted"
  | "provider_lost" | "idle_timeout" | "create_failed" | "deleted";
export type ChannelKind = "array" | "scalar" | "category";
export type ChannelSource = "simulated_sensor" | "physical_sensor";
export type ChannelQuality = "ok" | "unavailable" | "unknown";

export interface Quantity {
  value: number;
  unit: string;
}

export interface Capability {
  capability_id: string;
  operation: string;
  scope: "shared" | "simulation_only";
  source: string | null;
  target: string | null;
  parameters: Record<string, { unit: string; allowed: number[] }>;
  side_effect: "moves_material" | "changes_state" | "ends_run";
  resources: string[];
  observes: string[];
  terminal: boolean;
  mapping_version: string;
}

export interface ChannelSpec {
  name: string;
  kind: ChannelKind;
  shape: number[];
  unit: string;
  source: ChannelSource;
  available: boolean;
  description: string;
  reason?: string;
  axes?: Array<{ name: string; labels?: string[] }>;
}

export interface Device {
  device_id: string;
  backend: string;
  mode: "simulation";
  title: string;
  profiles: string[];
  available: boolean | null;
  availability_detail: string | null;
}

export interface Descriptor {
  contract: string;
  device_id: string;
  mode: "simulation";
  backend: string;
  profile: string;
  backend_version: { package: string; version: string; source_sha: string; adapter_version: string };
  resources: Array<{ resource_id: string; kind: string; label: string }>;
  capabilities: Capability[];
  capability_revision: string;
  observation_channels: ChannelSpec[];
  limits: { max_steps: number };
  stop: { supported: boolean; semantics: string };
  time: { unit: "model_time"; wall_clock_equivalent: null };
  reproducibility: {
    status: "unverified" | "verified_for_profile" | "not_reproducible";
    evidence: unknown;
  };
  assumptions: string[];
}

export type SensorArray = Array<number | SensorArray>;
export interface ObservationChannel {
  name: string;
  kind: ChannelKind;
  unit: string;
  shape: number[];
  value: number | string | SensorArray | {
    shape: number[];
    summary: { min: number; max: number; mean: number };
    truncated: true;
  } | null;
  quality: ChannelQuality;
  source: ChannelSource;
}

export interface Observation {
  observation_id: string;
  run_id: string;
  command_id: string | null;
  sequence: number;
  sim_time: number;
  sim_time_unit: "model_time";
  wall_time_ms: number;
  channels: ObservationChannel[];
  artifact_version_id: string | null;
}

export interface Budgets {
  max_steps: number;
  max_commands: number;
  max_wall_ms: number;
  max_consecutive_failures: number;
  idle_timeout_ms: number;
}

export interface Run {
  run_id: string;
  mode: "simulation";
  backend: string;
  device_id: string;
  profile: string;
  adapter_version: string;
  capability_revision: string;
  seed: number | null;
  status: RunStatus;
  revision: number;
  step_count: number;
  command_count: number;
  consecutive_failures: number;
  end_reason: EndReason | null;
  budgets: Budgets;
  created_at: number;
  updated_at: number;
  ended_at: number | null;
  raw: { terminated: boolean | null; truncated: boolean | null };
}

/** REST body: the run identity belongs in the path, never in this object. */
export interface CommandRequest {
  operation: string;
  source?: string | null;
  target?: string | null;
  parameters?: Record<string, Quantity>;
  expected_revision: number;
  idempotency_key: string;
}

export interface Receipt {
  applied: boolean;
  status: "succeeded" | "failed" | "rejected";
  error: { code: string; message: string } | null;
  raw: { terminated: boolean; truncated: boolean };
  end_reason: EndReason | null;
  sim_time: number;
  step_index: number;
}

export interface Command {
  command_id: string;
  run_id: string;
  seq: number;
  idempotency_key: string;
  operation: string;
  capability_id: string | null;
  request: CommandRequest & { run_id: string; capability_id?: string };
  expected_revision: number;
  applied_revision: number | null;
  origin: "agent_tool" | "host_sdk" | "manual_ui" | "system";
  state: CommandState;
  error_code: string | null;
  error: string | null;
  observation_id: string | null;
  created_at: number;
  updated_at: number;
  dispatched_at: number | null;
  completed_at: number | null;
  receipt: Receipt | null;
}

export interface CreateRequest {
  device_id: string;
  profile: string;
  seed?: number;
  budgets?: Partial<Budgets>;
  idempotency_key: string;
}

export interface CreateResult {
  run: Run;
  descriptor: Descriptor;
  observation: Observation | null;
}

export interface DetailResult extends CreateResult {
  commands: Command[];
}

export interface CommandResult {
  run: Run;
  command: Command | null;
  observation: Observation | null;
}

export interface LabIndex {
  devices: Device[];
  runs: Run[];
  latest_event_seq: number;
}

export interface StopResult {
  run: Run;
  stopped: boolean;
  semantics: string;
}

export interface CommandPage {
  commands: Command[];
  next_after_seq: number;
}

export interface ObservationPage {
  observations: Observation[];
  next_after_sequence: number;
}

export interface LabEvent {
  event_seq: number;
  root_frame_id: string;
  run_id: string;
  kind: "run" | "command" | "observation";
  ref_id: string;
  state: string | null;
  created_at: number;
}

export interface EventPage {
  events: LabEvent[];
  next_after_seq: number;
  latest_event_seq: number;
}

/** Files are immutable Artifact versions; evaluation is present only after opt-in. */
export interface ExportArtifact {
  kind: "actions" | "observations_json" | "observations_csv" | "report" | "simulation_ground_truth";
  artifact_id: string;
  version_id: string;
  filename: string;
  checksum: string;
}

export interface ExportResult {
  run_id: string;
  include_evaluation: boolean;
  command_count: number;
  observation_count: number;
  artifacts: ExportArtifact[];
}

export interface ReplayEntry {
  command: Command | null;
  observation: Observation | null;
}
