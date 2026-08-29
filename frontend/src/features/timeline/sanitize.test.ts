import { beforeEach, describe, expect, it } from "vitest";
import { publicText } from "../scrub/scrub";
import { ACTION_TIMELINE_PAGE_SIZE, branchState } from "../../stores/timeline";
import { resetStoreFields } from "../../stores/signal-field";
import {
  branchUndoFromProjection,
  mergeActionTimelines,
  publicArtifacts,
  publicList,
  queueMetadata,
  RECOVERY_ACTION_IDS,
  sanitizeActionTimeline,
  sanitizeBranches,
  sanitizeComputeTasks,
  sanitizeContext,
  sanitizeDelegations,
  sanitizeExecutionQueue,
  sanitizeRecovery,
  sanitizeRecoveryActions,
  sanitizeRevertMutationResult,
  sanitizeRevertPreview,
  sanitizeSecurity,
  sanitizeVariableInspection,
  timelineOrdinal,
} from "./sanitize";

function group(
  groupId: string,
  ordinal: number,
  extras: Record<string, unknown> = {},
) {
  return {
    group_id: groupId,
    ordinal,
    turn_id: "turn-a",
    branch_id: "br",
    kind: "native_tools",
    title: `Action ${ordinal}`,
    status: "completed",
    owner: "owner",
    permission: { state: "allowed", scope: "once" },
    usage: { input_tokens: 1, output_tokens: 2, total_tokens: 3 },
    cost: 0.0001,
    created_at: ordinal,
    events: [],
    attempts: [],
    ...extras,
  };
}

describe("timelineOrdinal", () => {
  it("accepts finite numbers and numeric strings, rejects empty", () => {
    expect(timelineOrdinal(0)).toBe(0);
    expect(timelineOrdinal("41")).toBe(41);
    expect(timelineOrdinal("")).toBeNull();
    expect(timelineOrdinal(null)).toBeNull();
    expect(timelineOrdinal(Number.NaN)).toBeNull();
  });
});

describe("publicList / publicArtifacts", () => {
  it("slices, redacts, and drops empties", () => {
    expect(publicList(["a", "Bearer secret", ""], 8, 80)).toEqual([
      "a",
      "Bearer [redacted]",
    ]);
    expect(publicList("not-an-array")).toEqual([]);
  });

  it("walks nested artifact keys two levels deep", () => {
    expect(
      publicArtifacts({
        filename: "plot.png",
        artifacts: [{ artifact_id: "art-1" }],
        files: [{ filename: "out.csv" }],
        nested: { too: { deep: { filename: "skip.me" } } },
      }),
    ).toEqual(["plot.png", "art-1", "out.csv"]);
  });
});

