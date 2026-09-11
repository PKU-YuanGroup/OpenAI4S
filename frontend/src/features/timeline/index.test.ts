import { afterEach, describe, expect, it, vi } from "vitest";
import { resetStoreFields } from "../../stores/signal-field";
import { renderBranchPanel } from "./island";
import { S } from "./s";
import { onEvent } from "../ws/registry";
import { t } from "../../i18n/runtime";
import { installTimeline } from "./index";

describe("timeline window lane", () => {
  it("publishes loadWorkbenchState for later-lane callWindow/callLane", () => {
    const target: Record<string, unknown> = {};
    installTimeline(target);
    expect(typeof target.loadWorkbenchState).toBe("function");
    expect(typeof target.renderActionTimeline).toBe("function");
  });
});


class BranchNode {
  children: BranchNode[] = [];
  textContent = "";
  tagName: string;
  disabled = false;
  onclick?: () => unknown;
  constructor(tag: string) { this.tagName = tag; }
  appendChild(child: BranchNode): BranchNode { this.children.push(child); return child; }
  walk(): BranchNode[] { return [this, ...this.children.flatMap((child) => child.walk())]; }
}

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("branch actions replace scoped history", () => {
  it.each(["activate", "revert", "undo"])("%s requests replacement instead of merging cached earlier rows", async (action) => {
    resetStoreFields();
    S.currentId = "f"; S.project = "p";
    S.branchState = {
      branch_id: "a", capabilities: { activate: true, revert: true },
      branches: [{ branch_id: "a" }, { branch_id: "b", activatable: true }],
      revert_preview: { can_apply: true, branch_id: "a", target_checkpoint_id: "cp" },
    };
    S.branchUndo = { branch_id: "a", revert_checkpoint_id: "undo" };
    vi.stubGlobal("document", {
      createElement: (tag: string) => new BranchNode(tag),
      querySelector: () => null,
    });
    const open = vi.fn(async () => {});
    vi.stubGlobal("openConversation", open);
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true, status: 200, text: async () => JSON.stringify({ status: "active", ok: true }),
    })));
    const panel = renderBranchPanel() as unknown as BranchNode;
    const label = t(action === "activate" ? "branch.activate" : action === "undo" ? "branch.undo" : "branch.revert");
    const button = panel.walk().find((node) => node.tagName === "button" && node.textContent === label);
    expect(button?.disabled).toBe(false);
    button?.onclick?.();
    await vi.waitFor(() => expect(open).toHaveBeenCalledTimes(1));
    expect(open).toHaveBeenCalledWith("f", "p", { resetHistory: true });
  });
});


it.each(["branch_projection_restored", "branch_activation_state"])("%s refresh also replaces cached branch history", async (type) => {
  resetStoreFields(); vi.useFakeTimers();
  S.currentId = "f"; S.project = "p";
  installTimeline({});
  const open = vi.fn(async () => {});
  vi.stubGlobal("openConversation", open);
  onEvent({ type, frame_id: "f", branch_id: "b" });
  await vi.advanceTimersByTimeAsync(120);
  expect(open).toHaveBeenCalledWith("f", "p", { resetHistory: true });
});
