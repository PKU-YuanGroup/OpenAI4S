/** Validate reads before the permissive historical display transforms run. */
import type { ArtifactRow } from "../artifacts/types";
import { provenanceT } from "./copy";
import type { EnvSnapshot, LineagePayload } from "./types";

function fail(): never { throw new Error(provenanceT("invalid")); }
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail();
  return value as Record<string, unknown>;
}
function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : fail();
}
function strings(value: unknown): void {
  if (list(value).some((v) => typeof v !== "string")) fail();
}
function textFields(row: Record<string, unknown>, names: string[]): void {
  for (const key of names) if (row[key] != null && typeof row[key] !== "string") fail();
}
function cellFields(row: Record<string, unknown>): void {
  textFields(row, ["kind", "source", "language", "environment", "exit_status", "status", "kernel_id", "frame_id", "frame_kind", "producing_cell_id", "capture_kind", "observation_id", "version_id", "at"]);
  if (row.cell_recorded != null && typeof row.cell_recorded !== "boolean") fail();
  if (row.cell_index != null && !(typeof row.cell_index === "number" && Number.isFinite(row.cell_index)) && typeof row.cell_index !== "string") fail();
  for (const key of ["files_read", "files_written", "inputs"]) if (key in row) strings(row[key]);
}

export function validateLineage(value: unknown, target: ArtifactRow): LineagePayload {
  const row = record(value);
  textFields(row, ["artifact_id", "version_id", "filename"]);
  if (row.artifact_id != null && row.artifact_id !== target.id) fail();
  if (target._exactVersion && row.version_id !== target.version_id) fail();
  for (const item of list(row.interactions)) {
    const interaction = record(item);
    if (typeof interaction.kind !== "string" || !interaction.kind) fail();
    cellFields(interaction);
  }
  strings(record(row.dependency_mappings).inputs);
  if ("capture_observations" in row) for (const item of list(row.capture_observations)) {
    const capture = record(item);
    if (typeof capture.capture_kind !== "string" || !capture.capture_kind) fail();
    cellFields(capture);
    if (target._exactVersion && capture.version_id != null && capture.version_id !== target.version_id) fail();
  }
  if (row.producer != null) {
    const producer = record(row.producer);
    if (typeof producer.kind !== "string" || !producer.kind) fail();
    cellFields(producer);
  }
  // Preserve extension fields and raw evidence; validation never invents records.
  return row as LineagePayload;
}

export function validateEnvironment(value: unknown, target?: ArtifactRow): EnvSnapshot {
  const row = record(value);
  if (row.source !== "captured" && row.source !== "live") fail();
  if (target?._exactVersion && row.source !== "captured") fail();
  if (target && row.artifact_id != null && row.artifact_id !== target.id) fail();
  if (target?._exactVersion && row.version_id != null && row.version_id !== target.version_id) fail();
  textFields(row, ["source", "generation_confidence", "generation_id", "provenance", "python_version", "implementation", "kind", "environment_name", "packages_unavailable", "interpreter", "platform"]);
  // Legacy snapshots omit several runtime fields, but always record the package list.
  for (const item of list(row.packages)) {
    const pkg = record(item);
    if (typeof pkg.name !== "string" || !pkg.name) fail();
    textFields(pkg, ["version"]);
  }
  if (row.package_count != null && !(typeof row.package_count === "number" && Number.isInteger(row.package_count) && row.package_count >= 0)) fail();
  if ("remote" in row) for (const item of list(row.remote)) {
    const remote = record(item);
    textFields(remote, ["host", "engine", "service"]);
    if (typeof remote.service !== "string" || typeof remote.host !== "string") fail();
    if (remote.env != null) {
      const env = record(remote.env);
      textFields(env, ["hostname", "gpu", "conda_env", "python", "run_utc"]);
      if (Array.isArray(env.packages)) strings(env.packages);
      else if (env.packages != null) textFields(record(env.packages), Object.keys(record(env.packages)));
      if (env.code != null) {
        const code = record(env.code);
        textFields(code, ["repo", "git_commit", "wrapper_sha256"]);
        if (code.git_dirty != null && typeof code.git_dirty !== "boolean") fail();
      }
      if (env.model != null) {
        const model = record(env.model);
        textFields(model, ["name", "weights_sha256"]);
        if (model.weights_bytes != null && !(typeof model.weights_bytes === "number" && Number.isFinite(model.weights_bytes) && model.weights_bytes >= 0)) fail();
      }
    }
  }
  return row as EnvSnapshot;
}
