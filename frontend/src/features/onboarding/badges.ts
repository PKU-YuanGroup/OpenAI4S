/**
 * B-04 capability_receipt → tri-state badge rows.
 * unknown is shown as unknown; the reason is the probe/receipt detail as-is.
 */
import { publicText } from "../scrub/scrub";
import type { CapabilityReceipt, Evidence } from "../customize/models";
import { readCapabilityReceipt } from "../customize/models";

export type BadgeCap = "native_tool_call" | "streaming";

export type BadgeRow = {
  cap: BadgeCap;
  label: string;
  state: Evidence;
  stale: boolean;
  /** Raw unknown reason. Empty when the field is not unknown. Never rewritten. */
  unknownReason: string;
};

const LABELS: Record<BadgeCap, string> = {
  native_tool_call: "native tool call",
  streaming: "streaming",
};

export function capabilityUnknownReason(
  receipt: CapabilityReceipt | null,
  probeDetail = "",
): string {
  const fromReceipt = receipt && receipt.detail ? publicText(receipt.detail, 240) : "";
  const fromProbe = publicText(probeDetail, 240);
  return fromReceipt || fromProbe;
}

export function capabilityBadgeRows(
  receipt: CapabilityReceipt | null,
  unknownReason = "",
): BadgeRow[] {
  if (!receipt) return [];
  const reason = capabilityUnknownReason(receipt, unknownReason);
  const row = (cap: BadgeCap, state: Evidence): BadgeRow => ({
    cap,
    label: LABELS[cap],
    state,
    stale: receipt.stale,
    unknownReason: state === "unknown" ? reason : "",
  });
  return [
    row("native_tool_call", receipt.native_tool_call),
    row("streaming", receipt.streaming),
  ];
}

export function capabilityBadgeText(row: BadgeRow): string {
  let text = `${row.label} · ${row.state}`;
  if (row.unknownReason) text += ` — ${row.unknownReason}`;
  if (row.stale) text += " · stale";
  return text;
}

export function capabilityBadgeMarkup(row: BadgeRow): string {
  const stale = row.stale ? "true" : "false";
  return (
    `<span class="pill prof-cap" data-cap="${row.cap}" data-state="${row.state}"` +
    ` data-stale="${stale}">${capabilityBadgeText(row)}</span>`
  );
}

export function badgesFromProbe(
  receiptRaw: unknown,
  probeDetail = "",
): BadgeRow[] {
  return capabilityBadgeRows(readCapabilityReceipt(receiptRaw), probeDetail);
}

export type { CapabilityReceipt, Evidence };
export { readCapabilityReceipt };
