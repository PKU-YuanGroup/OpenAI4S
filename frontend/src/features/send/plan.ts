/**
 * Structured plan card + live progress. Port of app.js:5876-6041.
 *
 * `PLAN_SETTLED_STEP_STATUSES` matches `PlanService._SETTLED_STEP_STATUSES`.
 * `skipped` is in the settled set. Dispatch takes the turn ticket BEFORE the
 * POST so a terminal event that beats the 202 cannot be revived by the await.
 */

import { t } from "../../i18n/runtime";
import { turnDone } from "./turn";
import { _openGen, currentId } from "../../stores/session";
import { defaultModelName } from "../../stores/customize";
import {
  planMode,
  planPending,
  planReady,
  planStatus,
  running,
} from "../../stores/stream";
import { $, el } from "../messages/dom";
import { down } from "../messages/scroll";
import { api, apiErrorText } from "../sessions/api";
import { hint } from "../sessions/chrome";
import { enableComposer } from "../sessions/dom";
import { callLane, setCancelHidden } from "./host";
import { icon, iconEl } from "./icon";
import {
  commitTurnTicket,
  openTurnTicket,
  ownsTurnTicket,
  resumeWatch,
} from "./ticket";

export function planConfLevel(c: unknown): "high" | "medium" | "low" {
  if (c == null || c === "") return "medium";
  const s = String(c).toLowerCase();
  const n = parseFloat(s);
  if (s.includes("high") || s.includes("高") || (!isNaN(n) && n >= 0.75)) return "high";
  if (s.includes("low") || s.includes("低") || (!isNaN(n) && n > 0 && n < 0.4)) return "low";
  return "medium";
}

export const PLAN_SETTLED_STEP_STATUSES = ["completed", "failed", "skipped"];

export function planStepSettled(status: unknown): boolean {
  return PLAN_SETTLED_STEP_STATUSES.includes(String(status));
}

export function planStepIcon(status: unknown): string {
  if (status === "completed") return "check";
  if (status === "in_progress") return "circle-dot";
  if (status === "failed") return "x";
  if (status === "skipped") return "circle";
  return "circle";
}

type PlanStep = {
  id?: string;
  title?: string;
  content?: string;
  detail?: string;
  status?: string;
  deliverables?: unknown[];
};

type Plan = {
  title?: string;
  confidence?: unknown;
  status?: string;
  steps?: PlanStep[];
  [key: string]: unknown;
};

function asPlan(value: unknown): Plan {
  return value && typeof value === "object" ? (value as Plan) : {};
}

export function renderPlanCard(plan: unknown, status?: string | null): void {
  const p = asPlan(plan);
  if (!plan || !currentId.value) return;
  const st = status || p.status || "draft";
  planReady.value = p;
  planStatus.value = st;
  const old = $("#plan-card-live");
  if (old) old.remove();
  if (typeof document !== "undefined") {
    document.querySelectorAll(".plan-card:not(.rich)").forEach((n) => n.remove());
  }
  if (st === "discarded") {
    planReady.value = null;
    return;
  }
  const card = el("div", "plan-card rich");
  card.id = "plan-card-live";
  const head = el("div", "pc-head");
  const tt = el("div", "pc-title-wrap");
  const eyebrow =
    st === "draft"
      ? t("plan.eyebrow.draft")
      : st === "executing"
        ? t("plan.eyebrow.executing")
        : st === "completed"
          ? t("plan.eyebrow.completed")
          : st === "failed"
            ? t("plan.eyebrow.failed")
            : st === "paused"
              ? t("plan.eyebrow.paused")
              : t("plan.eyebrow.default");
  tt.appendChild(el("div", "pc-eyebrow", eyebrow));
  tt.appendChild(el("div", "pc-title", p.title || t("plan.title.default")));
  head.appendChild(tt);
  if (p.confidence) {
    const lvl = planConfLevel(p.confidence);
    const badge = el("span", "pc-conf " + lvl);
    badge.appendChild(
      el(
        "span",
        null,
        t(
          "plan.confidenceSuffix",
          typeof p.confidence === "string" && isNaN(parseFloat(p.confidence))
            ? p.confidence
            : lvl,
        ),
      ),
    );
    head.appendChild(badge);
  }
  card.appendChild(head);
  const steps = el("div", "pc-steps");
  (p.steps || []).forEach((s, i) => {
    const sid = s.id || "s" + (i + 1);
    const row = el("div", "pc-step " + (s.status || "pending"));
    row.dataset.stepId = sid;
    const chk = el("span", "pc-check");
    chk.innerHTML = icon(planStepIcon(s.status), 15);
    row.appendChild(chk);
    const body = el("div", "pc-step-body");
    body.appendChild(
      el("div", "pc-step-t", i + 1 + ". " + (s.title || s.content || t("plan.step.default"))),
    );
    if (s.detail) body.appendChild(el("div", "pc-step-d", s.detail));
    if ((s.deliverables || []).length) {
      const chips = el("div", "pc-chips");
      (s.deliverables || []).forEach((d) => {
        const c = el("span", "pc-chip");
        c.appendChild(iconEl("file-text", 11));
        c.appendChild(el("span", null, String(d)));
        chips.appendChild(c);
      });
      body.appendChild(chips);
    }
    row.appendChild(body);
    steps.appendChild(row);
  });
  card.appendChild(steps);
  if (st === "draft") {
    const rev = el("div", "pc-revise");
    const ta = el("textarea", "pc-revise-input") as HTMLTextAreaElement;
    ta.placeholder = t("plan.revise.placeholder");
    ta.rows = 1;
    ta.onkeydown = (e) => {
      if (e.isComposing || e.keyCode === 229) return;
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const v = ta.value.trim();
        if (v) {
          ta.value = "";
          void revisePlan(v);
        }
      }
    };
    rev.appendChild(ta);
    card.appendChild(rev);
    const pa = el("div", "pa");
    const ok = el("button", "approve-btn");
    ok.appendChild(iconEl("check", 15));
    ok.appendChild(el("span", null, t("plan.approve")));
    ok.onclick = () => void approvePlan();
    const no = el("button", "outline-btn small", t("plan.discard"));
    no.onclick = () => void discardPlan();
    pa.appendChild(ok);
    pa.appendChild(no);
    card.appendChild(pa);
  } else {
    const done = (p.steps || []).filter((s) => s.status === "completed").length;
    const total = (p.steps || []).length;
    const stEl = el("div", "pc-status " + st);
    stEl.textContent =
      st === "executing"
        ? t("plan.status.executing", done, total)
        : st === "completed"
          ? t("plan.status.completed", done, total)
          : st === "failed"
            ? t("plan.status.failed", done, total)
            : st === "paused"
              ? t(
                  "plan.status.paused",
                  done,
                  total,
                  (p.steps || []).filter((x) => !planStepSettled(x.status)).length,
                )
              : "";
    card.appendChild(stEl);
    if (st === "paused") {
      const pa = el("div", "pa");
      const go = el("button", "approve-btn");
      go.appendChild(iconEl("check", 15));
      go.appendChild(el("span", null, t("plan.resume")));
      go.onclick = () => void resumePlan();
      const no = el("button", "outline-btn small", t("plan.discard"));
      no.onclick = () => void discardPlan();
      pa.appendChild(go);
      pa.appendChild(no);
      card.appendChild(pa);
    }
  }
  const host = $("#messages");
  if (host) host.appendChild(card);
  down();
}

