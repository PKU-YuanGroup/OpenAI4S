import { beforeEach, describe, expect, it } from "vitest";
import { currentId, sSignals } from "../stores";
import { resetStoreFields } from "../stores/signal-field";
import {
  ACTION_TIMELINE_OVERSCAN,
  ACTION_TIMELINE_OVERVIEW_WIDTH,
  ACTION_TIMELINE_PAGE_SIZE,
  ACTION_TIMELINE_ROW_HEIGHT,
  CONTRACT_GLOBAL_NAMES,
  installWindowExports,
  type WindowExportsTarget,
} from "./window-exports";

type SBag = Record<string, unknown>;

function freshTarget(): WindowExportsTarget {
  return {};
}

describe("window.S Proxy", () => {
  let target: WindowExportsTarget;
  let S: SBag;

  beforeEach(() => {
    resetStoreFields();
    target = freshTarget();
    installWindowExports(target);
    S = target.S as SBag;
  });

  it("get returns signal.value and set writes it back", () => {
    expect(S.currentId).toBeNull();
    S.currentId = "frame-1";
    expect(S.currentId).toBe("frame-1");
    expect(currentId.value).toBe("frame-1");
    expect(sSignals.currentId!.value).toBe("frame-1");
  });

  it("covers every F-01 write-path field (top-level set)", () => {
    const writes: Array<[string, unknown]> = [
      ["_timelineHistoryLoading", { frameId: "f", branchId: "b" }],
      ["_timelineHistoryReq", 7],
      ["_timelineRestoreFocusGroupId", "g1"],
      ["_workbenchLoading", "frame-1"],
      ["_workbenchReq", 3],
      ["actionTimeline", { groups: [], branch_id: "br" }],
      ["actionTimelineSelectedBranchId", "br"],
      ["actionTimelineSelectedGroupId", "g1"],
      ["annotations", [{ id: "a1" }]],
      ["artifacts", [{ id: "art1" }]],
      ["delegationState", { children: [] }],
      ["workbenchErrors", { timelineHistory: "boom" }],
    ];
    for (const [name, value] of writes) {
      S[name] = value;
      expect(S[name], name).toBe(value);
      expect(sSignals[name]!.value, name).toBe(value);
    }
  });

  it("keeps _timelineView / actionTimeline / executionQueue identity", () => {
    const view = {
      searchQuery: "",
      searchNeedle: "",
      autoLoadArmed: true,
      collapsedTurns: new Set<string>(),
    };
    const timeline = { groups: [{ group_id: "g1" }], branch_id: "br" };
    const queue = { queue: [], owner: null };

    S._timelineView = view;
    S.actionTimeline = timeline;
    S.executionQueue = queue;

    expect(S._timelineView).toBe(view);
    expect(S.actionTimeline).toBe(timeline);
    expect(S.executionQueue).toBe(queue);

    (S._timelineView as typeof view).searchQuery = "alpha microscopy";
    (S._timelineView as typeof view).searchNeedle = "alpha microscopy";
    (S._timelineView as typeof view).autoLoadArmed = false;
    (S._timelineView as typeof view).collapsedTurns.add("turn-alpha");

    expect(S._timelineView).toBe(view);
    expect(view.searchQuery).toBe("alpha microscopy");
    expect(view.searchNeedle).toBe("alpha microscopy");
    expect(view.autoLoadArmed).toBe(false);
    expect(view.collapsedTurns.has("turn-alpha")).toBe(true);
    expect(sSignals._timelineView!.value).toBe(view);
  });

  it("nested workbenchErrors delete mutates the stored object", () => {
    const errors: Record<string, unknown> = { recoveryAction: "x", branchAction: "y" };
    S.workbenchErrors = errors;
    delete (S.workbenchErrors as Record<string, unknown>).recoveryAction;
    expect(S.workbenchErrors).toBe(errors);
    expect(errors.recoveryAction).toBeUndefined();
    expect(errors.branchAction).toBe("y");
  });

  it("nested dock.open write keeps dock identity", () => {
    const dock = S.dock as { open: boolean; tab: string };
    dock.open = true;
    expect(S.dock).toBe(dock);
    expect((sSignals.dock!.value as { open: boolean }).open).toBe(true);
  });

  it("nested _seqSeen writes keep the cursor object", () => {
    const cursors = S._seqSeen as Record<string, number>;
    cursors.fid = 12;
    expect(S._seqSeen).toBe(cursors);
    expect((sSignals._seqSeen!.value as Record<string, number>).fid).toBe(12);
  });

  it("exposes declared fields as own keys", () => {
    expect("currentId" in S).toBe(true);
    expect("actionTimeline" in S).toBe(true);
    expect(Object.keys(S)).toContain("_timelineView");
  });

  it("accepts a dynamic field write that is not in the frozen map", () => {
    S._futureLaneField = { ok: true };
    expect(S._futureLaneField).toEqual({ ok: true });
  });
});

describe("window export layer", () => {
  it("installs every F-01 contract global as a defined own property", () => {
    const target = freshTarget();
    installWindowExports(target);
    for (const name of CONTRACT_GLOBAL_NAMES) {
      expect(Object.prototype.hasOwnProperty.call(target, name), name).toBe(true);
      expect(target[name], name).not.toBeUndefined();
    }
  });

  it("exports ACTION_TIMELINE_* with app.js:2784-2789 values", () => {
    const target = freshTarget();
    installWindowExports(target);
    expect(target.ACTION_TIMELINE_PAGE_SIZE).toBe(500);
    expect(target.ACTION_TIMELINE_ROW_HEIGHT).toBe(46);
    expect(target.ACTION_TIMELINE_OVERSCAN).toBe(8);
    expect(target.ACTION_TIMELINE_OVERVIEW_WIDTH).toBe(1000);
    expect(ACTION_TIMELINE_PAGE_SIZE).toBe(500);
    expect(ACTION_TIMELINE_ROW_HEIGHT).toBe(46);
    expect(ACTION_TIMELINE_OVERSCAN).toBe(8);
    expect(ACTION_TIMELINE_OVERVIEW_WIDTH).toBe(1000);
  });

  it("does not overwrite a function a later lane already assigned", () => {
    const target = freshTarget();
    const real = () => "md";
    target.renderMd = real;
    installWindowExports(target);
    expect(target.renderMd).toBe(real);
  });

  it("stubs throw until a later lane assigns the real implementation", () => {
    const target = freshTarget();
    installWindowExports(target);
    expect(() => (target.renderActionTimeline as () => void)()).toThrow(/F-05 stub/);
  });
});
