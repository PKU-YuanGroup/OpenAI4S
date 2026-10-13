/**
 * The three lines stay three facts, in both languages: availability, the
 * saved selection and its source, and the run's own state. docs/auto-mode.md
 * §2-§3 fixes the copy; the worked example (storage off, deployment preset
 * autonomous, no run) must not read as "On" / "已开启" anywhere.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { LANG, setLang } from "../../i18n";
import { autoModeCopyKeys } from "./copy";
import {
  availabilityText,
  budgetModel,
  meterFlag,
  runText,
  selectionDetail,
  selectionText,
} from "./present";
import { sanitizeAutoModeView } from "./sanitize";
import { autoModeBody, meter, runBody, selectionBody, usage } from "./testing";
import type { AutoModeView } from "./types";

function view(overrides: Record<string, unknown> = {}): AutoModeView {
  const parsed = sanitizeAutoModeView(autoModeBody(overrides));
  if (!parsed) throw new Error("fixture did not sanitize");
  return parsed;
}

const WORKED = {
  feature_enabled: false,
  writable: false,
  disabled_reason: "stage2_feature_disabled",
  selection: selectionBody({
    preset: "autonomous",
    result_review_mode: "auto_fix",
    approvals_reviewer: "auto_review",
    source: "deployment_explicit",
    explicit: true,
  }),
  deployment: { explicit: true, explicit_fields: ["preset"] },
  run: null,
};

const BANNED = /\bOn\b|\bEnabled\b|已开启/;

function lines(v: AutoModeView): string[] {
  return [availabilityText(v), selectionText(v), selectionDetail(v) || "", runText(v), budgetModel(v).summary];
}

let previous: string;
beforeAll(() => {
  previous = LANG;
});
afterAll(async () => {
  await setLang(previous);
});

describe("in English", () => {
  beforeAll(async () => {
    await setLang("en");
  });

  it("reads the worked example as storage off, saved autonomous, and no Auto Run", () => {
    const v = view(WORKED);
    expect(availabilityText(v)).toBe("Storage off");
    expect(selectionText(v)).toBe("Autonomous; result review Auto-fix; approvals Auto review of asks. Set by deployment.");
    expect(selectionDetail(v)).toBe("Deployment sets: preset.");
    expect(runText(v)).toBe("No Auto Run.");
    for (const text of lines(v)) expect(text).not.toMatch(BANNED);
  });

  it.each([
    ["stage2_feature_disabled", false, "Storage off"],
    ["import_quarantine", true, "Imported session, read only"],
    ["import_quarantine", false, "Imported session, read only"],
  ])("names the availability for %s (feature_enabled=%s)", (reason, enabled, text) => {
    expect(availabilityText(view({ feature_enabled: enabled, writable: false, disabled_reason: reason }))).toBe(text);
  });

  it("says storage is available only when it is writable", () => {
    expect(availabilityText(view())).toBe("Storage available");
  });

  it.each([
    ["frame", "Saved on this conversation"],
    ["project", "Saved on this project"],
    ["deployment_explicit", "Set by deployment"],
    ["legacy_result_review", "Inherited from Auto review"],
    ["built_in_defaults", "Built-in default, no saved override"],
    ["import_quarantine", "Held at the safe default after import"],
  ])("labels source %s", (source, label) => {
    const text = selectionText(view({ selection: selectionBody({ source, explicit: source !== "built_in_defaults" }) }));
    expect(text).toBe(`Off; result review Off; approvals You. ${label}.`);
  });

  it("does not call an inherited legacy selection one saved on this conversation", () => {
    const text = selectionText(
      view({ selection: selectionBody({ source: "legacy_result_review", explicit: true, result_review_mode: "review_only" }) }),
    );
    expect(text).toBe("Off; result review Review only; approvals You. Inherited from Auto review.");
    expect(text).not.toContain("Saved on this conversation");
  });

  it("names only the deployment fields that were set, and only for that source", () => {
    expect(selectionDetail(view({ deployment: { explicit: true, explicit_fields: ["preset"] } }))).toBeNull();
    expect(
      selectionDetail(
        view({
          selection: selectionBody({ source: "deployment_explicit", explicit: true }),
          deployment: { explicit: true, explicit_fields: ["result_review_mode", "approvals_reviewer"] },
        }),
      ),
    ).toBe("Deployment sets: result review, approvals.");
  });

  it("keeps the saved selection off while the run is in review", () => {
    // The server counts rounds from 0: this is the re-review after the first repair.
    const v = view({ run: runBody({ status: "reviewing", result_review_mode: "auto_fix", approvals_reviewer: "user", review_round: 1, repair_round: 0 }) });
    expect(selectionText(v)).toBe("Off; result review Off; approvals You. Built-in default, no saved override.");
    expect(runText(v)).toBe(
      "In progress. Reviewing the candidate · not verified. This run: result review Auto-fix; approvals You. Review round 2 · repair round 1.",
    );
  });

  it("counts a first review as round 1 and shows a lone round by itself", () => {
    expect(runText(view({ run: runBody({ status: "reviewing", review_round: 0 }) }))).toBe(
      "In progress. Reviewing the candidate · not verified. Review round 1.",
    );
    expect(runText(view({ run: runBody({ status: "repairing", repair_round: 0 }) }))).toBe(
      "In progress. Repairing · not verified. Repair round 1.",
    );
    // A finished run states its outcome; its rounds are history.
    expect(runText(view({ run: runBody({ status: "verified", review_round: 0, repair_round: 0 }) }))).toBe("Finished. Verified.");
  });

  it.each([
    [2, "Finished. Completed · unverified · 2 unresolved issues. This run: result review Review only; approvals You."],
    [1, "Finished. Completed · unverified · 1 unresolved issue. This run: result review Review only; approvals You."],
    [null, "Finished. Completed · unverified · unresolved issues. This run: result review Review only; approvals You."],
  ])("names N unresolved issues when the server counted them (%o)", (count, text) => {
    const run = runBody({ status: "completed_with_issues", result_review_mode: "review_only", approvals_reviewer: "user", unresolved_finding_count: count });
    expect(runText(view({ run }))).toBe(text);
  });

  it("lets the server's user truth win over a count", () => {
    const run = runBody({ status: "completed_with_issues", user_truth: "Completed · repaired answer was not delivered", unresolved_finding_count: 2 });
    expect(runText(view({ run }))).toBe("Finished. Completed · repaired answer was not delivered.");
  });

  it("omits the run clause when the run does not carry its modes", () => {
    expect(runText(view({ run: runBody({ status: "candidate" }) }))).toBe("In progress. Candidate · provisional / not verified.");
  });

  it("shows the server's user truth unchanged on a finished run", () => {
    const v = view({ run: runBody({ status: "paused", terminal_reason: "budget_exhausted", user_truth: "Paused · Budget exhausted" }) });
    expect(runText(v)).toBe("Finished. Paused · Budget exhausted.");
  });

  it.each([
    [{ status: "verified" }, "Finished. Verified."],
    [{ status: "failed", terminal_reason: "safety_boundary" }, "Finished. Failed · Safety boundary."],
    [{ status: "failed", terminal_reason: "policy_requires_explicit_setup" }, "Finished. Blocked · Policy requires explicit setup."],
    [{ status: "review_unavailable" }, "Finished. Unavailable · not verified."],
    [{ status: "blocked_by_guardian" }, "Finished. Blocked · Guardian."],
    [{ status: "unverified_import", terminal_reason: "quarantined_import" }, "Finished. Unverified · imported history."],
    [{ status: "cancelled" }, "Finished. Cancelled."],
  ])("gives a finished run its frozen sentence (%o)", (fields, text) => {
    expect(runText(view({ run: runBody({ legacy: true, ...fields }) }))).toBe(text);
  });

  it("lists ceilings with no usage when there is no run or no budget projection", () => {
    for (const v of [view(), view({ run: runBody({ legacy: true, budget_usage: {} }) })]) {
      const model = budgetModel(v);
      expect(model.noUsage).toBe(true);
      expect(model.rows).toHaveLength(13);
      expect(model.rows.every((row) => row.meter === null && row.flag === null)).toBe(true);
      expect(model.rows.find((row) => row.field === "wall_time_s")!.ceiling).toBe("900 s");
      expect(model.rows.find((row) => row.field === "extra_token_multiplier")!.ceiling).toBe("1.5×");
      expect(model.summary).toBe("Deployment ceilings · No usage recorded");
      expect(model.warn).toBe(false);
    }
  });

  it("reads meters as used of limit, with the near and at ceiling marks", () => {
    const model = budgetModel(
      view({
        run: runBody({
          budget_usage: usage({
            max_extra_cells: meter(30, 25),
            max_repair_rounds: meter(2, 2),
            max_review_rounds: meter(2, 0, { reserved: 1 }),
          }),
        }),
      }),
    );
    const row = (field: string) => model.rows.find((item) => item.field === field)!;
    expect(row("max_extra_cells")).toMatchObject({ meter: "25 of 30, 5 remaining · Near ceiling", flag: "near", authority: "Auto Run" });
    expect(row("max_repair_rounds")).toMatchObject({ meter: "2 of 2, 0 remaining · At ceiling", flag: "at" });
    expect(row("max_review_rounds")).toMatchObject({ meter: "0 of 2, 1 remaining · 1 reserved", flag: null });
    expect(row("extra_token_multiplier")).toMatchObject({ meter: "Token ceiling not frozen", flag: "token" });
    expect(row("guardian_window_size").authority).toBe("Guardian");
    expect(model.noUsage).toBe(false);
    expect(model.summary).toBe("Deployment ceilings · 1 at ceiling · 1 near ceiling");
    expect(model.exhausted).toBeNull();
    expect(model.warn).toBe(true);
  });

  it("names the exhausted meters when the run stopped on its budget", () => {
    const model = budgetModel(
      view({
        run: runBody({
          status: "paused",
          terminal_reason: "budget_exhausted",
          user_truth: "Paused · Budget exhausted",
          budget_usage: usage({ max_extra_cells: meter(30, 30) }),
        }),
      }),
    );
    expect(model.exhausted).toBe("Exhausted: Additional Cells.");
  });

  it("shows a tripped circuit by its server string, in either language", () => {
    const model = budgetModel(
      view({ run: runBody({ circuit: { state: "tripped", reason: "budget_measurement_unavailable" } }) }),
    );
    expect(model.circuit).toBe("Circuit tripped · 无法验证 token 预算");
    expect(model.warn).toBe(true);
  });

  it("has no line that reads On, Enabled or 已开启 in any state it can show", () => {
    const states = [
      view(WORKED),
      view(),
      view({ selection: selectionBody({ preset: "autonomous", result_review_mode: "auto_fix", approvals_reviewer: "auto_review", source: "frame", explicit: true }) }),
      view({ run: runBody({ status: "repairing" }) }),
    ];
    for (const v of states) for (const text of lines(v)) expect(text).not.toMatch(BANNED);
  });
});

describe("in Chinese", () => {
  beforeAll(async () => {
    await setLang("zh");
  });

  it("reads the worked example the same way", () => {
    const v = view(WORKED);
    expect(availabilityText(v)).toBe("存储未开启");
    expect(selectionText(v)).toBe("自主；结果审核 自动修复；审批 自动复核询问。由部署配置指定。");
    expect(selectionDetail(v)).toBe("部署配置指定的字段：预设。");
    expect(runText(v)).toBe("没有自动运行。");
    for (const text of lines(v)) expect(text).not.toMatch(BANNED);
  });

  it("uses the Chinese copy for each line", () => {
    expect(availabilityText(view({ writable: false, disabled_reason: "import_quarantine" }))).toBe("导入会话，只读");
    expect(availabilityText(view())).toBe("存储可用");
    expect(selectionText(view({ selection: selectionBody({ source: "legacy_result_review", explicit: true }) }))).toBe(
      "关闭；结果审核 关闭；审批 由你。继承自自动审核。",
    );
    expect(runText(view({ run: runBody({ status: "running", approvals_reviewer: "auto_review" }) }))).toBe(
      "进行中。运行中 · 未验证。本次运行：审批 自动复核询问。",
    );
    expect(runText(view({ run: runBody({ status: "paused", user_truth: "Paused · Budget exhausted" }) }))).toBe(
      "已结束。Paused · Budget exhausted。",
    );
    expect(runText(view({ run: runBody({ status: "reviewing", review_round: 1, repair_round: 0 }) }))).toBe(
      "进行中。正在审核候选 · 未验证。审核第 2 轮 · 修复第 1 轮。",
    );
    const issues = runBody({ status: "completed_with_issues", result_review_mode: "review_only", approvals_reviewer: "user", unresolved_finding_count: 2 });
    expect(runText(view({ run: issues }))).toBe("已结束。已完成 · 未验证 · 2 个未解决的问题。本次运行：结果审核 仅审核；审批 由你。");
  });

  it("reads the budget block in Chinese", () => {
    const model = budgetModel(view({ run: runBody({ budget_usage: usage({ max_extra_cells: meter(30, 25) }) }) }));
    expect(model.rows.find((row) => row.field === "max_extra_cells")!.meter).toBe("已用 25/30，剩余 5 · 接近上限");
    expect(budgetModel(view()).summary).toBe("部署上限 · 尚未记录用量");
    expect(budgetModel(view({ run: runBody({ circuit: { state: "tripped", reason: "budget_measurement_unavailable" } }) })).circuit).toBe(
      "熔断已触发 · 无法验证 token 预算",
    );
  });
});

describe("the near-ceiling display rule", () => {
  it.each([
    ["max_extra_cells", { limit: 30, used: 23, reserved: 0, remaining: 7, exhausted: false }, null],
    ["max_extra_cells", { limit: 30, used: 24, reserved: 0, remaining: 6, exhausted: false }, "near"],
    ["max_extra_cells", { limit: 25, used: 20, reserved: 0, remaining: 5, exhausted: false }, "near"],
    ["max_extra_cells", { limit: 0, used: 0, reserved: 0, remaining: 0, exhausted: false }, null],
    ["max_extra_cells", { limit: 2, used: 2, reserved: 0, remaining: 0, exhausted: true }, "at"],
    ["extra_token_multiplier", { limit: 0, used: 0, reserved: 0, remaining: 0, exhausted: false }, "token"],
    ["extra_token_multiplier", { limit: 1000, used: 900, reserved: 0, remaining: 100, exhausted: false }, "near"],
  ] as const)("%s %o → %s", (field, m, flag) => {
    expect(meterFlag(field, { ...m, authority: "auto_budget" })).toBe(flag);
  });
});

describe("the copy table", () => {
  it("defines every key in both languages", () => {
    const { en, zh } = autoModeCopyKeys();
    expect(zh).toEqual(en);
  });
});
