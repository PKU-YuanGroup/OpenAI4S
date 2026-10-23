/**
 * B-04 capability_receipt → tri-state observation rows, plus M2's separate
 * next-request rows (appendix B.3: exactly `tool_calling` and `streaming`).
 * unknown is shown as unknown; the reason is the probe/receipt detail as-is.
 *
 * Each capability can produce two rows: "last test observation" (the receipt's
 * tri-state, stale flag, observation time and raw reason) and "next request
 * uses" (the effective boolean plus `catalog | explicit_override | probe`
 * provenance). A catalogue default is therefore never shown as a measured
 * pass, `unknown` is never "unsupported", and an old service without the
 * projection keeps exactly the observation-only display.
 *
 * Copy: observation vocabulary lives in `ot`; next-request vocabulary lives in
 * `modelT` (PLAN M2 item 4).
 */
import type {
  CapabilityReceipt,
  EffectiveCapabilities,
  EffectiveCapability,
  EffectiveCapabilityField,
  Evidence,
} from "../customize/models";
import {
  modelT,
  readCapabilityReceipt,
  readEffectiveCapabilities,
} from "../customize/models";
import { publicText } from "../scrub/scrub";
import { ot } from "./copy";

/** One row per capability; `native_tool_call` is the receipt-era name of `tool_calling`. */
export type BadgeCap = "native_tool_call" | "streaming";
export type BadgeKind = "observed" | "effective";

export type BadgeRow = {
  cap: BadgeCap;
  /** The effective-capability field (B.3 contract) this row explains. */
  field: EffectiveCapabilityField;
  kind: BadgeKind;
  label: string;
  /** Last observation; meaningful only on an "observed" row. */
  state: Evidence;
  stale: boolean;
  /** Raw unknown reason. Empty when the field is not unknown. Never rewritten. */
  unknownReason: string;
  /** Whether a probe receipt observed this capability at all. */
  observed: boolean;
  /** Probe observation start time, ms since epoch; absent when never observed. */
  observedAt?: number;
  /** Next-request decision; present only on an "effective" row. */
  effective?: EffectiveCapability | null;
};

type ObservationKey = "native_tool_call" | "streaming";

const CAPS: readonly {
  cap: BadgeCap;
  field: EffectiveCapabilityField;
  observation: ObservationKey;
}[] = [
  { cap: "native_tool_call", field: "tool_calling", observation: "native_tool_call" },
  { cap: "streaming", field: "streaming", observation: "streaming" },
];

function capLabel(cap: BadgeCap): string {
  return ot(
    cap === "native_tool_call"
      ? "onboarding.badge.nativeToolCall"
      : "onboarding.badge.streaming",
  );
}

export function capabilityUnknownReason(
  receipt: CapabilityReceipt | null,
  probeDetail = "",
): string {
  const fromReceipt = receipt && receipt.detail ? publicText(receipt.detail, 240) : "";
  const fromProbe = publicText(probeDetail, 240);
  return fromReceipt || fromProbe;
}

/** Absolute local time of the probe; the observation is a fact, not "just now". */
export function capabilityObservedTime(ms: number): string {
  const date = new Date(ms);
  const pad = (value: number) => String(value).padStart(2, "0");
  return (
    date.getFullYear() +
    "-" +
    pad(date.getMonth() + 1) +
    "-" +
    pad(date.getDate()) +
    " " +
    pad(date.getHours()) +
    ":" +
    pad(date.getMinutes())
  );
}

export function capabilityBadgeRows(
  receipt: CapabilityReceipt | null,
  unknownReason = "",
  effective: EffectiveCapabilities | null = null,
): BadgeRow[] {
  if (!receipt && !effective) return [];
  const reason = receipt ? capabilityUnknownReason(receipt, unknownReason) : "";
  const rows: BadgeRow[] = [];
  for (const spec of CAPS) {
    if (receipt) {
      const observation = receipt[spec.observation];
      rows.push({
        cap: spec.cap,
        field: spec.field,
        kind: "observed",
        label: capLabel(spec.cap),
        state: observation,
        stale: receipt.stale,
        unknownReason: observation === "unknown" ? reason : "",
        observed: true,
        observedAt: receipt.observed_at,
      });
    }
    const next = effective?.[spec.field] ?? null;
    if (next) {
      rows.push({
        cap: spec.cap,
        field: spec.field,
        kind: "effective",
        label: capLabel(spec.cap),
        state: "unknown",
        stale: false,
        unknownReason: "",
        observed: false,
        effective: next,
      });
    }
  }
  return rows;
}

/** The next-request clause alone; empty when the service sent no projection. */
export function capabilityEffectiveText(row: BadgeRow): string {
  const effective = row.effective;
  if (!effective) return "";
  return modelT(
    "model.effective.value",
    effective.value
      ? modelT("model.effective.on")
      : modelT("model.effective.off"),
    modelT("model.effective.source." + effective.source),
  );
}

export function capabilityBadgeText(row: BadgeRow): string {
  if (row.kind !== "observed") return capabilityEffectiveText(row);
  let text = `${row.label} · ${row.state}`;
  if (row.unknownReason) text += ` — ${row.unknownReason}`;
  if (row.stale) text += ` · ${ot("onboarding.badge.stale")}`;
  if (row.observedAt) {
    text += ` · ${ot(
      "onboarding.badge.observedAt",
      capabilityObservedTime(row.observedAt),
    )}`;
  }
  return text;
}

export function badgesFromProbe(
  receiptRaw: unknown,
  probeDetail = "",
  effectiveRaw: unknown = null,
): BadgeRow[] {
  return capabilityBadgeRows(
    readCapabilityReceipt(receiptRaw),
    probeDetail,
    readEffectiveCapabilities(effectiveRaw),
  );
}

export type {
  CapabilityReceipt,
  EffectiveCapability,
  EffectiveCapabilities,
  Evidence,
};
export { readCapabilityReceipt, readEffectiveCapabilities };
