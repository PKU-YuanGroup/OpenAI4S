/**
 * Allowlist sanitizers for `GET /frames/{id}/auto-mode` and
 * `GET /frames/{id}/auto-audits`.
 *
 * Pure: no DOM, no fetch, no store writes. Each returns a fresh object that
 * carries only the fields named in docs/auto-mode.md, "Workbench status
 * surface", copied one at a time. Nothing is spread from the response, so a
 * later payload that adds a prompt, a hidden rationale, a permission-request
 * body or a reusable authorization still never reaches the renderer.
 *
 * A status envelope whose schema or closed vocabulary this client does not
 * know returns `null`, and the caller shows "Status unavailable" instead of
 * guessing a layout from it.
 */

import { publicText } from "../scrub/scrub";
import {
  APPROVALS_REVIEWERS,
  AUDIT_SUBJECT_ENTITY,
  AUDIT_SUBJECT_KINDS,
  AUTO_MODE_SCHEMA_VERSION,
  BUDGET_FIELDS,
  DEPLOYMENT_FIELDS,
  DISABLED_REASONS,
  METER_AUTHORITIES,
  PRESETS,
  RESULT_REVIEW_MODES,
  RUN_STATUSES,
  SELECTION_SOURCES,
  type AuditFinding,
  type AuditPage,
  type AuditRow,
  type AutoModeRun,
  type AutoModeSelection,
  type AutoModeView,
  type BudgetCircuit,
  type BudgetField,
  type BudgetMeter,
  type DeploymentField,
} from "./types";

const MAX_IDENTIFIER = 512;
const MAX_CURSOR = 128;
const MAX_TEXT = 2000;
const MAX_AUDITS = 500;
const MAX_FINDINGS = 200;
const MAX_REFS = 64;
const HEX64 = /^[0-9a-f]{64}$/;
const CODE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const RUN_DIGESTS = [
  "candidate_digest",
  "candidate_snapshot_sha256",
  "evidence_snapshot_sha256",
  "artifact_set_sha256",
] as const;

type Rec = Record<string, unknown>;

function record(value: unknown): Rec | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Rec) : null;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[]): T | null {
  return typeof value === "string" && (allowed as readonly string[]).includes(value) ? (value as T) : null;
}

/** An identifier the server bounded; returned byte-for-byte so a cursor can be sent back. */
function identifier(value: unknown, limit = MAX_IDENTIFIER): string | null {
  return typeof value === "string" && value.length > 0 && value.length <= limit ? value : null;
}

/** A short machine code (`status`, `verdict`, `terminal_reason` …); anything else is dropped. */
function code(value: unknown): string | null {
  return typeof value === "string" && CODE.test(value) ? value : null;
}

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

