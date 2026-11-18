/**
 * A refused action is not a failed one.
 *
 * A Host call the user denied, one a Stop cancelled while it waited for
 * approval, and one a rule or policy refused all come back as errors: a
 * step's `output.error` is "Permission denied: <reason>", and a Cell that let
 * it propagate ends with "RuntimeError: bash: Permission denied: <reason>;
 * command was not executed" (or "host.<method> error: Permission denied: …").
 * Painted like a crash -- a red ✗, "Failed", the server's English -- the
 * user's own "no", and their own Stop, read as the system breaking.
 *
 * These helpers recognise that shape (never an OS `PermissionError`, which
 * is a real failure) and say what happened in the UI language.
 */

import { LANG } from "../../i18n/runtime";
import { permCards } from "../../stores/stream";

export type DeclinedKind = "you" | "rule" | "stopped" | "timedOut";

export type Declined = {
  kind: DeclinedKind;
  /** The reason worth showing (a typed reason, a rule's message); "" for none. */
  reason: string;
};

/** The broker's own words for the outcomes that are not a "no". */
const TURN_CANCELLED = "turn cancelled";
const TIMED_OUT = "approval timed out";
const NO_REASON = "denied by user";

const STEP_DENIED = /^Permission denied:\s*([\s\S]*)$/;
/** The last line of a Cell's error, when a Host refusal is what it raised. */
const CELL_DENIED =
  /^(?:RuntimeError:\s*)?(?:bash:\s*|host\.[\w.]+ error:\s*)Permission denied:\s*(.*?)(?:;\s*command was not executed)?\s*$/;

const COPY: Record<"en" | "zh", Record<DeclinedKind | "reason", string>> = {
  en: {
    you: "You declined · not run",
    rule: "Not approved · not run",
    stopped: "Stopped with the turn · not run",
    timedOut: "Approval timed out · not run",
    reason: "Reason: {0}",
  },
  zh: {
    you: "你拒绝了 · 未执行",
    rule: "未获批准 · 未执行",
    stopped: "已随任务停止 · 未执行",
    timedOut: "审批超时 · 未执行",
    reason: "原因：{0}",
  },
};

function copy(key: DeclinedKind | "reason", arg = ""): string {
  return (COPY[LANG === "zh" ? "zh" : "en"] || COPY.en)[key].replace("{0}", arg);
}

/** A reason as both ends spell it: the bash path drops a closing period. */
function normalized(reason: string): string {
  return reason.trim().replace(/[.。]\s*$/, "").trim();
}

/**
 * Whether `reason` is what the user typed when they pressed Deny on a card in
 * this view: a decided card keeps the reason it was denied with.
 */
function typedOnACard(reason: string): boolean {
  const reg = (permCards.value || {}) as Record<
    string,
    { resolved?: boolean; allowed?: boolean; resolution?: unknown } | undefined
  >;
  return Object.keys(reg).some((id) => {
    const handle = reg[id];
    if (!handle || !handle.resolved || handle.allowed) return false;
    const resolution = handle.resolution as { message?: unknown } | null | undefined;
    return (
      !!resolution && typeof resolution.message === "string" && normalized(resolution.message) === reason
    );
  });
}

function classify(raw: string): Declined {
  const reason = normalized(raw);
  if (reason === TURN_CANCELLED) return { kind: "stopped", reason: "" };
  if (reason === TIMED_OUT) return { kind: "timedOut", reason: "" };
  if (!reason || reason === NO_REASON) return { kind: "you", reason: "" };
  return { kind: typedOnACard(reason) ? "you" : "rule", reason };
}

/** A step whose Host call was refused (its `output.error`), or null. */
export function declinedStep(output: unknown): Declined | null {
  const error =
    output && typeof output === "object" ? (output as { error?: unknown }).error : null;
  if (typeof error !== "string") return null;
  const match = STEP_DENIED.exec(error.trim());
  return match ? classify(match[1] || "") : null;
}

/** A Cell that ended on a Host refusal (its error text), or null. */
export function declinedCell(error: unknown): Declined | null {
  if (typeof error !== "string" || !error.trim()) return null;
  const lines = error.trim().split("\n");
  const last = (lines[lines.length - 1] || "").trim();
  const match = CELL_DENIED.exec(last);
  return match ? classify(match[1] || "") : null;
}

/** The one-line status for a refused step or Cell. */
export function declinedMeta(declined: Declined): string {
  return copy(declined.kind);
}

/** "Reason: …" for a refusal that carries one, else "". */
export function declinedReason(declined: Declined): string {
  return declined.reason ? copy("reason", declined.reason) : "";
}
