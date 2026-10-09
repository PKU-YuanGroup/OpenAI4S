/**
 * The block as the session options menu shows it: three separate lines, the
 * budget block and the Audit entry, beside -- never instead of -- the legacy
 * Auto review row. Opening it, retrying it and the socket's hints only ever
 * GET; the only write the menu can make is the old row's own PATCH.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const dom = vi.hoisted(() => ({ nodes: {} as Record<string, unknown> }));
vi.mock("../sessions/chrome", () => ({
  hint: vi.fn(),
  openMenu: vi.fn(),
  closeMenu: vi.fn(),
  repositionMenu: vi.fn(),
  reportFailure: vi.fn(),
  ensureActivateKeys: vi.fn(),
}));
vi.mock("../sessions/dom", async () => {
  const { fakeEl } = await import("./testing");
  return {
    $: (selector: string) => dom.nodes[selector] ?? null,
    el: fakeEl,
    enableComposer: vi.fn(),
    clearConversationChrome: vi.fn(),
    setTitle: vi.fn(),
  };
});
vi.mock("../chrome/modal", () => ({
  openModalEl: (node: { classList: { remove: (name: string) => void } } | null) => node?.classList.remove("hidden"),
  closeModalEl: (node: { classList: { add: (name: string) => void } } | null) => node?.classList.add("hidden"),
}));
vi.mock("../sessions/conversation", () => ({ openConversation: vi.fn(), resumeWatch: vi.fn() }));
vi.mock("../sessions/compute", () => ({ openRunLocationDialog: vi.fn() }));

import { setLang, t } from "../../i18n";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { _modalMode } from "../../stores/ui";
import { closeMenu, openMenu, type MenuItem } from "../sessions/chrome";
import { sessionOptionsMenu } from "../sessions/actions";
import { resetAutoModeAudits } from "./audits";
import { autoModeHint, resetAutoModeHints } from "./hints";
import { resetAutoModeMenu } from "./menu";
import { resetAutoModeStatus } from "./status";
import {
  FakeEl,
  auditPageBody,
  auditRowBody,
  autoModeBody,
  json,
  meter,
  routeFetch,
  runBody,
  selectionBody,
  settle,
  usage,
  type FetchCall,
} from "./testing";

const WORKED = autoModeBody({
  feature_enabled: false,
  writable: false,
  disabled_reason: "stage2_feature_disabled",
  selection: selectionBody({ preset: "autonomous", result_review_mode: "auto_fix", approvals_reviewer: "auto_review", source: "deployment_explicit", explicit: true }),
  deployment: { explicit: true, explicit_fields: ["preset"] },
});

let autoMode: () => Response;
let calls: FetchCall[];

function installFetch(): void {
  calls = routeFetch((url) => {
    if (url.pathname === "/api/v1/frames/f-root/review-settings") {
      return json({ auto_review: true, reviewer_model: "", delegation_enabled: true });
    }
    if (url.pathname === "/api/v1/frames/f-root/auto-mode") return autoMode();
    if (url.pathname === "/api/v1/frames/f-root/auto-audits") return json(auditPageBody([auditRowBody(1)]));
    return json({ error: "unexpected" }, 500);
  });
}

async function openOptions(): Promise<{ items: MenuItem[]; block: FakeEl }> {
  vi.mocked(openMenu).mockClear();
  await sessionOptionsMenu({} as Element);
  await settle();
  const items = vi.mocked(openMenu).mock.calls[0]![1] as MenuItem[];
  const last = items[items.length - 1]!;
  return { items, block: last.node as unknown as FakeEl };
}

function lineValue(block: FakeEl, kind: string): string {
  const line = block.find((node) => node.dataset.line === kind)!;
  return line.byClass("am-v")!.textContent;
}

beforeEach(async () => {
  resetStoreFields();
  resetAutoModeStatus();
  resetAutoModeMenu();
  resetAutoModeAudits();
  resetAutoModeHints();
  vi.mocked(closeMenu).mockClear();
  dom.nodes = {
    "#modal": new FakeEl("div", "modal hidden"),
    "#modal-title": new FakeEl("span"),
    "#modal-body": new FakeEl("div"),
    "#modal-download": new FakeEl("a"),
  };
  autoMode = () => json(WORKED);
  installFetch();
  currentId.value = "f-root";
  await setLang("en");
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the menu block", () => {
  it("sits at the foot of the menu, after a separator, and keeps the menu's own rows", async () => {
    const { items } = await openOptions();
    expect(items.at(-2)).toEqual({ sep: true });
    const labels = items.filter((item) => item.label).map((item) => item.label!.replace("✓  ", ""));
    expect(labels).toContain(t("composer.option.autoReview"));
    expect(labels).toContain(t("composer.option.reviewerModel"));
  });

  it("shows the worked example as three separate facts, in English", async () => {
    const { block } = await openOptions();
    expect(lineValue(block, "availability")).toBe("Storage off");
    expect(lineValue(block, "selection")).toBe("Autonomous; result review Auto-fix; approvals Auto review of asks. Set by deployment.");
    expect(lineValue(block, "run")).toBe("No Auto Run.");
    expect(block.texts()).toContain("Deployment sets: preset.");
    expect(block.texts()).toContain("No usage recorded");
    expect(block.textContent).not.toMatch(/\bOn\b|\bEnabled\b|已开启/);
    expect(block.findAll((node) => node.tagName === "BUTTON").map((node) => node.textContent)).toEqual(["Audit"]);
  });

  it("shows the worked example in Chinese", async () => {
    await setLang("zh");
    const { block } = await openOptions();
    expect(lineValue(block, "availability")).toBe("存储未开启");
    expect(lineValue(block, "selection")).toBe("自主；结果审核 自动修复；审批 自动复核询问。由部署配置指定。");
    expect(lineValue(block, "run")).toBe("没有自动运行。");
    expect(block.textContent).not.toMatch(/已开启|\bOn\b/);
    expect(block.findAll((node) => node.tagName === "BUTTON").map((node) => node.textContent)).toEqual(["审计"]);
  });

  it("does not turn the saved selection on to match a checked Auto review row", async () => {
    autoMode = () => json(autoModeBody());
    const { items, block } = await openOptions();
    const autoReview = items.find((item) => item.label === "✓  " + t("composer.option.autoReview"));
    expect(autoReview).toBeDefined();
    expect(lineValue(block, "selection")).toBe("Off; result review Off; approvals You. Built-in default, no saved override.");
    await autoReview!.onClick!();
    const writes = calls.filter((call) => call.method !== "GET");
    expect(writes).toEqual([
      { path: "/api/v1/frames/f-root/review-settings", method: "PATCH", body: JSON.stringify({ auto_review: false }) },
    ]);
  });

  it("opens the budget block when a meter is near or at its ceiling", async () => {
    autoMode = () =>
      json(autoModeBody({ run: runBody({ budget_usage: usage({ max_extra_cells: meter(30, 25) }) }), last_event_ordinal: 3 }));
    const { block } = await openOptions();
    const budget = block.byClass("am-budget")!;
    expect(budget.open).toBe(true);
    expect(budget.find((node) => node.dataset.field === "max_extra_cells")!.dataset.flag).toBe("near");
    expect(block.texts()).toContain("25 of 30, 5 remaining · Near ceiling");
  });

  it("keeps the run's identity and digests in a detail row, off the run line", async () => {
    autoMode = () => json(autoModeBody({ run: runBody({ status: "candidate", candidate_digest: "d".repeat(64) }), last_event_ordinal: 3 }));
    const { block } = await openOptions();
    expect(lineValue(block, "run")).toBe("In progress. Candidate · provisional / not verified.");
    const detail = block.byClass("am-detail")!;
    expect(detail.texts()).toEqual(expect.arrayContaining(["run-1", "turn-1", "exec-1", "d".repeat(64)]));
    expect(lineValue(block, "run")).not.toContain("run-1");
  });

  it("offers a retry after a failed read, and the retry is a GET", async () => {
    let fail = true;
    autoMode = () => (fail ? json({ error: "x", code: "auto_mode_storage_unavailable" }, 503) : json(WORKED));
    const { block } = await openOptions();
    expect(["availability", "selection", "run"].map((kind) => lineValue(block, kind))).toEqual([
      "Status unavailable",
      "Status unavailable",
      "Status unavailable",
    ]);
    fail = false;
    block.byClass("am-retry")!.click();
    await settle();
    expect(lineValue(block, "availability")).toBe("Storage off");
    expect(vi.mocked(closeMenu)).not.toHaveBeenCalled();
    expect(calls.filter((call) => call.path.endsWith("/auto-mode")).every((call) => call.method === "GET")).toBe(true);
  });

  it("says the session was not found, distinctly from unavailable", async () => {
    autoMode = () => json({ error: "frame not found", code: "frame_not_found" }, 404);
    const { block } = await openOptions();
    expect(lineValue(block, "run")).toBe("Session not found");
  });

  it("opens the Audit view from the run line and reads the first page", async () => {
    const { block } = await openOptions();
    block.byClass("am-audit")!.click();
    await settle();
    expect(vi.mocked(closeMenu)).toHaveBeenCalledTimes(1);
    expect(_modalMode.value).toBe("auto-audits:f-root");
    expect((dom.nodes["#modal"] as FakeEl).classList.contains("hidden")).toBe(false);
    expect(calls.at(-1)).toMatchObject({ method: "GET", path: "/api/v1/frames/f-root/auto-audits?limit=20" });
    expect((dom.nodes["#modal-body"] as FakeEl).texts()).toContain("Audit 1 summary.");
  });
});

describe("hints while the block is open", () => {
  it("turn a canonical event for this conversation into one GET, never into the lines", async () => {
    vi.useFakeTimers();
    autoMode = () => json(autoModeBody({ run: runBody({ status: "running" }), last_event_ordinal: 3 }));
    const { block } = await openOptions();
    const reads = () => calls.filter((call) => call.path.endsWith("/auto-mode")).length;
    const before = reads();
    autoModeHint({ type: "auto_run_terminal", root_frame_id: "f-root", status: "verified", user_truth: "Verified" });
    autoModeHint({ type: "candidate_ready", root_frame_id: "f-root" });
    expect(lineValue(block, "run")).toBe("In progress. Running · not verified.");
    await vi.advanceTimersByTimeAsync(60);
    await settle();
    expect(reads()).toBe(before + 1);
    expect(lineValue(block, "run")).toBe("In progress. Running · not verified.");
  });

  it("ignore another conversation's events and non-canonical types", async () => {
    vi.useFakeTimers();
    await openOptions();
    const reads = () => calls.filter((call) => call.path.endsWith("/auto-mode")).length;
    const before = reads();
    autoModeHint({ type: "auto_run_started", root_frame_id: "f-other" });
    autoModeHint({ type: "review_started", root_frame_id: "f-root" });
    autoModeHint({ type: "frame_update", root_frame_id: "f-root" });
    await vi.advanceTimersByTimeAsync(60);
    expect(reads()).toBe(before);
  });

  it("stop once the menu has closed", async () => {
    vi.useFakeTimers();
    const { block } = await openOptions();
    block.isConnected = false;
    const reads = () => calls.filter((call) => call.path.endsWith("/auto-mode")).length;
    const before = reads();
    autoModeHint({ type: "repair_started", root_frame_id: "f-root" });
    await vi.advanceTimersByTimeAsync(60);
    expect(reads()).toBe(before);
  });

  it("never throw, whatever arrives", () => {
    expect(() => autoModeHint(null)).not.toThrow();
    expect(() => autoModeHint({ type: "auto_run_started" })).not.toThrow();
    expect(() => autoModeHint({ type: 7 } as never)).not.toThrow();
  });
});
