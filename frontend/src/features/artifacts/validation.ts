/** Required export reads must fail as a whole, never silently drop bad rows. */
import { filesT } from "./copy";
import type { ArtifactRow, ArtifactVersionRow } from "./types";

function fail(): never { throw new Error(filesT("artifact.invalidMetadata")); }
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail();
  return value as Record<string, unknown>;
}
function rows(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return fail();
  return value.map(record);
}
function validateRow(row: Record<string, unknown>, identity: string): void {
  if (typeof row[identity] !== "string" || !row[identity]) fail();
  for (const key of ["filename", "content_type", "checksum", "producing_cell_id", "created_at", "artifact_id", "root_frame_id", "project_id", "version_id", "latest_version_id"])
    if (row[key] != null && typeof row[key] !== "string") fail();
  for (const key of ["size_bytes", "ordinal"])
    if (row[key] != null && !(typeof row[key] === "number" && Number.isFinite(row[key]) && row[key] >= 0)) fail();
  if (row.priority != null && !(typeof row.priority === "number" && Number.isFinite(row.priority))) fail();
  if (row.is_latest != null && typeof row.is_latest !== "boolean") fail();
}

export function validateArtifactVersions(value: unknown, artifactId: string): ArtifactVersionRow[] {
  const envelope = record(value);
  if (envelope.artifact_id != null && envelope.artifact_id !== artifactId) fail();
  const versions = rows(envelope.versions);
  for (const row of versions) {
    validateRow(row, "version_id");
    if (row.artifact_id != null && row.artifact_id !== artifactId) fail();
  }
  return versions as ArtifactVersionRow[];
}

export function validateSessionArtifacts(value: unknown, frameId: string): ArtifactRow[] {
  const artifacts = rows(value);
  for (const row of artifacts) {
    validateRow(row, "id");
    if (row.root_frame_id != null && row.root_frame_id !== frameId) fail();
  }
  return artifacts as ArtifactRow[];
}