function amount(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

/** Free text a model or a summary produced: bounded and credential-scrubbed. */
function prose(value: unknown, limit = MAX_TEXT): string | null {
  return typeof value === "string" && value.trim() ? publicText(value, limit) : null;
}

function digest(value: unknown): string | null {
  return typeof value === "string" && HEX64.test(value) ? value : null;
}

function identifiers(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const out: string[] = [];
  for (const item of value.slice(0, MAX_REFS)) {
    const id = identifier(item);
    if (id) out.push(id);
  }
  return out;
}

function sanitizeSelection(raw: unknown): AutoModeSelection | null {
  const s = record(raw);
  if (!s) return null;
  const preset = oneOf(s.preset, PRESETS);
  const resultReviewMode = oneOf(s.result_review_mode, RESULT_REVIEW_MODES);
  const approvalsReviewer = oneOf(s.approvals_reviewer, APPROVALS_REVIEWERS);
  const source = oneOf(s.source, SELECTION_SOURCES);
  const revision = count(s.revision);
  const sourceRevision = count(s.source_revision);
  if (!preset || !resultReviewMode || !approvalsReviewer || !source) return null;
  if (typeof s.explicit !== "boolean" || revision === null || sourceRevision === null) return null;
  return {
    preset,
    result_review_mode: resultReviewMode,
    approvals_reviewer: approvalsReviewer,
    source,
    explicit: s.explicit,
    revision,
    source_revision: sourceRevision,
  };
}

function sanitizeDeployment(raw: unknown): AutoModeView["deployment"] | null {
  const d = record(raw);
  if (!d || typeof d.explicit !== "boolean" || !Array.isArray(d.explicit_fields)) return null;
  const fields: DeploymentField[] = [];
  for (const item of d.explicit_fields) {
    const field = oneOf(item, DEPLOYMENT_FIELDS);
    if (field && !fields.includes(field)) fields.push(field);
  }
  return { explicit: d.explicit, explicit_fields: fields };
}

function sanitizeBudgets(raw: unknown): Partial<Record<BudgetField, number>> | null {
  const b = record(raw);
  if (!b) return null;
  const out: Partial<Record<BudgetField, number>> = {};
  for (const field of BUDGET_FIELDS) {
    const value = amount(b[field]);
    if (value !== null) out[field] = value;
  }
  return out;
}

function sanitizeMeter(raw: unknown): BudgetMeter | null {
  const m = record(raw);
  if (!m) return null;
  const limit = amount(m.limit);
  const used = amount(m.used);
  const reserved = amount(m.reserved);
  const remaining = amount(m.remaining);
  if (limit === null || used === null || reserved === null || remaining === null) return null;
  if (typeof m.exhausted !== "boolean") return null;
  return { limit, used, reserved, remaining, exhausted: m.exhausted, authority: oneOf(m.authority, METER_AUTHORITIES) };
}

function sanitizeCircuit(raw: unknown): BudgetCircuit | null {
  const c = record(raw);
  if (!c) return null;
  const state = c.state === "closed" || c.state === "tripped" ? c.state : null;
  if (!state) return null;
  return { state, reason: code(c.reason) };
}

function sanitizeRun(raw: unknown): AutoModeRun | null {
  const r = record(raw);
  if (!r) return null;
  const status = oneOf(r.status, RUN_STATUSES);
  if (!status) return null;
  // Anything but an explicit `false` is the store saying it has no budget
  // projection, and an empty usage map is not zero usage.
  const legacy = r.legacy !== false;
  const usage: Partial<Record<BudgetField, BudgetMeter>> = {};
  const rawUsage = legacy ? null : record(r.budget_usage);
  if (rawUsage) {
    for (const field of BUDGET_FIELDS) {
      const meter = sanitizeMeter(rawUsage[field]);
      if (meter) usage[field] = meter;
    }
  }
  const digests: Array<[string, string]> = [];
  for (const name of RUN_DIGESTS) {
    const value = digest(r[name]);
    if (value) digests.push([name, value]);
  }
  // N exists only for a finished run with issues, and a count of none is no N.
  const issues = count(r.unresolved_finding_count);
  const unresolved = status === "completed_with_issues" && issues !== null && issues >= 1 ? issues : null;
  return {
    run_id: identifier(r.run_id),
    turn_id: identifier(r.turn_id),
    execution_id: identifier(r.execution_id),
    status,
    user_truth: prose(r.user_truth, 300),
    terminal_reason: code(r.terminal_reason),
    result_review_mode: oneOf(r.result_review_mode, RESULT_REVIEW_MODES),
    approvals_reviewer: oneOf(r.approvals_reviewer, APPROVALS_REVIEWERS),
    review_round: count(r.review_round),
    repair_round: count(r.repair_round),
    unresolved_finding_count: unresolved,
    legacy,
    budget_usage: usage,
    circuit: legacy ? null : sanitizeCircuit(r.circuit),
    digests,
  };
}

/**
 * The status envelope, or `null` when this client cannot read it truthfully.
 * The three availability facts must agree with each other: a writable session
 * has no disabled reason, an unwritable one has exactly one of the two, and
 * storage that is off is never writable.
 */
export function sanitizeAutoModeView(raw: unknown): AutoModeView | null {
  const body = record(raw);
  if (!body || body.schema_version !== AUTO_MODE_SCHEMA_VERSION) return null;
  const featureEnabled = body.feature_enabled;
  const writable = body.writable;
  if (typeof featureEnabled !== "boolean" || typeof writable !== "boolean") return null;
  let disabledReason: AutoModeView["disabled_reason"] = null;
  if (body.disabled_reason !== null) {
    disabledReason = oneOf(body.disabled_reason, DISABLED_REASONS);
    if (!disabledReason) return null;
  }
  if (writable !== (disabledReason === null)) return null;
  if (writable && !featureEnabled) return null;
  if (featureEnabled && disabledReason === "stage2_feature_disabled") return null;
  const rootFrameId = identifier(body.root_frame_id);
  const branchId = identifier(body.branch_id);
  const selection = sanitizeSelection(body.selection);
  const deployment = sanitizeDeployment(body.deployment);
  const budgets = sanitizeBudgets(body.budgets);
  if (!rootFrameId || !branchId || !selection || !deployment || !budgets) return null;
  if (!("run" in body)) return null;
  let run: AutoModeRun | null = null;
  if (body.run !== null) {
    run = sanitizeRun(body.run);
    if (!run) return null;
  }
  return {
    schema_version: AUTO_MODE_SCHEMA_VERSION,
    feature_enabled: featureEnabled,
    writable,
    disabled_reason: disabledReason,
    root_frame_id: rootFrameId,
    branch_id: branchId,
    selection,
    deployment,
    budgets,
    run,
    last_event_id: identifier(body.last_event_id),
    last_event_ordinal: count(body.last_event_ordinal),
  };
}

function sanitizeFinding(raw: unknown): AuditFinding | null {
  const f = record(raw);
  if (!f) return null;
  const findingId = identifier(f.finding_id);
  if (!findingId) return null;
  return {
    finding_id: findingId,
    fingerprint: identifier(f.fingerprint),
    severity: code(f.severity),
    category: code(f.category),
    status: code(f.status),
    claim: prose(f.claim),
    evidence_refs: identifiers(f.evidence_refs),
    version_ids: identifiers(f.version_ids),
    artifact_ids: identifiers(f.artifact_ids),
    cell_ids: identifiers(f.cell_ids),
  };
}

/** One audit row, or `null` when its subject pairing is not one of the two valid ones. */
export function sanitizeAuditRow(raw: unknown): AuditRow | null {
  const a = record(raw);
  if (!a) return null;
  const auditId = identifier(a.audit_id);
  const subjectKind = oneOf(a.subject_kind, AUDIT_SUBJECT_KINDS);
  if (!auditId || !subjectKind || a.subject_entity_kind !== AUDIT_SUBJECT_ENTITY[subjectKind]) return null;
  const findings: AuditFinding[] = [];
  if (Array.isArray(a.findings)) {
    for (const item of a.findings.slice(0, MAX_FINDINGS)) {
      const finding = sanitizeFinding(item);
      if (finding) findings.push(finding);
    }
  }
  return {
    audit_id: auditId,
    run_id: identifier(a.run_id),
    subject_kind: subjectKind,
    subject_entity_kind: AUDIT_SUBJECT_ENTITY[subjectKind],
    status: code(a.status),
    verdict: code(a.verdict),
    outcome: code(a.outcome),
    decision: code(a.decision),
    risk: code(a.risk),
    round: count(a.round),
    attempt: count(a.attempt),
    finding_count: count(a.finding_count),
    public_summary: prose(a.public_summary),
    rationale_summary: prose(a.rationale_summary),
    error_kind: code(a.error_kind),
    created_at: count(a.created_at),
    started_at: count(a.started_at),
    completed_at: count(a.completed_at),
    event_ordinal: count(a.event_ordinal),
    reviewer_profile_id: identifier(a.reviewer_profile_id),
    profile_revision: count(a.profile_revision),
    audit_request_digest: digest(a.audit_request_digest),
    assessment_digest: digest(a.assessment_digest),
    action_digest: digest(a.action_digest),
    candidate_digest: digest(a.candidate_digest),
    findings,
  };
}

/**
 * One audit page, or `null` for an envelope this client does not understand.
 * `has_more` is believed only together with a cursor the client can send back.
 */
export function sanitizeAuditPage(raw: unknown): AuditPage | null {
  const body = record(raw);
  if (!body || body.schema_version !== AUTO_MODE_SCHEMA_VERSION || !Array.isArray(body.audits)) return null;
  const rootFrameId = identifier(body.root_frame_id);
  const branchId = identifier(body.branch_id);
  if (!rootFrameId || !branchId) return null;
  let subjectKind: AuditPage["subject_kind"] = null;
  if (body.subject_kind !== null && body.subject_kind !== undefined) {
    subjectKind = oneOf(body.subject_kind, AUDIT_SUBJECT_KINDS);
    if (!subjectKind) return null;
  }
  const audits: AuditRow[] = [];
  for (const item of body.audits.slice(0, MAX_AUDITS)) {
    const row = sanitizeAuditRow(item);
    if (row) audits.push(row);
  }
  const nextBefore = identifier(body.next_before, MAX_CURSOR);
  return {
    root_frame_id: rootFrameId,
    branch_id: branchId,
    subject_kind: subjectKind,
    audits,
    next_before: nextBefore,
    has_more: body.has_more === true && nextBefore !== null,
  };
}
