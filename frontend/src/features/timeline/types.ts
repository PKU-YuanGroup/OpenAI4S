/** Allowlist-shaped timeline / workbench payloads. Port of app.js:2795-3298. */

export type TimelineUsage = {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
};

export type TimelineEvent = {
  event_id: string;
  sequence: unknown;
  type: string;
  action_id: string;
  name: string;
  side_effect_class: string;
  resource_keys: string[];
  artifacts: string[];
  outcome: string;
  is_error: boolean;
  created_at: unknown;
};

export type TimelineAttempt = {
  attempt_id: string;
  producing_cell_id: string;
  attempt_ordinal: unknown;
  generation_id: string;
  allocated_at: unknown;
  started_at: unknown;
  response_at: unknown;
  capture_at: unknown;
  finished_at: unknown;
  terminal_state: string;
  error: string;
  replayed_from_cell_id: string;
};

export type TimelineSession = {
  root_frame_id: string;
  name: string;
} | null;

export type TimelineGroup = {
  group_id: string;
  branch_id: string;
  turn_id: string;
  ordinal: number | null;
  kind: string;
  language: string;
  provider: string;
  model: string;
  title: string;
  status: string;
  owner: string;
  permission: string;
  replay_policy: string;
  usage: TimelineUsage;
  cost: number | null;
  created_at: unknown;
  session: TimelineSession;
  events: TimelineEvent[];
  attempts: TimelineAttempt[];
};

export type ActionTimeline = {
  project_id: string;
  root_frame_id: string;
  branch_id: string;
  groups: TimelineGroup[];
  session_count: number | null;
  count: number;
  total_count: number;
  truncated: boolean;
  has_more_before: boolean;
  has_more_after: boolean;
  has_earlier: boolean;
  has_more: boolean;
  first_ordinal: number | null;
  last_ordinal: number | null;
  running: boolean;
};

export type QueueMetadata = {
  preview: string;
  model_profile_id: string;
  model_profile_revision: number | null;
};

export type ExecutionTicket = {
  execution_id: string;
  status: string;
  owner: { kind: string; id: string };
  branch_id: string;
  language: string;
  generation_id: string;
  resource_keys: string[];
  queue_position: number | null;
  queued_at: unknown;
  started_at: unknown;
  cancel_requested: boolean;
  metadata: QueueMetadata;
};

export type ExecutionQueue = {
  owner: ExecutionTicket | null;
  queue: ExecutionTicket[];
  queued_count: number;
  active_count: number;
  closed: boolean;
  close_reason: string;
};

export type RecoveryLogItem = {
  status: string;
  message: string;
  at: unknown;
};

export type RecoveryState = {
  status: string;
  progress: number | null;
  state_revision: unknown;
  branch_id: string;
  view_only: boolean;
  trust_state: string;
  explicit_recovery_required: boolean;
  python_generation_id: string;
  r_generation_id: string;
  message: string;
  log: RecoveryLogItem[];
};

export type RecoveryAction = {
  id: string;
  enabled: boolean;
  reason: string;
  requires_confirmation: boolean;
  requires_ticket: boolean;
};

export type RecoveryActions = {
  root_frame_id: string;
  branch_id: string;
  checkpoint_id: string;
  state: string;
  view_only: boolean;
  trust_state: string;
  explicit_recovery_required: boolean;
  actions: RecoveryAction[];
};

export type BranchCheckpoint = {
  checkpoint_id: string;
  parent_checkpoint_id: string;
  reason: string;
  created_at: unknown;
  message_cursor: unknown;
  action_cursor: unknown;
  cell_cursor: unknown;
  internal: boolean;
  source_kind: string;
  source_id: string;
  requires_kernel_recovery: boolean;
  undo_revert_checkpoint_id: string;
};

export type BranchRow = {
  branch_id: string;
  name: string;
  head_checkpoint_id: string;
  created_at: unknown;
  active: boolean;
  view_only: boolean;
  activatable: boolean;
  checkpoints: BranchCheckpoint[];
};

export type RevertPreview = {
  branch_id: string;
  current_checkpoint_id: string;
  target_checkpoint_id: string;
  can_apply: boolean;
  messages: { delta: number };
  notebook: { delta: number };
  actions: { delta: number };
  workspace: {
    writes_count: number;
    deletes_count: number;
    conflicts_count: number;
  };
  artifacts: { added_count: number; removed_count: number };
  environment: { changed: true } | null;
  permissions: { changed: true } | null;
};

