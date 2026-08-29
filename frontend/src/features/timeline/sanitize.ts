/**
 * Allowlist sanitizers for Action Timeline and workbench projections.
 * Verbatim port of app.js:2769-3276 plus sanitizeComputeTasks (4925-4946).
 *
 * These are pure: they never write S, never touch the DOM, and never fetch.
 * `sanitizeVariableInspection` reads `branchState` only to match the original
 * exact-scope gate (app.js:3154).
 */

import { publicText } from "../scrub/scrub";
import { ACTION_TIMELINE_PAGE_SIZE, branchState } from "../../stores/timeline";
import { t } from "../../i18n/runtime";
import type {
  ActionTimeline,
  BranchState,
  BranchUndo,
  ComputeTasks,
  ContextState,
  DelegationState,
  ExecutionQueue,
  ExecutionTicket,
  QueueMetadata,
  RecoveryActions,
  RecoveryState,
  RevertMutationResult,
  RevertPreview,
  SecurityState,
  TimelineAttempt,
  TimelineEvent,
  TimelineGroup,
} from "./types";

export function publicList(
  value: unknown,
  limit = 24,
  textLimit = 160,
): string[] {
  return (Array.isArray(value) ? value : [])
    .slice(0, limit)
    .map((item) => publicText(item, textLimit))
    .filter(Boolean);
}

export function publicArtifacts(result: unknown): string[] {
  const found: string[] = [];
  const add = (value: unknown) => {
    const text = publicText(value, 160);
    if (text && !found.includes(text) && found.length < 16) found.push(text);
  };
  const walk = (value: unknown, depth: number) => {
    if (depth > 2 || value == null) return;
    if (Array.isArray(value)) {
      value.slice(0, 16).forEach((item) => walk(item, depth + 1));
      return;
    }
    if (typeof value !== "object") return;
    const rec = value as Record<string, unknown>;
    ["filename", "artifact_id", "version_id"].forEach((key) => {
      if (rec[key] != null) add(rec[key]);
    });
    ["artifact", "artifacts", "files", "files_written"].forEach((key) => {
      if (rec[key] != null) walk(rec[key], depth + 1);
    });
  };
  walk(result, 0);
  return found;
}