describe("sanitizeActionTimeline", () => {
  it("unwraps timeline/payload envelopes and allowlists groups", () => {
    const inner = {
      project_id: "p1",
      root_frame_id: "f1",
      branch_id: "br",
      groups: [
        group("g1", 1, {
          title: "Bearer abc.def",
          events: [
            {
              event_id: "e1",
              sequence: 0,
              type: "result",
              resource_keys: ["rk"],
              artifacts: ["a.csv"],
              result: { filename: "from-result.png" },
              is_error: 1,
            },
          ],
          attempts: Array.from({ length: 52 }, (_, i) => ({
            attempt_id: `a${i}`,
            attempt_ordinal: i,
            error: "old",
          })),
        }),
      ],
      has_earlier: true,
      has_more: true,
      first_ordinal: 1,
      last_ordinal: 1,
      running: true,
    };
    const a = sanitizeActionTimeline({ timeline: inner });
    const b = sanitizeActionTimeline({ payload: inner });
    const c = sanitizeActionTimeline(inner);
    expect(a.groups).toHaveLength(1);
    expect(a.root_frame_id).toBe("f1");
    expect(a.groups[0]?.title).toBe("Bearer [redacted]");
    expect(a.groups[0]?.permission).toBe("allowed · once");
    expect(a.groups[0]?.events[0]?.artifacts).toEqual([
      "a.csv",
      "from-result.png",
    ]);
    expect(a.groups[0]?.attempts).toHaveLength(50);
    expect(a.groups[0]?.attempts[0]?.attempt_id).toBe("a2");
    expect(a.has_more_before).toBe(true);
    expect(a.has_earlier).toBe(true);
    expect(a.has_more_after).toBe(true);
    expect(a.has_more).toBe(true);
    expect(a.running).toBe(true);
    expect(b.groups[0]?.group_id).toBe(c.groups[0]?.group_id);
  });

  it("keeps a late event past the 16th artifact and drops empty group_id", () => {
    const safe = sanitizeActionTimeline({
      groups: [
        { group_id: "", ordinal: 1 },
        group("keep", 2, {
          events: [
            {
              event_id: "late",
              artifacts: Array.from({ length: 20 }, (_, i) => `f${i}`),
              resource_keys: Array.from({ length: 70 }, (_, i) => `r${i}`),
            },
          ],
        }),
      ],
    });
    expect(safe.groups.map((g) => g.group_id)).toEqual(["keep"]);
    expect(safe.groups[0]?.events[0]?.artifacts).toHaveLength(20);
    expect(safe.groups[0]?.events[0]?.resource_keys).toHaveLength(64);
  });

  it("takes only the last PAGE_SIZE groups and falls ordinals back to them", () => {
    const groups = Array.from({ length: ACTION_TIMELINE_PAGE_SIZE + 3 }, (_, i) =>
      group(`g${i}`, i),
    );
    const safe = sanitizeActionTimeline({ groups });
    expect(safe.groups).toHaveLength(ACTION_TIMELINE_PAGE_SIZE);
    expect(safe.groups[0]?.group_id).toBe("g3");
    expect(safe.first_ordinal).toBe(3);
    expect(safe.last_ordinal).toBe(ACTION_TIMELINE_PAGE_SIZE + 2);
    expect(safe.count).toBe(ACTION_TIMELINE_PAGE_SIZE);
  });

  it("coerces usage / cost and redacts credential-shaped ids", () => {
    const safe = sanitizeActionTimeline({
      groups: [
        group("sk-abcdefghijk", 1, {
          usage: { input_tokens: -1, output_tokens: 1.5, total_tokens: 9 },
          cost: "nope",
        }),
      ],
    });
    expect(safe.groups[0]?.group_id).toBe("[redacted]");
    expect(safe.groups[0]?.usage).toEqual({
      input_tokens: 0,
      output_tokens: 0,
      total_tokens: 9,
    });
    expect(safe.groups[0]?.cost).toBeNull();
  });
});

describe("mergeActionTimelines", () => {
  const page = (
    ids: Array<[string, number]>,
    extras: Record<string, unknown> = {},
  ) =>
    sanitizeActionTimeline({
      root_frame_id: "f",
      branch_id: "br",
      groups: ids.map(([id, n]) => group(id, n)),
      total_count: ids.length,
      ...extras,
    });

  it("returns the other side when one is missing", () => {
    const incoming = page([["a", 1]]);
    expect(mergeActionTimelines(null, incoming)).toBe(incoming);
    expect(mergeActionTimelines(incoming, null)).toBe(incoming);
  });

  it("replaces on root_frame_id or branch_id mismatch", () => {
    const current = page([["a", 1]]);
    const otherFrame = sanitizeActionTimeline({
      root_frame_id: "other",
      branch_id: "br",
      groups: [group("b", 2)],
    });
    const otherBranch = sanitizeActionTimeline({
      root_frame_id: "f",
      branch_id: "other",
      groups: [group("b", 2)],
    });
    expect(mergeActionTimelines(current, otherFrame)).toBe(otherFrame);
    expect(mergeActionTimelines(current, otherBranch)).toBe(otherBranch);
  });

  it("prepends before-pages, dedupes by group_id, sorts by ordinal, does not slice", () => {
    const current = page(
      Array.from({ length: 3 }, (_, i) => [`c${i}`, 10 + i] as [string, number]),
      { has_more_before: false, has_more_after: true, running: true, total_count: 9 },
    );
    const incoming = page(
      [
        ["old", 1],
        ["c0", 10],
        ["mid", 5],
      ],
      { has_more_before: true, has_more_after: false, running: false, total_count: 9 },
    );
    const merged = mergeActionTimelines(current, incoming, "before");
    expect(merged?.groups.map((g) => g.group_id)).toEqual([
      "old",
      "mid",
      "c0",
      "c1",
      "c2",
    ]);
    expect(merged?.count).toBe(5);
    expect(merged?.has_more_before).toBe(true);
    expect(merged?.has_more_after).toBe(true);
    expect(merged?.has_earlier).toBe(true);
    expect(merged?.running).toBe(true);
    expect(merged?.first_ordinal).toBe(1);
    expect(merged?.last_ordinal).toBe(12);
    expect(merged?.total_count).toBe(9);
  });

  it("latest concat prefers incoming on the same group_id", () => {
    const current = page([["a", 1], ["b", 2]], { running: false });
    const incoming = sanitizeActionTimeline({
      root_frame_id: "f",
      branch_id: "br",
      groups: [group("b", 2, { title: "Updated B" }), group("c", 3)],
      running: true,
    });
    const merged = mergeActionTimelines(current, incoming, "latest");
    expect(merged?.groups.map((g) => g.group_id)).toEqual(["a", "b", "c"]);
    expect(merged?.groups[1]?.title).toBe("Updated B");
    expect(merged?.running).toBe(true);
  });
});

