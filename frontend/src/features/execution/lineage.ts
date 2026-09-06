/**
 * Provenance chain transforms. Port of the data path in app.js:10631-10833
 * (loadLineage / renderProvReview / env snapshot honesty).
 *
 * Pure: no DOM, no fetch, no S writes. Rendering consumes these models.
 */

import { publicText } from "../scrub/scrub";
import type {
  EnvHonesty,
  EnvPythonChip,
  EnvSnapshot,
  LineageCapture,
  LineageInteraction,
  LineagePayload,
  LineageProducer,
  LineageReviewModel,
} from "./types";

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function asList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function textList(value: unknown): string[] {
  return asList(value)
    .map((item) => publicText(item, 160))
    .filter(Boolean);
}

/** app.js:10632-10634 empty fallback. */
export function emptyLineage(): LineagePayload {
  return { interactions: [], dependency_mappings: { inputs: [] } };
}

export function asLineage(payload: unknown): LineagePayload {
  const rec = asRecord(payload);
  if (!rec) return emptyLineage();
  return {
    interactions: asList(rec.interactions) as LineageInteraction[],
    dependency_mappings: asRecord(rec.dependency_mappings) || { inputs: [] },
    capture_observations: asList(rec.capture_observations) as LineageCapture[],
    producer: (asRecord(rec.producer) as LineageProducer | null) || null,
  };
}

export function lineageCell(lin: LineagePayload | null | undefined): LineageInteraction | null {
  if (!lin) return null;
  const found = asList(lin.interactions).find((item) => {
    const rec = asRecord(item);
    return !!(rec && rec.kind === "cell");
  });
  return found ? (found as LineageInteraction) : null;
}

export function lineageSave(lin: LineagePayload | null | undefined): LineageInteraction | null {
  if (!lin) return null;
  const found = asList(lin.interactions).find((item) => {
    const rec = asRecord(item);
    return !!(rec && rec.kind === "save");
  });
  return found ? (found as LineageInteraction) : null;
}

export function lineageMappedInputs(lin: LineagePayload | null | undefined): string[] {
  if (!lin) return [];
  const mapped = lin.dependency_mappings && lin.dependency_mappings.inputs;
  return Array.isArray(mapped) ? textList(mapped) : [];
}

export function lineageCellInputs(cell: LineageInteraction | null): string[] {
  return cell && Array.isArray(cell.files_read) ? textList(cell.files_read) : [];
}

export function lineageCellWrites(cell: LineageInteraction | null): string[] {
  return cell && Array.isArray(cell.files_written) ? textList(cell.files_written) : [];
}

/**
 * app.js:10807. `head_checksum_reused` always listed; other captures only
 * when there is no producing cell (otherwise they duplicate the cell card).
 */
export function lineageCaptures(
  lin: LineagePayload | null | undefined,
  cellPresent: boolean,
): LineageCapture[] {
  const captures = asList(lin && lin.capture_observations) as LineageCapture[];
  return captures.filter((capture) => {
    if (!capture || typeof capture !== "object") return false;
    return capture.capture_kind === "head_checksum_reused" || !cellPresent;
  });
}

/**
 * A delegated capture's cell_index orders the CHILD frame's log. A
 * root-Notebook heading or view-code link for it would point at a root
 * cell that does not exist. app.js:10813-10814.
 */
export function captureInRootNotebook(capture: LineageCapture | null | undefined): boolean {
  if (!capture) return false;
  return capture.cell_index != null && capture.frame_kind !== "delegate";
}

export function lineageProducer(lin: LineagePayload | null | undefined): LineageProducer | null {
  const producer = lin && lin.producer && typeof lin.producer === "object" ? lin.producer : null;
  return producer;
}

export function lineageReviewModel(payload: unknown): LineageReviewModel {
  const lin = asLineage(payload);
  const cell = lineageCell(lin);
  const mappedInputs = lineageMappedInputs(lin);
  const cellInputs = lineageCellInputs(cell);
  const captures = lineageCaptures(lin, !!cell);
  const producer = lineageProducer(lin);
  const save = lineageSave(lin);
  const empty = !cell && !mappedInputs.length && !captures.length && !producer;
  return {
    cell,
    mappedInputs,
    cellInputs,
    captures,
    producer,
    saveAt: save && save.at,
    empty,
  };
}

/**
 * Environment snapshot honesty, three states rather than two.
 * app.js:10713-10732. `legacy_unverified` must not render as a verified
 * production environment; live fallback is a separate sentence.
 */
export function envSnapshotHonesty(env: EnvSnapshot | null | undefined): EnvHonesty {
  const source = env && env.source;
  const captured = source !== "live";
  const verified = String((env && env.generation_confidence) || "") === "verified";
  const noteKey = !captured
    ? "prov.env.liveFallback"
    : verified
      ? "prov.env.recorded"
      : "prov.env.recordedUnverified";
  return {
    captured,
    verified,
    noteKey,
    noteClass: captured && verified ? "ok" : "warn",
    showProvenanceWhy: !!(captured && !verified && env && env.provenance),
  };
}

/**
 * Only claim a Python version when the record has one. An R kernel's
 * snapshot leaves it null; "Python ?" would reintroduce the
 * misattribution the snapshot was fixed to stop telling. app.js:10699-10702.
 */
export function envPythonChip(env: EnvSnapshot | null | undefined): EnvPythonChip {
  if (!env || !env.python_version) return null;
  return {
    label: String(env.implementation || "Python"),
    value: String(env.python_version),
  };
}

export function envPackageCount(env: EnvSnapshot | null | undefined): number {
  const pkgs = (env && env.packages) || [];
  if (env && env.package_count != null) return Number(env.package_count) || 0;
  return pkgs.length;
}
