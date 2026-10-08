/** Public sensor-only test data. No evaluation or material composition. */
import type { Command, Descriptor, DetailResult, Device, ExportResult, Observation, Run } from "./types";
export const device: Device = { device_id: "chemgym.extractor.01", backend: "chemgymrl", mode: "simulation", title: "ChemGymRL extractor", profiles: ["WaterOilExtract-v0", "GenWurtzExtract-v2"], available: true, availability_detail: null };
export const descriptor: Descriptor = {
  contract: "openai4s.lab/v1-draft", device_id: device.device_id, mode: "simulation", backend: "chemgymrl", profile: device.profiles[0]!,
  backend_version: { package: "chemistrygym", version: "2.0.0", source_sha: "pinned", adapter_version: "1" },
  resources: [{ resource_id: "beaker_1", label: "Beaker 1", kind: "vessel" }, { resource_id: "extraction_vessel", label: "Extraction vessel", kind: "vessel" }],
  capabilities: [{ capability_id: "transfer_liquid:beaker_1->extraction_vessel", operation: "transfer_liquid", source: "beaker_1", target: "extraction_vessel", scope: "shared", parameters: { volume: { unit: "mL", allowed: [200, 400, 600, 800, 1000] } }, resources: ["beaker_1", "extraction_vessel"], observes: ["layers"], side_effect: "moves_material", terminal: false, mapping_version: "1" },
    { capability_id: "end_experiment", operation: "end_experiment", source: null, target: null, scope: "simulation_only", parameters: {}, resources: [], observes: [], side_effect: "ends_run", terminal: true, mapping_version: "1" }],
  capability_revision: "caps", observation_channels: [
    { name: "layers", kind: "array", shape: [2, 4], unit: "dimensionless", source: "simulated_sensor", available: true, description: "Sensor bands", axes: [{ name: "resource", labels: ["extraction_vessel", "beaker_1"] }, { name: "layer_px" }] },
    { name: "pressure", kind: "scalar", shape: [], unit: "dimensionless", source: "simulated_sensor", available: false, description: "Not modeled", reason: "not_modeled" },
    { name: "targets", kind: "category", shape: [], unit: "dimensionless", source: "simulated_sensor", available: true, description: "Targets" },
  ], limits: { max_steps: 50 }, stop: { supported: true, semantics: "end_session" }, time: { unit: "model_time", wall_clock_equivalent: null }, reproducibility: { status: "unverified", evidence: null }, assumptions: [],
};
export function run(patch: Partial<Run> = {}): Run {
  return { run_id: "labrun-one", mode: "simulation", backend: "chemgymrl", device_id: device.device_id, profile: device.profiles[0]!, adapter_version: "1", capability_revision: "caps", seed: 0, status: "ready", revision: 0, step_count: 0, command_count: 0, consecutive_failures: 0, end_reason: null, budgets: { max_steps: 50, max_commands: 200, max_wall_ms: 1800000, max_consecutive_failures: 3, idle_timeout_ms: 3600000 }, created_at: 1790000000000, updated_at: 1790000000000, ended_at: null, raw: { terminated: false, truncated: false }, ...patch };
}
export function observation(patch: Partial<Observation> = {}): Observation {
  return { observation_id: "labobs-one", run_id: "labrun-one", command_id: null, sequence: 0, sim_time: 0, sim_time_unit: "model_time", wall_time_ms: 1790000000000,
    channels: [
      { name: "layers", kind: "array", shape: [2, 4], unit: "dimensionless", source: "simulated_sensor", quality: "ok", value: [[.1, .1, .8, .8], [.2, .2, .6, .6]] },
      { name: "pressure", kind: "scalar", shape: [], unit: "dimensionless", source: "simulated_sensor", quality: "unavailable", value: null },
      { name: "targets", kind: "category", shape: [], unit: "dimensionless", source: "simulated_sensor", quality: "ok", value: "Target A" },
    ], artifact_version_id: null, ...patch };
}
export function command(patch: Partial<Command> = {}): Command {
  return { command_id: "labcmd-one", run_id: "labrun-one", seq: 1, idempotency_key: "same-key", operation: "transfer_liquid", capability_id: descriptor.capabilities[0]!.capability_id, request: { run_id: "labrun-one", operation: "transfer_liquid", source: "beaker_1", target: "extraction_vessel", parameters: { volume: { value: 200, unit: "mL" } }, expected_revision: 0, idempotency_key: "same-key" }, expected_revision: 0, applied_revision: null, origin: "manual_ui", state: "outcome_unknown", error_code: "outcome_unknown", error: "No confirmed receipt.", observation_id: null, created_at: 1790000000000, updated_at: 1790000000001, dispatched_at: 1790000000000, completed_at: null, receipt: null, ...patch };
}
export function detail(patch: Partial<DetailResult> = {}): DetailResult { return { run: run(), descriptor: structuredClone(descriptor), observation: observation(), commands: [], ...patch }; }
export const json = (value: unknown, status = 200): Response => new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
export function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

export function exported(patch: Partial<ExportResult> = {}): ExportResult {
  return { run_id: "labrun-one", include_evaluation: false, command_count: 2, observation_count: 2,
    artifacts: [{ kind: "observations_json", artifact_id: "artifact-one", version_id: "version-exact", filename: "observations.json", checksum: "recorded-checksum" }], ...patch };
}