describe("sanitizeExecutionQueue", () => {
  it("freezes ticket metadata and caps the queue", () => {
    const safe = sanitizeExecutionQueue({
      execution: {
        owner: {
          execution_id: "ex-1",
          owner: { kind: "agent", id: "o1" },
          metadata: { preview: "hi", model_profile_id: "mp", model_profile_revision: 3 },
        },
        queue: Array.from({ length: 120 }, (_, i) => ({
          execution_id: `q${i}`,
          owner_kind: "agent",
          owner_id: `o${i}`,
        })),
      },
    });
    expect(safe.owner?.execution_id).toBe("ex-1");
    expect(safe.owner?.metadata).toEqual({
      preview: "hi",
      model_profile_id: "mp",
      model_profile_revision: 3,
    });
    expect(safe.queue).toHaveLength(100);
    expect(safe.queue[0]?.owner.kind).toBe("agent");
  });
});

describe("sanitizeRecovery / actions", () => {
  it("keeps a 50-event tail and the three advertised action ids", () => {
    const rec = sanitizeRecovery({
      recovery: {
        status: "restoring",
        progress: 1.5,
        log: Array.from({ length: 60 }, (_, i) => ({
          status: "step",
          message: `m${i}`,
        })),
      },
    });
    expect(rec.progress).toBe(1);
    expect(rec.log).toHaveLength(50);
    expect(rec.log[0]?.message).toBe("m10");
    const actions = sanitizeRecoveryActions({
      actions: [
        { id: "restore", enabled: true, reason: "ok" },
        { id: "inspect_log", enabled: true },
      ],
    });
    expect(actions.actions.map((a) => a.id)).toEqual([...RECOVERY_ACTION_IDS]);
    expect(actions.actions[0]?.enabled).toBe(true);
    expect(actions.actions[1]?.enabled).toBe(false);
  });
});

describe("sanitizeBranches / revert", () => {
  it("projects capabilities, checkpoints, and undo from head metadata", () => {
    const state = sanitizeBranches({
      branch: {
        branch_id: "br",
        capabilities: {
          revert: { enabled: true },
          fork: { enabled: true, fork_from_cell: true },
        },
        branches: [
          {
            branch_id: "br",
            head_checkpoint_id: "cp-head",
            checkpoints: [
              {
                checkpoint_id: "cp-head",
                metadata: { undo_checkpoint_id: "yes" },
              },
            ],
          },
        ],
        revert_preview: {
          can_apply: true,
          messages: { delta: 2 },
          workspace: { writes: [1], deletes: [], conflicts: [1, 2] },
        },
      },
    });
    expect(state.capabilities.revert).toBe(true);
    expect(state.capabilities.fork_from_cell).toBe(true);
    expect(state.revert_preview?.workspace.conflicts_count).toBe(2);
    expect(branchUndoFromProjection(state)).toEqual({
      branch_id: "br",
      revert_checkpoint_id: "cp-head",
    });
    expect(
      sanitizeRevertMutationResult({
        ok: true,
        checkpoint: { branch_id: "br", checkpoint_id: "cp-r" },
      }),
    ).toEqual({
      ok: true,
      branch_id: "br",
      revert_checkpoint_id: "cp-r",
      requires_kernel_recovery: false,
    });
    expect(sanitizeRevertPreview(null)).toBeNull();
  });
});

