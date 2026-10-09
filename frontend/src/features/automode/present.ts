/**
 * Pure text for the three status lines, the budget block and the audit rows.
 *
 * docs/auto-mode.md, "Workbench status surface" §2-§3. The lines stay three
 * separate facts: availability never inherits the preset's wording, the saved
 * selection is never rewritten to match a run, and a run's own state never
 * comes from the selection. Nothing here fetches or touches the DOM.
 */

import { LANG } from "../../i18n/runtime";
import { autoModeT } from "./copy";
import {
  BUDGET_FIELDS,
  IN_PROGRESS_STATUSES,
  type AuditRow,
  type AutoModeRun,
  type AutoModeView,
  type BudgetField,
  type BudgetMeter,
} from "./types";

/** Terminal reasons with a frozen sentence of their own (the reason table in the contract). */
const REASON_SENTENCES: ReadonlySet<string> = new Set([
  "policy_requires_explicit_setup",
  "budget_exhausted",
  "safe_rollback_unavailable",
  "outcome_unknown",
  "loop_detected",
  "safety_boundary",
  "quota_exceeded",
  "budget_measurement_unavailable",
]);

/**
 * The server's own user truth for a tripped circuit (`TERMINAL_USER_TRUTH` in
 * `server/auto_budget.py`). Shown as the server spells it, in either language:
 * the run line shows the same string untranslated, and the two must agree.
 */
const CIRCUIT_TRUTH: Readonly<Record<string, string>> = {
  budget_exhausted: "Paused · Budget exhausted",
  loop_detected: "Paused/Blocked · Loop detected",
  budget_measurement_unavailable: "无法验证 token 预算",
  quota_exceeded: "Paused · Team quota exhausted",
};

const SECONDS: ReadonlySet<BudgetField> = new Set(["wall_time_s", "guardian_timeout_s"]);

function sentenceJoin(parts: Array<string | null>): string {
  return parts.filter((part): part is string => !!part).join(LANG === "en" ? " " : "");
}

/** "{0}." in the active language, without doubling a full stop the text already ends with. */
function sentence(text: string): string {
  return autoModeT("autoMode.run.sentence", text.trim().replace(/[.。]+$/u, ""));
}

function number(value: number): string {
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)));
}

export function availabilityText(view: AutoModeView): string {
  return view.disabled_reason
    ? autoModeT("autoMode.availability." + view.disabled_reason)
    : autoModeT("autoMode.availability.available");
}

/** `{preset}; result review {mode}; approvals {reviewer}. {source label}.` */
export function selectionText(view: AutoModeView): string {
  const s = view.selection;
  return autoModeT(
    "autoMode.selection.line",
    autoModeT("autoMode.preset." + s.preset),
    autoModeT("autoMode.result." + s.result_review_mode),
    autoModeT("autoMode.approvals." + s.approvals_reviewer),
    autoModeT("autoMode.source." + s.source),
  );
}

/** Which deployment variables were actually set, when the deployment is the winning source. */
export function selectionDetail(view: AutoModeView): string | null {
  const fields = view.deployment.explicit_fields;
  if (view.selection.source !== "deployment_explicit" || !fields.length) return null;
  return autoModeT(
    "autoMode.selection.deploymentFields",
    fields.map((field) => autoModeT("autoMode.field." + field)).join(autoModeT("autoMode.list.join")),
  );
}

export function runInProgress(run: AutoModeRun): boolean {
  return IN_PROGRESS_STATUSES.has(run.status);
}

/**
 * The run's own sentence: the server's `user_truth` unchanged when it sent one,
 * then the frozen sentence for a finished run's terminal reason, then the one
 * for its status.
 */
export function runSentence(run: AutoModeRun): string {
  if (run.user_truth) return run.user_truth;
  if (run.terminal_reason && !runInProgress(run) && REASON_SENTENCES.has(run.terminal_reason)) {
    return autoModeT("autoMode.reason." + run.terminal_reason);
  }
  return autoModeT("autoMode.status." + run.status);
}

function thisRunClause(run: AutoModeRun): string | null {
  const mode = run.result_review_mode ? autoModeT("autoMode.result." + run.result_review_mode) : null;
  const reviewer = run.approvals_reviewer ? autoModeT("autoMode.approvals." + run.approvals_reviewer) : null;
  if (mode && reviewer) return autoModeT("autoMode.run.this", mode, reviewer);
  if (mode) return autoModeT("autoMode.run.thisResult", mode);
  if (reviewer) return autoModeT("autoMode.run.thisApprovals", reviewer);
  return null;
}