export type BranchState = {
  root_frame_id: string;
  branch_id: string;
  capabilities: Record<string, boolean>;
  capability_reasons: Record<string, string>;
  branches: BranchRow[];
  revert_preview: RevertPreview | null;
};

export type BranchUndo = {
  branch_id: string;
  revert_checkpoint_id: string;
};

export type RevertMutationResult = {
  ok: boolean;
  branch_id: string;
  revert_checkpoint_id: string;
  requires_kernel_recovery: boolean;
};

export type ContextLayer = {
  name: string;
  kind: string;
  token_count: number | null;
  status: string;
  compressed: boolean;
};

export type ContextOmitted = {
  kind: string;
  count: number;
  reasons: Array<{ reason: string; count: number }>;
};

export type CompactionHistoryItem = {
  archive_id: string;
  branch_id: string;
  generation_id: string;
  created_at: number;
  message_count: number;
  tokens_before: number;
  tokens_after: number;
  artifact_count: number;
};

export type ContextState = {
  token_count: number | null;
  token_limit: number | null;
  output_reserve: number | null;
  message_count: number | null;
  compaction_count: number;
  handoff: boolean;
  compressed: boolean;
  layers: ContextLayer[];
  omitted: ContextOmitted[];
  compaction_history: CompactionHistoryItem[];
};

export type SecurityState = {
  sandbox: {
    state: string;
    mode: string;
    backend: string;
    enforced: boolean;
    self_test_passed: boolean;
    network_policy: string;
    detail: string;
    generation_ended: boolean;
    runtimes: Array<{
      language: string;
      source: string;
      generation_state: string;
      generation_ended: boolean;
      generation_ended_reason: string;
    }>;
  };
  permission: {
    mode: string;
    pending_count: number;
    unattended: string;
  };
};

export type DelegationChild = {
  child_id: string;
  parent_child_id: string;
  frame_id: string;
  name: string;
  status: string;
  depth: number;
  task_status: string;
  stop_reason: string;
  error: string;
  created_at: unknown;
  started_at: unknown;
  finished_at: unknown;
  progress: { turn_boundary: number; max_turns: number | null };
  steering: { queued: number; delivered: number; discarded: number };
  overrides: {
    model: string;
    steps: number | null;
    permission_count: number;
    capability_count: number;
  };
};

export type DelegationState = {
  root_frame_id: string;
  initialized: boolean;
  budget: {
    limit: number;
    spawned: number;
    active: number;
    remaining: number;
  } | null;
  stats: {
    total: number;
    pending: number;
    running: number;
    done: number;
    failed: number;
    stopped: number;
  };
  children: DelegationChild[];
};

export type ComputeTask = {
  job_id: string;
  provider: string;
  status: string;
  reason: string;
  live: boolean;
  terminal: boolean;
  updated_at: number;
  outputs: { file_count: number; total_bytes: number };
};

export type ComputeTasks = {
  polled: boolean;
  live_count: number;
  tasks: ComputeTask[];
};

export type TimelineSpanSegment = {
  phase: string;
  start: number;
  end: number;
};

export type TimelineSpan = {
  groupId: string;
  group: TimelineGroup;
  attempt: TimelineAttempt;
  rank: number;
  laneCount: number;
  times: {
    allocated: number | null;
    started: number | null;
    response: number | null;
    capture: number | null;
    finished: number | null;
  };
  segments: TimelineSpanSegment[];
  start: number;
  end: number;
  markerAt: number | null;
  pointAt: number | null;
  running: boolean;
};

export type OverviewModel = {
  items: TimelineSpan[];
  byId: Map<string, TimelineSpan>;
  laneCount: number;
  dataStart: number | null;
  dataEnd: number | null;
};

export type LedgerTurnEntry = {
  type: "turn";
  turnId: string;
  groups: TimelineGroup[];
  stats: { count: number; totalMs: number | null; hasRunning: boolean; duration: string };
  turnBoundary: boolean;
};

export type LedgerGroupEntry = {
  type: "group";
  group: TimelineGroup;
  turnId: string;
  turnStart: boolean;
  turnBoundary: boolean;
  stats: { count: number; totalMs: number | null; hasRunning: boolean; duration: string } | null;
  foldable: boolean;
};

export type LedgerEntry = LedgerTurnEntry | LedgerGroupEntry;