export function timelineOrdinal(value: unknown): number | null {
  return value !== null && value !== "" && Number.isFinite(Number(value))
    ? Number(value)
    : null;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function sanitizeActionTimeline(payload: unknown): ActionTimeline {
  const source = payload && (asRecord(payload).timeline || asRecord(payload).payload || payload);
  const src = asRecord(source);
  const usage = (value: unknown) => {
    const sourceBag = value && typeof value === "object" ? asRecord(value) : {};
    const number = (key: string) =>
      Number.isSafeInteger(sourceBag[key]) && (sourceBag[key] as number) >= 0
        ? (sourceBag[key] as number)
        : 0;
    return {
      input_tokens: number("input_tokens"),
      output_tokens: number("output_tokens"),
      total_tokens: number("total_tokens"),
    };
  };
  const permission = (value: unknown) =>
    value && typeof value === "object"
      ? publicText(
          [asRecord(value).state, asRecord(value).scope].filter(Boolean).join(" · "),
          80,
        )
      : publicText(value, 80);
  const rawGroups = Array.isArray(src.groups) ? src.groups : [];
  const groups: TimelineGroup[] = rawGroups
    .slice(-ACTION_TIMELINE_PAGE_SIZE)
    .map((raw) => {
      const group = asRecord(raw);
      const events: TimelineEvent[] = (
        Array.isArray(group.events) ? group.events : []
      ).map((rawEvent) => {
        const event = asRecord(rawEvent);
        return {
          event_id: publicText(event.event_id, 96),
          sequence: event.sequence,
          type: publicText(event.type, 64),
          action_id: publicText(event.action_id, 96),
          name: publicText(event.name, 120),
          side_effect_class: publicText(event.side_effect_class, 64),
          resource_keys: publicList(event.resource_keys, 64, 160),
          artifacts: publicList(event.artifacts, 32, 200)
            .concat(publicArtifacts(event.result))
            .slice(0, 32),
          outcome: publicText(event.outcome, 32),
          is_error: !!event.is_error,
          created_at: event.created_at,
        };
      });
      const attempts: TimelineAttempt[] = (
        Array.isArray(group.attempts) ? group.attempts : []
      )
        .slice(-50)
        .map((rawAttempt) => {
          const attempt = asRecord(rawAttempt);
          return {
            attempt_id: publicText(attempt.attempt_id, 96),
            producing_cell_id: publicText(attempt.producing_cell_id, 96),
            attempt_ordinal: attempt.attempt_ordinal,
            generation_id: publicText(attempt.generation_id, 96),
            allocated_at: attempt.allocated_at,
            started_at: attempt.started_at,
            response_at: attempt.response_at,
            capture_at: attempt.capture_at,
            finished_at: attempt.finished_at,
            terminal_state: publicText(attempt.terminal_state, 48),
            error: publicText(attempt.error, 240),
            replayed_from_cell_id: publicText(attempt.replayed_from_cell_id, 96),
          };
        });
      const sessionRaw = group.session;
      const session =
        sessionRaw && typeof sessionRaw === "object"
          ? {
              root_frame_id: publicText(asRecord(sessionRaw).root_frame_id, 96),
              name: publicText(asRecord(sessionRaw).name, 160),
            }
          : null;
      const costNum = +((group.cost as number | string | undefined) ?? NaN);
      return {
        group_id: publicText(group.group_id, 96),
        branch_id: publicText(group.branch_id, 96),
        turn_id: publicText(group.turn_id, 96),
        ordinal: timelineOrdinal(group.ordinal),
        kind: publicText(group.kind, 48),
        language: publicText(group.language, 24),
        provider: publicText(group.provider, 48),
        model: publicText(group.model, 96),
        title: publicText(group.title, 260),
        status: publicText(group.status, 32),
        owner: publicText(group.owner || group.owner_kind, 80),
        permission: permission(group.permission || group.permission_state),
        replay_policy: publicText(group.replay_policy, 48),
        usage: usage(group.usage),
        cost: Number.isFinite(costNum) && costNum >= 0 ? costNum : null,
        created_at: group.created_at,
        session,
        events,
        attempts,
      };
    })
    .filter((group) => !!group.group_id);
  const firstOrdinal = timelineOrdinal(src.first_ordinal);
  const lastOrdinal = timelineOrdinal(src.last_ordinal);
  const hasMoreBefore = !!(src.has_more_before || src.has_earlier);
  const hasMoreAfter = !!(src.has_more_after || src.has_more);
  const sessionCountRaw = +(src.session_count as number | string);
  const countRaw = +(src.count as number | string);
  const totalRaw = +(src.total_count as number | string);
  const firstGroup = groups[0];
  const lastGroup = groups[groups.length - 1];
  return {
    project_id: publicText(src.project_id, 120),
    root_frame_id: publicText(src.root_frame_id, 96),
    branch_id: publicText(src.branch_id, 96),
    groups,
    session_count: Number.isFinite(sessionCountRaw)
      ? Math.max(0, sessionCountRaw)
      : null,
    count: Number.isFinite(countRaw) ? countRaw : groups.length,
    total_count: Number.isFinite(totalRaw) ? totalRaw : groups.length,
    truncated: !!src.truncated,
    has_more_before: hasMoreBefore,
    has_more_after: hasMoreAfter,
    has_earlier: hasMoreBefore,
    has_more: hasMoreAfter,
    first_ordinal:
      firstOrdinal != null ? firstOrdinal : (firstGroup ? firstGroup.ordinal : null),
    last_ordinal:
      lastOrdinal != null ? lastOrdinal : (lastGroup ? lastGroup.ordinal : null),
    running: !!src.running,
  };
}

export function mergeActionTimelines(
  current: ActionTimeline | null | undefined,
  incoming: ActionTimeline | null | undefined,
  direction = "latest",
): ActionTimeline | null | undefined {
  if (!current) return incoming;
  if (!incoming) return current;
  if (
    (current.root_frame_id &&
      incoming.root_frame_id &&
      current.root_frame_id !== incoming.root_frame_id) ||
    (current.branch_id &&
      incoming.branch_id &&
      current.branch_id !== incoming.branch_id)
  )
    return incoming;
  const deduped = new Map<string, TimelineGroup>();
  const ordered =
    direction === "before"
      ? (incoming.groups || []).concat(current.groups || [])
      : (current.groups || []).concat(incoming.groups || []);
  ordered.forEach((group) => {
    if (group && group.group_id) deduped.set(group.group_id, group);
  });
  const groups = Array.from(deduped.values()).sort((a, b) => {
    const left = timelineOrdinal(a.ordinal),
      right = timelineOrdinal(b.ordinal);
    if (left != null && right != null && left !== right) return left - right;
    return (+(a.created_at as number) || 0) - (+(b.created_at as number) || 0);
  });
  const currentFirst = timelineOrdinal(current.first_ordinal),
    incomingFirst = timelineOrdinal(incoming.first_ordinal);
  const beforeSource =
    direction === "before"
      ? incoming
      : currentFirst != null &&
          (incomingFirst == null || currentFirst <= incomingFirst)
        ? current
        : incoming;
  const afterSource = direction === "before" ? current : incoming;
  const hasMoreBefore = !!beforeSource.has_more_before;
  const hasMoreAfter = !!afterSource.has_more_after;
  const first = groups[0];
  const last = groups[groups.length - 1];
  return {
    ...afterSource,
    root_frame_id: incoming.root_frame_id || current.root_frame_id,
    branch_id: incoming.branch_id || current.branch_id,
    groups,
    count: groups.length,
    total_count: Math.max(
      +current.total_count || 0,
      +incoming.total_count || 0,
      groups.length,
    ),
    truncated: hasMoreBefore || hasMoreAfter,
    has_more_before: hasMoreBefore,
    has_more_after: hasMoreAfter,
    has_earlier: hasMoreBefore,
    has_more: hasMoreAfter,
    first_ordinal: groups.length ? (first ? first.ordinal : null) : null,
    last_ordinal: groups.length ? (last ? last.ordinal : null) : null,
    running: direction === "before" ? !!current.running : !!incoming.running,
  };
}

export function queueMetadata(raw: unknown): QueueMetadata {
  const m = asRecord(raw);
  const rev = +((m.model_profile_revision as number | string) ?? NaN);
  return {
    preview: publicText(m.preview, 160),
    model_profile_id: publicText(m.model_profile_id, 96),
    model_profile_revision: Number.isFinite(rev) && rev > 0 ? rev : null,
  };
}

export function sanitizeExecutionQueue(payload: unknown): ExecutionQueue {
  const wrapped = asRecord(payload);
  const source = asRecord(
    wrapped.execution || wrapped.payload || payload || {},
  );
  const ticket = (item: unknown): ExecutionTicket | null => {
    if (!item) return null;
    const rec = asRecord(item);
    const owner = asRecord(rec.owner);
    return {
      execution_id: publicText(rec.execution_id, 96),
      status: publicText(rec.status, 32),
      owner: {
        kind: publicText(owner.kind || rec.owner_kind, 48),
        id: publicText(owner.id || rec.owner_id, 96),
      },
      branch_id: publicText(rec.branch_id, 96),
      language: publicText(rec.language, 24),
      generation_id: publicText(rec.generation_id, 96),
      resource_keys: publicList(rec.resource_keys),
      queue_position: Number.isFinite(+(rec.queue_position as number))
        ? +(rec.queue_position as number)
        : null,
      queued_at: rec.queued_at,
      started_at: rec.started_at,
      cancel_requested: !!rec.cancel_requested,
      metadata: queueMetadata(rec.metadata),
    };
  };
  const queue = (Array.isArray(source.queue) ? source.queue : [])
    .slice(0, 100)
    .map(ticket)
    .filter((item): item is ExecutionTicket => !!item);
  return {
    owner: ticket(source.owner),
    queue,
    queued_count: Number.isFinite(+(source.queued_count as number))
      ? +(source.queued_count as number)
      : Array.isArray(source.queue)
        ? source.queue.length
        : 0,
    active_count: Number.isFinite(+(source.active_count as number))
      ? +(source.active_count as number)
      : source.owner
        ? 1
        : 0,
    closed: !!source.closed,
    close_reason: publicText(source.close_reason, 160),
  };
}

export function sanitizeRecovery(payload: unknown): RecoveryState {
  const wrapped = asRecord(payload);
  const source = asRecord(wrapped.recovery || wrapped.payload || payload || {});
  const generations = asRecord(source.generations);
  const current = asRecord(source.current);
  const candidateJournal = source.log || source.events || current.events;
  const journal = Array.isArray(candidateJournal)
    ? candidateJournal
    : /recovery_log/.test(String(source.type || ""))
      ? [source]
      : [];
  const pythonGen = asRecord(generations.python);
  return {
    status: publicText(source.status || source.state, 48),
    progress: Number.isFinite(+(source.progress as number))
      ? Math.max(0, Math.min(1, +(source.progress as number)))
      : null,
    state_revision: source.state_revision,
    branch_id: publicText(source.branch_id, 96),
    view_only: source.view_only === true,
    trust_state: publicText(source.trust_state, 32),
    explicit_recovery_required: source.explicit_recovery_required === true,
    python_generation_id: publicText(
      source.python_generation_id ||
        pythonGen.generation_id ||
        generations.python,
      96,
    ),
    r_generation_id: publicText(
      source.r_generation_id ||
        asRecord(generations.r).generation_id ||
        generations.r,
      96,
    ),
    message: publicText(
      source.message || source.reason || source.error || current.phase,
      240,
    ),
    log: journal.slice(-50).map((raw) => {
      const item = asRecord(raw);
      return {
        status: publicText(item.status || item.state || item.type, 48),
        message: publicText(
          item.message ||
            item.reason ||
            item.error ||
            [item.phase, item.status].filter(Boolean).join(": "),
          240,
        ),
        at: item.at || item.created_at,
      };
    }),
  };
}

export const RECOVERY_ACTION_IDS = ["restore", "retry", "restart_fresh"] as const;

export function sanitizeRecoveryActions(payload: unknown): RecoveryActions {
  const wrapped = asRecord(payload);
  const source = asRecord(wrapped.recovery || wrapped.payload || payload || {});
  const advertised = new Map(
    (Array.isArray(source.actions) ? source.actions : []).map((item) => {
      const rec = asRecord(item);
      return [String((rec && rec.id) || ""), rec || {}];
    }),
  );
  return {
    root_frame_id: publicText(source.root_frame_id, 96),
    branch_id: publicText(source.branch_id, 96),
    checkpoint_id: publicText(source.checkpoint_id, 96),
    state: publicText(source.state, 48),
    view_only: source.view_only === true,
    trust_state: publicText(source.trust_state, 32),
    explicit_recovery_required: source.explicit_recovery_required === true,
    actions: RECOVERY_ACTION_IDS.map((id) => {
      const item = advertised.get(id);
      return {
        id,
        enabled: !!(item && item.enabled === true),
        reason: publicText(
          item ? item.reason : t("recovery.action.unavailable"),
          240,
        ),
        requires_confirmation: !!(item && item.requires_confirmation === true),
        requires_ticket: !!(item && item.requires_ticket === true),
      };
    }),
  };
}

export function sanitizeRevertPreview(source: unknown): RevertPreview | null {
  if (!source || typeof source !== "object") return null;
  const rec = asRecord(source);
  const workspace = asRecord(rec.workspace);
  const count = (value: unknown) =>
    Array.isArray(value) ? Math.min(value.length, 1000000) : 0;
  const delta = (value: unknown) =>
    Number.isFinite(Number(value))
      ? Math.max(-1000000, Math.min(1000000, Number(value)))
      : 0;
  const setDelta = (value: unknown) => {
    const bag = asRecord(value);
    return {
      added_count: count(bag.added),
      removed_count: count(bag.removed),
    };
  };
  return {
    branch_id: publicText(rec.branch_id, 96),
    current_checkpoint_id: publicText(rec.current_checkpoint_id, 96),
    target_checkpoint_id: publicText(rec.target_checkpoint_id, 96),
    can_apply: !!rec.can_apply,
    messages: { delta: delta(asRecord(rec.messages).delta) },
    notebook: { delta: delta(asRecord(rec.notebook).delta) },
    actions: { delta: delta(asRecord(rec.actions).delta) },
    workspace: {
      writes_count: count(workspace.writes),
      deletes_count: count(workspace.deletes),
      conflicts_count: count(workspace.conflicts),
    },
    artifacts: setDelta(rec.artifacts),
    environment: rec.environment ? { changed: true } : null,
    permissions: rec.permissions ? { changed: true } : null,
  };
}

export function sanitizeBranches(payload: unknown): BranchState {
  const wrapped = asRecord(payload);
  const source = asRecord(wrapped.branch || wrapped.payload || payload || {});
  const capabilities = asRecord(source.capabilities || source.actions);
  const capabilityEnabled = (value: unknown) =>
    value === true ||
    !!(value && typeof value === "object" && asRecord(value).enabled === true);
  const capabilityReason = (value: unknown) =>
    publicText(
      value && typeof value === "object" ? asRecord(value).reason : "",
      200,
    );
  const checkpoints = (items: unknown) =>
    (Array.isArray(items) ? items : []).slice(0, 100).map((item) => {
      const cp = item && typeof item === "object" ? asRecord(item) : {};
      const metadata =
        cp.metadata && typeof cp.metadata === "object"
          ? asRecord(cp.metadata)
          : {};
      return {
        checkpoint_id: publicText(cp.checkpoint_id || cp.id, 96),
        parent_checkpoint_id: publicText(cp.parent_checkpoint_id, 96),
        reason: publicText(cp.reason, 80),
        created_at: cp.created_at,
        message_cursor: cp.message_cursor,
        action_cursor: cp.action_cursor,
        cell_cursor: cp.cell_cursor,
        internal: cp.internal === true || cp.internal === 1,
        source_kind: publicText(cp.source_kind, 24),
        source_id: publicText(cp.source_id, 96),
        requires_kernel_recovery: !!(
          metadata.requires_kernel_recovery || cp.requires_kernel_recovery
        ),
        undo_revert_checkpoint_id: publicText(
          metadata.undo_checkpoint_id ? cp.checkpoint_id || cp.id : "",
          96,
        ),
      };
    });
  const fork = asRecord(capabilities.fork);
  return {
    root_frame_id: publicText(source.root_frame_id, 96),
    branch_id: publicText(source.branch_id || source.current_branch_id, 96),
    capabilities: {
      checkpoint: capabilityEnabled(capabilities.checkpoint),
      fork: capabilityEnabled(capabilities.fork),
      fork_from_cell: capabilityEnabled(fork.fork_from_cell),
      fork_from_message: capabilityEnabled(fork.fork_from_message),
      revert_preview: capabilityEnabled(
        capabilities.revert_preview || capabilities.preview_revert,
      ),
      revert: capabilityEnabled(capabilities.revert),
      activate: capabilityEnabled(capabilities.activate),
      promote: capabilityEnabled(
        capabilities.promote || capabilities.promote_artifact,
      ),
    },
    capability_reasons: {
      checkpoint: capabilityReason(capabilities.checkpoint),
      fork: capabilityReason(capabilities.fork),
      fork_from_cell: publicText(fork.fork_from_cell_reason, 200),
      fork_from_message: publicText(fork.fork_from_message_reason, 200),
      revert_preview: capabilityReason(
        capabilities.revert_preview || capabilities.preview_revert,
      ),
      revert: capabilityReason(capabilities.revert),
      activate: capabilityReason(capabilities.activate),
    },
    branches: (Array.isArray(source.branches) ? source.branches : [])
      .slice(0, 100)
      .map((item) => {
        const branch = item && typeof item === "object" ? asRecord(item) : {};
        return {
          branch_id: publicText(branch.branch_id || branch.id, 96),
          name: publicText(branch.name, 120),
          head_checkpoint_id: publicText(branch.head_checkpoint_id, 96),
          created_at: branch.created_at,
          active: branch.active === true,
          view_only: branch.view_only === true,
          activatable: branch.activatable === true,
          checkpoints: checkpoints(branch.checkpoints),
        };
      }),
    revert_preview: sanitizeRevertPreview(source.revert_preview),
  };
}

export function branchUndoFromProjection(state: BranchState | null): BranchUndo | null {
  if (!state || !state.branch_id || !state.capabilities || state.capabilities.revert !== true)
    return null;
  const branch = (state.branches || []).find(
    (item) => item.branch_id === state.branch_id,
  );
  const checkpoint =
    branch &&
    (branch.checkpoints || []).find(
      (item) => item.checkpoint_id === branch.head_checkpoint_id,
    );
  return checkpoint && checkpoint.undo_revert_checkpoint_id
    ? {
        branch_id: state.branch_id,
        revert_checkpoint_id: checkpoint.undo_revert_checkpoint_id,
      }
    : null;
}

export function sanitizeRevertMutationResult(
  source: unknown,
): RevertMutationResult {
  const rec = asRecord(source);
  const checkpoint = asRecord(rec.checkpoint);
  return {
    ok: !!(source && rec.ok === true),
    branch_id: publicText(checkpoint.branch_id, 96),
    revert_checkpoint_id: publicText(checkpoint.checkpoint_id, 96),
    requires_kernel_recovery: !!(source && rec.requires_kernel_recovery === true),
  };
}

export function sanitizeVariableInspection(
  payload: unknown,
  frameId: string,
  language: string,
): {
  available: boolean;
  root_frame_id: string;
  branch_id: string;
  language: string;
  state: string;
  generation_id: string;
  state_revision: number;
  variables: Array<Record<string, unknown>>;
  truncated: boolean;
  reason: string;
} {
  const source = payload && typeof payload === "object" ? asRecord(payload) : {};
  const allowedStates = [
    "active",
    "busy",
    "ended",
    "not_started",
    "restoring",
    "unsupported",
    "failed",
  ];
  const state = allowedStates.includes(String(source.state || ""))
    ? String(source.state)
    : "failed";
  const storedBranch = asRecord(branchState.value).branch_id;
  const activeBranch = publicText(storedBranch, 96) || frameId;
  const exactScope =
    publicText(source.root_frame_id, 96) === frameId &&
    publicText(source.branch_id, 96) === activeBranch &&
    source.language === language;
  const primitive = (value: unknown) => {
    if (value === null || typeof value === "boolean") return value;
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string") return publicText(value, 240);
    return undefined;
  };
  const variables = (Array.isArray(source.variables) ? source.variables : [])
    .slice(0, 500)
    .map((raw) => {
      const item = raw && typeof raw === "object" ? asRecord(raw) : {};
      const safe: Record<string, unknown> = {
        name: publicText(item.name, 160),
        type: publicText(item.type, 160),
      };
      const kind = publicText(item.kind, 32);
      if (kind) safe.kind = kind;
      if (Number.isSafeInteger(item.length) && (item.length as number) >= 0)
        safe.length = Math.min(item.length as number, 1000000000000);
      const preview = primitive(item.preview);
      if (preview !== undefined) safe.preview = preview;
      const fingerprint = publicText(item.fingerprint, 128).toLowerCase();
      if (/^[a-f0-9]{8,128}$/.test(fingerprint)) safe.fingerprint = fingerprint;
      return safe;
    })
    .filter((item) => item.name && item.type);
  const available = !!(
    source.available === true &&
    exactScope &&
    state === "active"
  );
  return {
    available,
    root_frame_id: exactScope ? frameId : "",
    branch_id: exactScope ? activeBranch : "",
    language,
    state: exactScope ? state : "failed",
    generation_id: publicText(source.generation_id, 96),
    state_revision:
      Number.isSafeInteger(source.state_revision) &&
      (source.state_revision as number) >= 0
        ? (source.state_revision as number)
        : 0,
    variables: available ? variables : [],
    truncated: available && !!source.truncated,
    reason: publicText(source.reason, 200),
  };
}

export function sanitizeContext(payload: unknown): ContextState {
  const wrapped = asRecord(payload);
  const source = asRecord(wrapped.context || wrapped.payload || payload || {});
  const layers = source.layers || source.segments || source.composition || [];
  const history = Array.isArray(source.compaction_history)
    ? source.compaction_history
    : [];
  return {
    token_count: Number.isFinite(+(source.token_count as number))
      ? +(source.token_count as number)
      : null,
    token_limit: Number.isFinite(+(source.token_limit as number))
      ? +(source.token_limit as number)
      : null,
    output_reserve: Number.isFinite(+(source.output_reserve as number))
      ? +(source.output_reserve as number)
      : null,
    message_count: Number.isFinite(+(source.message_count as number))
      ? +(source.message_count as number)
      : null,
    compaction_count: Number.isFinite(+(source.compaction_count as number))
      ? Math.max(0, +(source.compaction_count as number))
      : history.length,
    handoff: !!(source.handoff || source.handoff_id),
    compressed: !!(source.compressed || source.compaction_count),
    layers: (Array.isArray(layers) ? layers : []).slice(0, 100).map((raw) => {
      const layer = asRecord(raw);
      return {
        name: publicText(layer.name || layer.kind || layer.type, 120),
        kind: publicText(layer.kind || layer.type, 64),
        token_count: Number.isFinite(+(layer.token_count as number))
          ? +(layer.token_count as number)
          : null,
        status: publicText(layer.status, 48),
        compressed: !!layer.compressed,
      };
    }),
    omitted: (Array.isArray(source.omitted) ? source.omitted : [])
      .slice(0, 20)
      .map((raw) => {
        const item = asRecord(raw);
        return {
          kind: publicText(item && item.kind, 48),
          count: Math.max(0, Number(item && item.count) || 0),
          reasons: (
            Array.isArray(item && item.reasons) ? (item.reasons as unknown[]) : []
          )
            .slice(0, 8)
            .map((r) => {
              const rec = asRecord(r);
              return {
                reason: publicText(rec && rec.reason, 48),
                count: Math.max(0, Number(rec && rec.count) || 0),
              };
            }),
        };
      }),
    compaction_history: history.slice(0, 50).map((raw) => {
      const item = asRecord(raw);
      return {
        archive_id: publicText(item && item.archive_id, 120),
        branch_id: publicText(item && item.branch_id, 120),
        generation_id: publicText(item && item.generation_id, 120),
        created_at: Number(item.created_at) || 0,
        message_count: Number.isFinite(+(item.message_count as number))
          ? Math.max(0, +(item.message_count as number))
          : 0,
        tokens_before: Number.isFinite(+(item.tokens_before as number))
          ? Math.max(0, +(item.tokens_before as number))
          : 0,
        tokens_after: Number.isFinite(+(item.tokens_after as number))
          ? Math.max(0, +(item.tokens_after as number))
          : 0,
        artifact_count: Array.isArray(item && item.artifact_refs)
          ? Math.min((item.artifact_refs as unknown[]).length, 100)
          : 0,
      };
    }),
  };
}

export function sanitizeSecurity(payload: unknown): SecurityState {
  const wrapped = asRecord(payload);
  const source = asRecord(wrapped.security || wrapped.payload || payload || {});
  const sandbox = asRecord(
    source.sandbox ||
      source.kernel_sandbox ||
      (/sandbox/.test(String(source.type || "")) ? source : {}),
  );
  const permission = asRecord(source.permission || source.permissions);
  return {
    sandbox: {
      state: publicText(sandbox.state || sandbox.status, 48),
      mode: publicText(sandbox.mode, 32),
      backend: publicText(sandbox.backend, 64),
      enforced: !!sandbox.enforced,
      self_test_passed: sandbox.self_test_passed === true,
      network_policy: publicText(sandbox.network_policy, 64),
      detail: publicText(sandbox.detail || sandbox.warning, 500),
      generation_ended: !!sandbox.generation_ended,
      runtimes: (Array.isArray(sandbox.runtimes) ? sandbox.runtimes : [])
        .slice(0, 2)
        .map((raw) => {
          const runtime = asRecord(raw);
          return {
            language: publicText(runtime.language, 16),
            source: publicText(runtime.source, 32),
            generation_state: publicText(runtime.generation_state, 48),
            generation_ended: !!runtime.generation_ended,
            generation_ended_reason: publicText(
              runtime.generation_ended_reason,
              80,
            ),
          };
        }),
    },
    permission: {
      mode: publicText(permission.mode || permission.policy, 48),
      pending_count: Number.isFinite(+(permission.pending_count as number))
        ? +(permission.pending_count as number)
        : 0,
      unattended: publicText(permission.unattended, 48),
    },
  };
}

export function sanitizeDelegations(payload: unknown): DelegationState {
  const wrapped = asRecord(payload);
  const source = asRecord(
    wrapped.delegation || wrapped.payload || payload || {},
  );
  const count = (value: unknown) =>
    Number.isSafeInteger(+((value as number) ?? NaN)) &&
    +((value as number) ?? NaN) >= 0
      ? Math.min(+((value as number) ?? 0), 1000000)
      : 0;
  const budgetSource =
    source.budget && typeof source.budget === "object"
      ? asRecord(source.budget)
      : null;
  const budget = budgetSource
    ? {
        limit: count(budgetSource.limit),
        spawned: count(budgetSource.spawned),
        active: count(budgetSource.active),
        remaining: count(budgetSource.remaining),
      }
    : null;
  const children = (Array.isArray(source.children) ? source.children : [])
    .slice(0, 1000)
    .map((raw) => {
      const item = raw && typeof raw === "object" ? asRecord(raw) : {};
      const progress =
        item.progress && typeof item.progress === "object"
          ? asRecord(item.progress)
          : {};
      const steering =
        item.steering && typeof item.steering === "object"
          ? asRecord(item.steering)
          : {};
      const overrides =
        item.overrides && typeof item.overrides === "object"
          ? asRecord(item.overrides)
          : {};
      return {
        child_id: publicText(item.child_id, 96),
        parent_child_id: publicText(item.parent_child_id, 96),
        frame_id: publicText(item.frame_id, 96),
        name: publicText(item.name, 160),
        status: publicText(item.status, 32),
        depth: Math.min(count(item.depth), 16),
        task_status: publicText(item.task_status, 32),
        stop_reason: publicText(item.stop_reason, 160),
        error: publicText(item.error, 240),
        created_at: item.created_at,
        started_at: item.started_at,
        finished_at: item.finished_at,
        progress: {
          turn_boundary: count(progress.turn_boundary),
          max_turns: count(progress.max_turns) || null,
        },
        steering: {
          queued: count(steering.queued),
          delivered: count(steering.delivered),
          discarded: count(steering.discarded),
        },
        overrides: {
          model: publicText(overrides.model, 120),
          steps: count(overrides.steps) || null,
          permission_count: Array.isArray(overrides.permissions)
            ? Math.min((overrides.permissions as unknown[]).length, 100)
            : 0,
          capability_count: Array.isArray(overrides.capabilities)
            ? Math.min((overrides.capabilities as unknown[]).length, 100)
            : 0,
        },
      };
    })
    .filter((item) => item.child_id);
  return {
    root_frame_id: publicText(source.root_frame_id, 96),
    initialized: source.initialized === true,
    budget,
    stats:
      source.stats && typeof source.stats === "object"
        ? {
            total: count(asRecord(source.stats).total),
            pending: count(asRecord(source.stats).pending),
            running: count(asRecord(source.stats).running),
            done: count(asRecord(source.stats).done),
            failed: count(asRecord(source.stats).failed),
            stopped: count(asRecord(source.stats).stopped),
          }
        : {
            total: children.length,
            pending: 0,
            running: 0,
            done: 0,
            failed: 0,
            stopped: 0,
          },
    children,
  };
}

export function sanitizeComputeTasks(payload: unknown): ComputeTasks {
  const wrapped = asRecord(payload);
  const source = asRecord(
    wrapped.tasks ? payload : wrapped.payload || payload || {},
  );
  const tasks = Array.isArray(source.tasks) ? source.tasks : [];
  return {
    polled: !!source.polled,
    live_count: Math.max(0, Number(source.live_count) || 0),
    tasks: tasks.slice(0, 200).map((raw) => {
      const task = asRecord(raw);
      const outputs = asRecord(task.outputs);
      return {
        job_id: publicText(task && task.job_id, 120),
        provider: publicText(task && task.provider, 64),
        status: publicText(task && task.status, 32) || "unknown",
        reason: publicText(task && (task.reason || task.termination_reason), 500),
        live: !!(task && task.live),
        terminal: !!(task && task.terminal),
        updated_at: Number(task && task.updated_at) || 0,
        outputs: {
          file_count: Math.max(0, Number(outputs.file_count) || 0),
          total_bytes: Math.max(0, Number(outputs.total_bytes) || 0),
        },
      };
    }),
  };
}