function roundsClause(run: AutoModeRun): string | null {
  if (run.review_round !== null && run.repair_round !== null) {
    return autoModeT("autoMode.run.rounds", run.review_round, run.repair_round);
  }
  if (run.review_round !== null) return autoModeT("autoMode.run.reviewRound", run.review_round);
  if (run.repair_round !== null) return autoModeT("autoMode.run.repairRound", run.repair_round);
  return null;
}

/** `{In progress | Finished | No Auto Run}. {sentence}. This run: …` */
export function runText(view: AutoModeView): string {
  const run = view.run;
  if (!run) return autoModeT("autoMode.run.lead", autoModeT("autoMode.run.none"));
  const progressing = runInProgress(run);
  return sentenceJoin([
    autoModeT("autoMode.run.lead", autoModeT(progressing ? "autoMode.run.inProgress" : "autoMode.run.finished")),
    sentence(runSentence(run)),
    thisRunClause(run),
    progressing ? roundsClause(run) : null,
  ]);
}

/** Identity and digests: the secondary detail row, never the run line. */
export function runDetailRows(run: AutoModeRun): Array<[string, string]> {
  const rows: Array<[string, string]> = [];
  if (run.run_id) rows.push([autoModeT("autoMode.run.id"), run.run_id]);
  if (run.turn_id) rows.push([autoModeT("autoMode.run.turn"), run.turn_id]);
  if (run.execution_id) rows.push([autoModeT("autoMode.run.execution"), run.execution_id]);
  for (const [name, value] of run.digests) rows.push([name, value]);
  return rows;
}

export type MeterFlag = "near" | "at" | "token" | null;

/**
 * The display rule; the server sends no near-limit field. A token ceiling that
 * is not frozen yet is neither near nor at its limit.
 */
export function meterFlag(field: BudgetField, meter: BudgetMeter): MeterFlag {
  if (field === "extra_token_multiplier" && meter.limit === 0 && !meter.exhausted) return "token";
  if (meter.exhausted) return "at";
  if (Number.isFinite(meter.limit) && meter.limit > 0 && meter.remaining * 5 <= meter.limit) return "near";
  return null;
}

export type BudgetRow = {
  field: BudgetField;
  label: string;
  ceiling: string | null;
  meter: string | null;
  flag: MeterFlag;
  authority: string | null;
};

export type BudgetModel = {
  rows: BudgetRow[];
  /** `run` null, `run.legacy` true, or no meter survived: usage is unknown, not zero. */
  noUsage: boolean;
  nearCount: number;
  atCount: number;
  exhausted: string | null;
  circuit: string | null;
  /** Open the block without a click: something here is a warning. */
  warn: boolean;
  summary: string;
};

function ceilingText(field: BudgetField, value: number): string {
  if (SECONDS.has(field)) return autoModeT("autoMode.budget.seconds", number(value));
  if (field === "extra_token_multiplier") return autoModeT("autoMode.budget.multiplier", number(value));
  return number(value);
}

function meterText(meter: BudgetMeter, flag: MeterFlag): string {
  if (flag === "token") return autoModeT("autoMode.budget.tokenNotFrozen");
  const parts = [autoModeT("autoMode.budget.meter", number(meter.used), number(meter.limit), number(meter.remaining))];
  if (meter.reserved > 0) parts.push(autoModeT("autoMode.budget.reserved", number(meter.reserved)));
  if (flag === "near") parts.push(autoModeT("autoMode.budget.near"));
  if (flag === "at") parts.push(autoModeT("autoMode.budget.at"));
  return parts.join(" · ");
}

