/**
 * Plan-mode user rows, reopened as what the user wrote.
 *
 * A plan-mode request is stored as the model received it: the workbench's
 * `planModePayload` (the localised `plan.prompt.*` text, or the legacy app.js
 * literal) followed by the task, and a revision as the server's revision seed
 * followed by the change request. Approving or resuming a plan runs a turn
 * whose "user" message is the server's execution seed. Live, none of that is
 * shown -- the bubble is the typed task and approval happens on the plan card
 * -- but a reload rendered every one of those rows verbatim as the user's own
 * message.
 *
 * The stored rows stay as they are (they are what the model was sent); only
 * the rendering changes. Mirrors `openai4s/server/plans.py`
 * `plan_mode_request_text`, which titles the session the same way.
 */

import { LANG } from "../../i18n/runtime";
import { el } from "./dom";

/** `plans.PLAN_MODE_PROMPT_MARKERS`. */
const MARKERS = ["[Plan Mode]", "[计划模式]"];

/** `plans.PLAN_MODE_REQUEST_DELIMITERS`: where the prompt ends and the user's words begin. */
const DELIMITERS = ["\n\nTask: ", "\n\n任务：", "\n\nChange requests: ", "\n\n修改意见："];

/** The first line of `PlanService.execution_seed` / `resume_seed`, en and zh. */
const SEEDS: Array<{ kind: PlanSeedKind; pattern: RegExp }> = [
  { kind: "approved", pattern: /^Plan "([^\n]*)" is approved; start executing it automatically now\./ },
  { kind: "approved", pattern: /^已批准计划「([^\n]*)」，现在开始自动执行。/ },
  { kind: "resumed", pattern: /^Continue executing plan "([^\n]*)" \(the previous run was interrupted\)\./ },
  { kind: "resumed", pattern: /^继续执行计划「([^\n]*)」（上一次执行被中断）。/ },
];

export type PlanSeedKind = "approved" | "resumed";
export type PlanSeed = { kind: PlanSeedKind; title: string };

/** Feature-local copy: en.ts / zh.ts are generated extracts. */
const COPY: Record<"en" | "zh", Record<PlanSeedKind, string>> = {
  en: { approved: "Plan approved: {0}", resumed: "Plan resumed: {0}" },
  zh: { approved: "已批准计划：{0}", resumed: "继续执行计划：{0}" },
};

/**
 * The user's own words inside a plan-mode prompt; `text` otherwise. Cut at
 * the first delimiter, which is the prompt's own. Text with no marker, or no
 * delimiter to separate, is the user's and is returned unchanged.
 */
export function planModeRequestText(text: string): string {
  const value = String(text || "");
  const lead = value.trimStart();
  if (!MARKERS.some((marker) => lead.startsWith(marker))) return value;
  let cut = -1;
  let length = 0;
  for (const delimiter of DELIMITERS) {
    const at = value.indexOf(delimiter);
    if (at >= 0 && (cut < 0 || at < cut)) {
      cut = at;
      length = delimiter.length;
    }
  }
  if (cut < 0) return value;
  return value.slice(cut + length).trim() || value;
}

/** A stored plan execution/resume seed, or null for anything else. */
export function planSeed(text: string): PlanSeed | null {
  const value = String(text || "");
  for (const { kind, pattern } of SEEDS) {
    const match = pattern.exec(value);
    if (match) return { kind, title: String(match[1] || "").slice(0, 200) };
  }
  return null;
}

/** The reopened seed row: one muted line naming the plan, not a user bubble. */
export function planSeedMarker(seed: PlanSeed): HTMLElement {
  const box = el("div", "msg-plan-seed");
  box.dataset.planSeed = seed.kind;
  box.appendChild(el("span", "lbl", (COPY[LANG === "zh" ? "zh" : "en"] || COPY.en)[seed.kind].replace("{0}", seed.title)));
  return box;
}
