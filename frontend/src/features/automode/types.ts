/**
 * The closed vocabularies of the two Auto Mode read routes, and the shapes the
 * sanitizer hands to the rest of this lane.
 *
 * Every list here is the server's own set (`openai4s/server/auto_mode.py`,
 * `openai4s/config.py`, `openai4s/storage/auto_mode.py`) as frozen in
 * docs/auto-mode.md, "Workbench status surface". A value outside a list is not
 * guessed at: the envelope it arrived in is treated as a schema this client
 * does not understand.
 */

export const AUTO_MODE_SCHEMA_VERSION = 1;

export const PRESETS = ["off", "autonomous"] as const;
export const RESULT_REVIEW_MODES = ["off", "review_only", "auto_fix"] as const;
export const APPROVALS_REVIEWERS = ["user", "auto_review"] as const;
export const SELECTION_SOURCES = [
  "import_quarantine",
  "frame",
  "project",
  "deployment_explicit",
  "legacy_result_review",
  "built_in_defaults",
] as const;
export const DISABLED_REASONS = ["import_quarantine", "stage2_feature_disabled"] as const;
export const DEPLOYMENT_FIELDS = ["preset", "result_review_mode", "approvals_reviewer"] as const;
export const RUN_STATUSES = [
  "running",
  "candidate",
  "reviewing",
  "repairing",
  "verified",
  "completed_with_issues",
  "review_unavailable",
  "blocked_by_guardian",
  "cancelled",
  "failed",
  "paused",
  "unverified_import",
] as const;
/** The four statuses the run line calls "In progress"; every other one is finished. */
export const IN_PROGRESS_STATUSES: ReadonlySet<string> = new Set([
  "running",
  "candidate",
  "reviewing",
  "repairing",
]);
/** The thirteen ceilings of *Frozen bounded budgets*, in that table's order. */
export const BUDGET_FIELDS = [
  "max_review_rounds",
  "max_repair_rounds",
  "repair_turns_per_round",
  "max_extra_cells",
  "wall_time_s",
  "extra_token_multiplier",
  "repeated_finding_limit",
  "same_action_no_delta_limit",
  "no_progress_turn_limit",
  "guardian_timeout_s",
  "guardian_consecutive_denial_limit",
  "guardian_window_size",
  "guardian_window_denial_limit",
] as const;
export const METER_AUTHORITIES = ["auto_budget", "guardian"] as const;
export const AUDIT_SUBJECT_KINDS = ["result_review", "permission_review"] as const;
/** The only valid `subject_kind` → `subject_entity_kind` pairings. */
export const AUDIT_SUBJECT_ENTITY: Readonly<Record<AuditSubjectKind, string>> = {
  result_review: "candidate_evidence_snapshot",
  permission_review: "approval_action",
};
/**
 * The seven canonical event types. They are refresh hints only: none of their
 * fields is ever copied onto the status lines.
 */
export const CANONICAL_AUTO_EVENTS: ReadonlySet<string> = new Set([
  "auto_run_started",
  "candidate_ready",
  "auto_audit_started",
  "auto_audit_completed",
  "repair_started",
  "repair_completed",
  "auto_run_terminal",
]);

export type Preset = (typeof PRESETS)[number];
export type ResultReviewMode = (typeof RESULT_REVIEW_MODES)[number];
export type ApprovalsReviewer = (typeof APPROVALS_REVIEWERS)[number];
export type SelectionSource = (typeof SELECTION_SOURCES)[number];
export type DisabledReason = (typeof DISABLED_REASONS)[number];
export type DeploymentField = (typeof DEPLOYMENT_FIELDS)[number];
export type RunStatus = (typeof RUN_STATUSES)[number];
export type BudgetField = (typeof BUDGET_FIELDS)[number];
export type MeterAuthority = (typeof METER_AUTHORITIES)[number];
export type AuditSubjectKind = (typeof AUDIT_SUBJECT_KINDS)[number];

export type AutoModeSelection = {
  preset: Preset;
  result_review_mode: ResultReviewMode;
  approvals_reviewer: ApprovalsReviewer;
  source: SelectionSource;
  explicit: boolean;
  /** The frame row's revision: the freshness tiebreak when the event cursor did not move. */
  revision: number;
  source_revision: number;
};

export type BudgetMeter = {
  limit: number;
  used: number;
  reserved: number;
  remaining: number;
  exhausted: boolean;
  authority: MeterAuthority | null;
};

export type BudgetCircuit = { state: "closed" | "tripped"; reason: string | null };

export type AutoModeRun = {
  run_id: string | null;
  turn_id: string | null;
  execution_id: string | null;
  status: RunStatus;
  user_truth: string | null;
  terminal_reason: string | null;
  /** This run's own sub-modes, frozen when it started; never the saved selection. */
  result_review_mode: ResultReviewMode | null;
  approvals_reviewer: ApprovalsReviewer | null;
  /** Round indexes as the server sends them, counted from 0. The run line shows them counted from 1. */
  review_round: number | null;
  repair_round: number | null;
  /** N in "Completed · unverified · N unresolved issues": `completed_with_issues` only, at least 1. */
  unresolved_finding_count: number | null;
  /** True when the store returned no budget projection: usage is unknown, not zero. */
  legacy: boolean;
  budget_usage: Partial<Record<BudgetField, BudgetMeter>>;
  circuit: BudgetCircuit | null;
  /** Hash-shaped digests only, for the detail row. */
  digests: Array<[string, string]>;
};

export type AutoModeView = {
  schema_version: 1;
  feature_enabled: boolean;
  writable: boolean;
  disabled_reason: DisabledReason | null;
  root_frame_id: string;
  branch_id: string;
  selection: AutoModeSelection;
  deployment: { explicit: boolean; explicit_fields: DeploymentField[] };
  budgets: Partial<Record<BudgetField, number>>;
  run: AutoModeRun | null;
  last_event_id: string | null;
  last_event_ordinal: number | null;
};

export type AuditFinding = {
  finding_id: string;
  fingerprint: string | null;
  severity: string | null;
  category: string | null;
  status: string | null;
  claim: string | null;
  evidence_refs: string[];
  version_ids: string[];
  artifact_ids: string[];
  cell_ids: string[];
};

export type AuditRow = {
  audit_id: string;
  run_id: string | null;
  subject_kind: AuditSubjectKind;
  subject_entity_kind: string;
  status: string | null;
  verdict: string | null;
  outcome: string | null;
  decision: string | null;
  risk: string | null;
  round: number | null;
  attempt: number | null;
  finding_count: number | null;
  public_summary: string | null;
  rationale_summary: string | null;
  error_kind: string | null;
  created_at: number | null;
  started_at: number | null;
  completed_at: number | null;
  event_ordinal: number | null;
  reviewer_profile_id: string | null;
  profile_revision: number | null;
  audit_request_digest: string | null;
  assessment_digest: string | null;
  action_digest: string | null;
  candidate_digest: string | null;
  findings: AuditFinding[];
};

export type AuditPage = {
  root_frame_id: string;
  branch_id: string;
  subject_kind: AuditSubjectKind | null;
  audits: AuditRow[];
  next_before: string | null;
  /** True only when `next_before` is present: a page with no cursor is the last one. */
  has_more: boolean;
};