describe("sanitizeContext / security / delegations / compute", () => {
  it("keeps omitted reasons and compaction history", () => {
    const ctx = sanitizeContext({
      token_count: 12,
      omitted: [
        {
          kind: "images",
          count: 3,
          reasons: [{ reason: "too_large", count: 3 }],
        },
      ],
      compaction_history: [{ archive_id: "arc", tokens_before: 9, tokens_after: 2 }],
    });
    expect(ctx.omitted[0]?.kind).toBe("images");
    expect(ctx.compaction_history[0]?.tokens_before).toBe(9);
  });

  it("reads sandbox from a sandbox-typed event", () => {
    const sec = sanitizeSecurity({
      type: "sandbox_status",
      state: "enforced",
      enforced: true,
      self_test_passed: true,
    });
    expect(sec.sandbox.state).toBe("enforced");
    expect(sec.sandbox.enforced).toBe(true);
  });

  it("drops children without child_id and rebuilds stats", () => {
    const dlg = sanitizeDelegations({
      children: [
        { child_id: "c1", status: "running", depth: 2 },
        { name: "no-id" },
      ],
      budget: { limit: 8, spawned: 1, active: 1, remaining: 7 },
    });
    expect(dlg.children).toHaveLength(1);
    expect(dlg.budget?.limit).toBe(8);
    expect(dlg.stats.total).toBe(1);
  });

  it("does not infer polled", () => {
    const tasks = sanitizeComputeTasks({
      polled: false,
      live_count: 1,
      tasks: [{ job_id: "j1", status: "running", live: true }],
    });
    expect(tasks.polled).toBe(false);
    expect(tasks.tasks[0]?.job_id).toBe("j1");
  });
});

describe("sanitizeVariableInspection", () => {
  beforeEach(() => {
    resetStoreFields();
  });

  it("fails closed when the branch scope does not match", () => {
    branchState.value = { branch_id: "other" };
    const safe = sanitizeVariableInspection(
      {
        available: true,
        state: "active",
        root_frame_id: "f",
        branch_id: "br",
        language: "python",
        variables: [{ name: "x", type: "int", preview: 1 }],
      },
      "f",
      "python",
    );
    expect(safe.available).toBe(false);
    expect(safe.variables).toEqual([]);
    expect(safe.state).toBe("failed");
  });

  it("keeps primitives only for an exact-scope active kernel", () => {
    branchState.value = { branch_id: "br" };
    const safe = sanitizeVariableInspection(
      {
        available: true,
        state: "active",
        root_frame_id: "f",
        branch_id: "br",
        language: "python",
        variables: [
          { name: "x", type: "int", preview: 1 },
          { name: "s", type: "str", preview: { nope: true } },
        ],
      },
      "f",
      "python",
    );
    expect(safe.available).toBe(true);
    expect(safe.variables[0]?.preview).toBe(1);
    expect(safe.variables[1]?.preview).toBeUndefined();
  });
});

describe("queueMetadata", () => {
  it("drops non-positive revisions", () => {
    expect(queueMetadata({ model_profile_revision: 0, preview: "p" })).toEqual({
      preview: "p",
      model_profile_id: "",
      model_profile_revision: null,
    });
  });
});

describe("publicText still redacts through the sanitizer", () => {
  it("matches the F-08 kernel on the same samples", () => {
    expect(publicText("Bearer abc.def")).toBe("Bearer [redacted]");
    const safe = sanitizeActionTimeline({
      groups: [group("g", 1, { title: "sk-abcdefghijk" })],
    });
    expect(safe.groups[0]?.title).toBe("[redacted]");
  });
});