export function budgetModel(view: AutoModeView): BudgetModel {
  const run = view.run;
  const usage = run && !run.legacy ? run.budget_usage : {};
  const noUsage = !run || run.legacy || Object.keys(usage).length === 0;
  const rows: BudgetRow[] = [];
  let nearCount = 0;
  let atCount = 0;
  const exhaustedLabels: string[] = [];
  for (const field of BUDGET_FIELDS) {
    const ceiling = view.budgets[field];
    const meter = noUsage ? undefined : usage[field];
    if (ceiling === undefined && !meter) continue;
    const label = autoModeT("autoMode.ceiling." + field);
    const flag = meter ? meterFlag(field, meter) : null;
    if (flag === "near") nearCount += 1;
    if (flag === "at") {
      atCount += 1;
      exhaustedLabels.push(label);
    }
    rows.push({
      field,
      label,
      ceiling: ceiling === undefined ? null : ceilingText(field, ceiling),
      meter: meter ? meterText(meter, flag) : null,
      flag,
      authority: meter && meter.authority ? autoModeT("autoMode.authority." + meter.authority) : null,
    });
  }
  const circuitReason = run && !run.legacy && run.circuit && run.circuit.state === "tripped" ? run.circuit : null;
  const budgetStop =
    !!run && (run.terminal_reason === "budget_exhausted" || (circuitReason && circuitReason.reason === "budget_exhausted"));
  const exhausted =
    budgetStop && exhaustedLabels.length
      ? autoModeT("autoMode.budget.exhausted", exhaustedLabels.join(autoModeT("autoMode.list.join")))
      : null;
  const circuit = circuitReason
    ? autoModeT(
        "autoMode.budget.circuit",
        (circuitReason.reason && CIRCUIT_TRUTH[circuitReason.reason]) || circuitReason.reason || "—",
      )
    : null;
  const summaryParts = [autoModeT("autoMode.budget.title")];
  if (noUsage) summaryParts.push(autoModeT("autoMode.budget.noUsage"));
  if (atCount) summaryParts.push(autoModeT("autoMode.budget.atCount", atCount));
  if (nearCount) summaryParts.push(autoModeT("autoMode.budget.nearCount", nearCount));
  if (circuit) summaryParts.push(circuit);
  return {
    rows,
    noUsage,
    nearCount,
    atCount,
    exhausted,
    circuit,
    warn: nearCount > 0 || atCount > 0 || !!circuit,
    summary: summaryParts.join(" · "),
  };
}

export function auditKindLabel(kind: string | null): string {
  return autoModeT("autoMode.audit.kind." + (kind || "all"));
}

/** The row's primary sentence: the server's bounded public summary, else kind and status. */
export function auditHeadline(row: AuditRow): string {
  return row.public_summary || autoModeT("autoMode.audit.fallback", auditKindLabel(row.subject_kind), row.status || "—");
}

export function formatAuditTime(ms: number | null): string | null {
  if (ms === null) return null;
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) return null;
  try {
    return date.toLocaleString(LANG === "en" ? "en-US" : "zh-CN");
  } catch {
    return date.toISOString();
  }
}

/** The facts the contract lists for an audit row, in its order, each only when present. */
export function auditFacts(row: AuditRow): Array<[string, string]> {
  const facts: Array<[string, string]> = [];
  const push = (key: string, value: string | number | null) => {
    if (value !== null && value !== "") facts.push([autoModeT("autoMode.audit.f." + key), String(value)]);
  };
  push("subject_entity_kind", autoModeT("autoMode.audit.entity." + row.subject_entity_kind));
  push("status", row.status);
  push("verdict", row.verdict);
  push("outcome", row.outcome);
  push("decision", row.decision);
  push("risk", row.risk);
  push("round", row.round);
  push("attempt", row.attempt);
  push("finding_count", row.finding_count);
  push("error_kind", row.error_kind);
  push("created_at", formatAuditTime(row.created_at));
  push("started_at", formatAuditTime(row.started_at));
  push("completed_at", formatAuditTime(row.completed_at));
  push("event_ordinal", row.event_ordinal === null ? null : "#" + row.event_ordinal);
  return facts;
}

/** Identity, profile and digests for the audit's detail row. Digests are hashes, not decision text. */
export function auditDetailRows(row: AuditRow): Array<[string, string]> {
  const rows: Array<[string, string]> = [];
  const push = (label: string, value: string | number | null) => {
    if (value !== null && value !== "") rows.push([label, String(value)]);
  };
  push(autoModeT("autoMode.audit.f.audit_id"), row.audit_id);
  push(autoModeT("autoMode.audit.f.run_id"), row.run_id);
  push(autoModeT("autoMode.audit.f.reviewer_profile_id"), row.reviewer_profile_id);
  push(autoModeT("autoMode.audit.f.profile_revision"), row.profile_revision);
  push("audit_request_digest", row.audit_request_digest);
  push("assessment_digest", row.assessment_digest);
  push("action_digest", row.action_digest);
  push("candidate_digest", row.candidate_digest);
  return rows;
}