export function updatePlanProgress(m: Record<string, unknown>): void {
  const ready = planReady.value as Plan | null;
  if (ready) {
    const s = (ready.steps || []).find((x) => x.id === m.step_id);
    if (s) s.status = String(m.status || s.status);
  }
  const card = $("#plan-card-live");
  if (!card) return;
  let row: HTMLElement | null = null;
  card.querySelectorAll(".pc-step").forEach((r) => {
    if ((r as HTMLElement).dataset.stepId === m.step_id) row = r as HTMLElement;
  });
  if (row) {
    (row as HTMLElement).className = "pc-step " + (m.status || "pending");
    const chk = (row as HTMLElement).querySelector(".pc-check");
    if (chk) chk.innerHTML = icon(planStepIcon(m.status), 15);
  }
  const foot = card.querySelector(".pc-status.executing");
  if (foot && ready) {
    const done = (ready.steps || []).filter((s) => s.status === "completed").length;
    const total = (ready.steps || []).length;
    foot.textContent = t("plan.status.executing", done, total);
  }
  down();
}

async function dispatchPlanTurn(
  path: string,
  body: Record<string, unknown>,
  runningHint: string,
  failedKey: string,
): Promise<boolean> {
  if (!currentId.value || running.value) return false;
  const token = openTurnTicket();
  running.value = true;
  enableComposer(false);
  setCancelHidden(false);
  hint(runningHint, false, true);
  try {
    const accepted = (await api(`/frames/${currentId.value}${path}`, {
      method: "POST",
      body: JSON.stringify(body),
    })) as { request_id?: unknown; execution_id?: unknown } | null;
    if (!ownsTurnTicket(token)) return true;
    commitTurnTicket(token, accepted || {});
    resumeWatch(currentId.value, _openGen.value);
    return true;
  } catch (e) {
    hint(t(failedKey, apiErrorText(e)), true);
    if (ownsTurnTicket(token)) turnDone("failed");
    return false;
  }
}

export async function approvePlan(): Promise<void> {
  if (
    await dispatchPlanTurn(
      "/plan/approve",
      { model: defaultModelName.value },
      t("plan.autoExecuting"),
      "plan.approveFailed",
    )
  ) {
    planMode.value = false;
    const pt = $("#plan-toggle");
    if (pt) pt.classList.remove("on");
  }
}

export async function resumePlan(): Promise<void> {
  await dispatchPlanTurn(
    "/plan/resume",
    { model: defaultModelName.value },
    t("plan.resuming"),
    "plan.resumeFailed",
  );
}

export async function discardPlan(): Promise<void> {
  if (!currentId.value) return;
  try {
    await api(`/frames/${currentId.value}/plan/discard`, { method: "POST", body: "{}" });
  } catch {
    /* discard is best-effort */
  }
  const card = $("#plan-card-live");
  if (card) card.remove();
  planReady.value = null;
  planStatus.value = "discarded";
  planPending.value = false;
  hint(t("toast.planDiscarded"));
}

export async function revisePlan(changes: string): Promise<void> {
  await dispatchPlanTurn(
    "/plan/revise",
    { changes, model: defaultModelName.value },
    t("toast.planRevising"),
    "toast.reviseFailed",
  );
}

export function showPlanApproval(): void {
  if ($("#plan-card-live")) return;
  const card = el("div", "plan-card");
  card.appendChild(el("div", null, t("plan.legacy.intro")));
  const pa = el("div", "pa");
  const ok = el("button", "approve-btn");
  ok.appendChild(iconEl("check", 15));
  ok.appendChild(el("span", null, t("plan.approve")));
  ok.onclick = () => {
    card.remove();
    void callLane("send", t("plan.legacy.approvedPrompt"), { execute: true });
  };
  const no = el("button", "outline-btn small", t("common.cancel"));
  no.onclick = () => card.remove();
  pa.appendChild(ok);
  pa.appendChild(no);
  card.appendChild(pa);
  const host = $("#messages");
  if (host) host.appendChild(card);
  down();
}
