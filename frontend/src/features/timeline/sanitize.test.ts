import { beforeEach, describe, expect, it } from "vitest";
import { publicText } from "../scrub/scrub";
import { ACTION_TIMELINE_PAGE_SIZE, branchState } from "../../stores/timeline";
import { resetStoreFields } from "../../stores/signal-field";
import {
  branchUndoFromProjection,
  carryRevertPreview,
  keepUnchanged,
  mergeActionTimelines,
  publicArtifacts,
  publicList,
  queueMetadata,
  RECOVERY_ACTION_IDS,
  sanitizeActionTimeline,
  sanitizeBranches,
  mergeComputeTaskPages,
  sanitizeComputeTasks,
  sanitizeContext,
  sanitizeArtifactEvidence,
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

describe("carryRevertPreview", () => {
  const state = (head: string, extra: Record<string, unknown> = {}) =>
    sanitizeBranches({
      branch_id: "main",
      branches: [
        { branch_id: "main", head_checkpoint_id: head, checkpoints: [{ checkpoint_id: "cp-target" }, { checkpoint_id: head }] },
        // A fork that has not moved yet shares its fork point with main.
        { branch_id: "side", head_checkpoint_id: head, checkpoints: [{ checkpoint_id: "cp-target" }, { checkpoint_id: head }] },
      ],
      ...extra,
    });
  const previewOf = (fields: Record<string, unknown>) =>
    sanitizeRevertPreview({ branch_id: "main", target_checkpoint_id: "cp-target", can_apply: true, ...fields });

  it("keeps a preview whose head is still the branch head, and drops one whose head moved", () => {
    const preview = previewOf({ current_checkpoint_id: "cp-head" });
    const previous = { ...state("cp-head"), revert_preview: preview };
    expect(carryRevertPreview(previous, state("cp-head")).revert_preview).toBe(preview);
    expect(carryRevertPreview(previous, state("cp-moved")).revert_preview).toBeNull();
  });

  it("drops a preview for another branch and lets a refreshed preview win", () => {
    const preview = previewOf({ current_checkpoint_id: "cp-head" });
    const previous = { ...state("cp-head"), revert_preview: preview };
    const otherBranch = sanitizeBranches({ ...state("cp-head"), branch_id: "side" });
    expect(carryRevertPreview(previous, otherBranch).revert_preview).toBeNull();
    const refreshed = state("cp-head", {
      revert_preview: { branch_id: "main", current_checkpoint_id: "cp-head", target_checkpoint_id: "cp-head" },
    });
    expect(carryRevertPreview(previous, refreshed).revert_preview?.target_checkpoint_id).toBe("cp-head");
  });

  it("without a recorded head, keeps the preview only while its target is still listed", () => {
    const preview = previewOf({});
    const previous = { ...state("cp-head"), revert_preview: preview };
    expect(carryRevertPreview(previous, state("cp-head")).revert_preview).toBe(preview);
    const gone = sanitizeBranches({
      branch_id: "main",
      branches: [{ branch_id: "main", head_checkpoint_id: "cp-head", checkpoints: [{ checkpoint_id: "cp-head" }] }],
    });
    expect(carryRevertPreview(previous, gone).revert_preview).toBeNull();
  });
});

describe("unchanged projections keep their identity", () => {
  const timeline = (groups: unknown[]) =>
    sanitizeActionTimeline({ root_frame_id: "f", branch_id: "br", groups });

  it("a re-sent group that did not change stays the object already held", () => {
    const current = timeline([group("a", 1), group("b", 2)]);
    const merged = mergeActionTimelines(current, timeline([group("b", 2), group("c", 3)]), "latest")!;
    expect(merged).not.toBe(current);
    expect(merged.groups[1]).toBe(current.groups[1]);
    const changed = mergeActionTimelines(current, timeline([group("b", 2, { status: "failed" })]), "latest")!;
    expect(changed.groups[1]).not.toBe(current.groups[1]);
    expect(changed.groups[1]!.status).toBe("failed");
  });

  it("a merge that changes nothing returns the current projection", () => {
    const current = timeline([group("a", 1), group("b", 2)]);
    expect(mergeActionTimelines(current, timeline([group("a", 1), group("b", 2)]), "latest")).toBe(current);
  });

  it("keepUnchanged compares content, not identity", () => {
    const previous = sanitizeContext({ token_count: 3, layers: [{ name: "system" }] });
    expect(keepUnchanged(previous, sanitizeContext({ token_count: 3, layers: [{ name: "system" }] }))).toBe(previous);
    const next = sanitizeContext({ token_count: 4, layers: [{ name: "system" }] });
    expect(keepUnchanged(previous, next)).toBe(next);
    expect(keepUnchanged(null, next)).toBe(next);
  });
});

const SHA = "ab".repeat(32);

function evidenceItem(overrides: Record<string, unknown> = {}) {
  return {
    filename: "kept.csv",
    artifact_id: "a-kept",
    version_id: "v-kept",
    checksum: SHA,
    size_bytes: 14,
    capture_kind: "version_created",
    producing_cell_id: "c-cell",
    cell_status: "ok",
    verdict: "verified_version_and_producer",
    reasons: ["no_cell_receipt"],
    ...overrides,
  };
}

describe("sanitizeArtifactEvidence", () => {
  it("keeps a bounded version-and-producer record", () => {
    const clean = sanitizeArtifactEvidence({
      scope: "version_and_producer",
      items: [evidenceItem()],
      total: 1,
      truncated: false,
    });
    expect(clean).toEqual({
      scope: "version_and_producer",
      items: [
        {
          filename: "kept.csv",
          artifact_id: "a-kept",
          version_id: "v-kept",
          checksum: SHA,
          size_bytes: 14,
          capture_kind: "version_created",
          producing_cell_id: "c-cell",
          cell_status: "ok",
          verdict: "verified_version_and_producer",
          reasons: ["no_cell_receipt"],
        },
      ],
      total: 1,
      truncated: false,
    });
    expect(clean && "unavailable" in clean).toBe(false);
  });

  it("drops a bad verdict, an unknown reason, and a checksum that is not lowercase sha256", () => {
    const clean = sanitizeArtifactEvidence({
      scope: "version_and_producer",
      items: [
        evidenceItem({ verdict: "trusted" }),
        evidenceItem({
          version_id: "v-bad",
          verdict: "insufficient_evidence",
          checksum: "A".repeat(64),
          cell_status: "crashed",
          capture_kind: "guessed",
          reasons: ["no_checksum", "invented", "no_snapshot", "cell_failed", "size_mismatch", "other_frame"],
        }),
      ],
      total: 2,
      truncated: 1,
      unavailable: "yes",
    });
    expect(clean?.items.map((item) => item.version_id)).toEqual(["v-bad"]);
    expect(clean?.items[0]).toMatchObject({
      checksum: null,
      cell_status: null,
      capture_kind: null,
      reasons: ["no_checksum", "no_snapshot", "cell_failed", "size_mismatch"],
      verdict: "insufficient_evidence",
    });
    expect(clean?.truncated).toBe(false);
    expect(clean && "unavailable" in clean).toBe(false);
  });

  it("caps items at 12, reasons at 4, and strings at 200", () => {
    const items = Array.from({ length: 13 }, (_, index) =>
      evidenceItem({ version_id: `v-${index}`, filename: "n".repeat(250) }),
    );
    const clean = sanitizeArtifactEvidence({
      scope: "version_and_producer",
      items,
      total: 40,
      truncated: true,
    });
    expect(clean?.items).toHaveLength(12);
    expect(clean?.items.some((item) => item.version_id === "v-12")).toBe(false);
    expect(clean?.total).toBe(40);
    expect(clean?.truncated).toBe(true);
    expect(clean?.items[0]?.filename).toHaveLength(200);
    expect(clean?.items[0]?.filename.endsWith("…")).toBe(true);
  });

  it("drops an empty version id and downgrades a verified item with a bad checksum", () => {
    const clean = sanitizeArtifactEvidence({
      scope: "version_and_producer",
      items: [
        evidenceItem({ version_id: "  ", verdict: "verified_version_and_producer" }),
        evidenceItem({ version_id: "", verdict: "insufficient_evidence" }),
        evidenceItem({
          version_id: "v-forged",
          verdict: "verified_version_and_producer",
          checksum: "not-a-sha",
          reasons: [],
        }),
      ],
      total: 3,
      truncated: false,
      checked_at: "yesterday",
    });
    expect(clean?.items.map((item) => item.version_id)).toEqual(["v-forged"]);
    expect(clean?.items[0]).toMatchObject({
      checksum: null,
      verdict: "insufficient_evidence",
      reasons: ["no_checksum"],
    });
    expect(clean && "checked_at" in clean).toBe(false);
    const stamped = sanitizeArtifactEvidence({
      scope: "version_and_producer",
      items: [evidenceItem()],
      total: 1,
      truncated: false,
      checked_at: 1_700_000_000.5,
    });
    expect(stamped?.checked_at).toBe(1_700_000_000.5);
  });

  it("rejects a record whose scope is not the version-and-producer literal", () => {
    expect(sanitizeArtifactEvidence({ scope: "bytes", items: [evidenceItem()] })).toBeUndefined();
    expect(sanitizeArtifactEvidence(null)).toBeUndefined();
  });

  it("omits the key when the child payload has no artifact_evidence", () => {
    const absent = sanitizeDelegations({
      children: [{ child_id: "c1", status: "done" }],
    });
    expect(absent.children[0] && "artifact_evidence" in absent.children[0]).toBe(false);

    const invalid = sanitizeDelegations({
      children: [{ child_id: "c1", status: "done", artifact_evidence: { scope: "bytes", items: [] } }],
    });
    expect(invalid.children[0] && "artifact_evidence" in invalid.children[0]).toBe(false);

    const present = sanitizeDelegations({
      children: [
        {
          child_id: "c1",
          status: "done",
          artifact_evidence: {
            scope: "version_and_producer",
            items: [evidenceItem()],
            total: 1,
            truncated: false,
          },
        },
      ],
    });
    expect(present.children[0]?.artifact_evidence?.items[0]?.version_id).toBe("v-kept");
  });
});

// C2: the task list grows a same-owner total and a bounded page cursor. An old
// service sends neither, and "absent" must stay unknown: a zero there would
// be read as "no tasks in the whole session", which the page cannot support.
describe("sanitizeComputeTasks paging", () => {
  it("keeps absent paging fields unknown rather than zero", () => {
    const page = sanitizeComputeTasks({ tasks: [], live_count: 0, polled: false });
    expect(page.total_live_count).toBeUndefined();
    expect(page.has_more).toBe(false);
    expect(page.next_cursor).toBeNull();
  });

  it("reads the additive fields and bounds the opaque cursor", () => {
    const page = sanitizeComputeTasks({
      polled: false,
      live_count: 2,
      total_live_count: 17,
      has_more: true,
      next_cursor: "x".repeat(600),
      tasks: [{ job_id: "j1", status: "running", live: true }],
    });
    expect(page.total_live_count).toBe(17);
    expect(page.has_more).toBe(true);
    // Refused, not clipped: a shortened cursor is a different (invalid) cursor.
    expect(page.next_cursor).toBeNull();
  });

  it("passes a base64url cursor through byte for byte, credential-shaped runs included", () => {
    const cursor = "eyJmcCI6IiJ9-sk_AbCdEfGhIjKl-ark-0123456789";
    expect(sanitizeComputeTasks({ tasks: [], next_cursor: cursor }).next_cursor).toBe(cursor);
    expect(sanitizeComputeTasks({ tasks: [], next_cursor: "a b" }).next_cursor).toBeNull();
  });

  it("does not turn malformed counts or cursors into a claim", () => {
    const page = sanitizeComputeTasks({
      live_count: -3,
      total_live_count: "12",
      has_more: "yes",
      next_cursor: { op: "all" },
      tasks: [],
    });
    expect(page.live_count).toBe(0);
    expect(page.total_live_count).toBeUndefined();
    expect(page.has_more).toBe(false);
    expect(page.next_cursor).toBeNull();
  });

  it("merges the next page without repeating a task and keeps the newest state", () => {
    const first = sanitizeComputeTasks({
      live_count: 2,
      total_live_count: 3,
      has_more: true,
      next_cursor: "c1",
      tasks: [
        { job_id: "a", live: true },
        { job_id: "b", live: false },
      ],
    });
    const second = sanitizeComputeTasks({
      live_count: 1,
      total_live_count: 3,
      has_more: false,
      next_cursor: null,
      tasks: [
        { job_id: "b", live: true },
        { job_id: "c", live: false },
      ],
    });
    const merged = mergeComputeTaskPages(first, second);
    expect(merged.tasks.map((task) => task.job_id)).toEqual(["a", "b", "c"]);
    // The later page is the newer observation of the task it repeats.
    expect(merged.tasks[1]?.live).toBe(true);
    expect(merged.has_more).toBe(false);
    expect(merged.next_cursor).toBeNull();
    expect(merged.total_live_count).toBe(3);
    // `live_count` stays the page-scoped count over what is loaded.
    expect(merged.live_count).toBe(2);
  });

  it("merges into a missing previous page", () => {
    const merged = mergeComputeTaskPages(
      null,
      sanitizeComputeTasks({ tasks: [{ job_id: "a", live: true }], live_count: 1 }),
    );
    expect(merged.tasks).toHaveLength(1);
    expect(merged.total_live_count).toBeUndefined();
    expect(merged.next_cursor).toBeNull();
  });
});

// Captured from the real C1 route (`compute_tasks.owner_tasks` over a
// 201-job owner whose oldest job is still live, at C1 5d565c67) rather than
// hand-written: the six top-level keys, the page-scoped `live_count` next to
// the same-owner `total_live_count`, a non-empty cursor and the public task
// fields are whatever the server actually produced.
describe("C2 contract: a payload captured from the merged C1 route", () => {
  const C1_PAGE = {
    tasks: [
      {
        created_at: 5000,
        job_id: "job-001",
        live: false,
        outputs: { file_count: 0, total_bytes: 0 },
        provider: "byoc-test",
        status: "succeeded",
        terminal: true,
        terminal_at: 1,
        updated_at: 1792759716313,
      },
    ],
    live_count: 0,
    polled: false,
    total_live_count: 1,
    has_more: true,
    next_cursor: "eyJqIjoiam9iLTAwMSIsInMiOiJhbGwiLCJ0Ijo1MDAwLCJ2IjoxfQ",
  };

  it("keeps every documented field and the opaque cursor as the server sent them", () => {
    const page = sanitizeComputeTasks(C1_PAGE);
    expect(page.has_more).toBe(true);
    expect(page.next_cursor).toBe(C1_PAGE.next_cursor);
    expect(page.polled).toBe(false);
    // 200 newest rows contain no live job; the same-owner total still says 1.
    expect(page.live_count).toBe(0);
    expect(page.total_live_count).toBe(1);
    expect(page.tasks[0]).toMatchObject({
      job_id: "job-001",
      provider: "byoc-test",
      status: "succeeded",
      live: false,
      terminal: true,
      updated_at: 1792759716313,
      outputs: { file_count: 0, total_bytes: 0 },
    });
  });

  it("appends the real next page without inventing another page", () => {
    const first = sanitizeComputeTasks(C1_PAGE);
    const second = sanitizeComputeTasks({
      ...C1_PAGE,
      tasks: [
        {
          ...C1_PAGE.tasks[0],
          job_id: "job-000",
          status: "running",
          live: true,
          terminal: false,
          terminal_at: undefined,
        },
      ],
      has_more: false,
      next_cursor: null,
      live_count: 1,
    });
    const merged = mergeComputeTaskPages(first, second);
    expect(merged.tasks.map((task) => task.job_id)).toEqual(["job-001", "job-000"]);
    expect(merged.has_more).toBe(false);
    expect(merged.next_cursor).toBeNull();
    expect(merged.total_live_count).toBe(1);
    expect(merged.live_count).toBe(1);
  });
});
