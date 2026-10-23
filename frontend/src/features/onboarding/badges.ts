/**
 * B-04 capability_receipt → tri-state badge rows.
 * unknown is shown as unknown; the reason is the probe/receipt detail as-is.
 *
 * M2 adds a second, separate statement per capability when the server sends
 * its additive `effective_capabilities` projection: the *last observation*
 * (probe receipt, tri-state, stale flag, time) and the *next request's*
 * decision (boolean + `catalog | explicit_override | probe` provenance). A
 * catalogue default is therefore never shown as a measured pass, and an old
 * service without the field keeps exactly the previous display.
 */
import { copyLookup, type CopyTable } from "../../i18n/copy";
import { publicText } from "../scrub/scrub";
import type {
  CapabilityReceipt,
  EffectiveCapabilities,
  EffectiveCapability,
  Evidence,
} from "../customize/models";
import { readCapabilityReceipt, readEffectiveCapabilities } from "../customize/models";

export type BadgeCap = "native_tool_call" | "streaming";

export type BadgeRow = {
  cap: BadgeCap;
  label: string;
  state: Evidence;
  stale: boolean;
  /** Raw unknown reason. Empty when the field is not unknown. Never rewritten. */
  unknownReason: string;
  /** Whether a probe receipt exists at all; false means nothing was measured. */
  observed: boolean;
  /** Probe observation time, ms since epoch; absent when never observed. */
  observedAt?: number;
  /** Next-request decision from the additive projection; null without it. */
  effective?: EffectiveCapability | null;
};

const COPY: CopyTable = {
  en: {
    "badges.stale": "stale",
    "badges.observed": "observed {0}",
    "badges.next": "next request: {0} ({1})",
    "badges.on": "on",
    "badges.off": "off",
    "badges.source.catalog": "catalogue default",
    "badges.source.explicit_override": "explicit config",
    "badges.source.probe": "tested",
  },
  zh: {
    "badges.stale": "已失效",
    "badges.observed": "观察于 {0}",
    "badges.next": "下次请求：{0}（{1}）",
    "badges.on": "启用",
    "badges.off": "关闭",
    "badges.source.catalog": "目录默认",
    "badges.source.explicit_override": "显式配置",
    "badges.source.probe": "最近测试",
  },
};
const badgesT = copyLookup(COPY);

const LABELS: Record<BadgeCap, string> = {
  native_tool_call: "native tool call",
  streaming: "streaming",
};

const CAPS: readonly BadgeCap[] = ["native_tool_call", "streaming"];

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
  return CAPS.filter((cap) => receipt != null || effective?.[cap]).map((cap) => {
    const state: Evidence = receipt ? receipt[cap] : "unknown";
    return {
      cap,
      label: LABELS[cap],
      state,
      stale: receipt?.stale ?? false,
      unknownReason: state === "unknown" ? reason : "",
      observed: receipt != null,
      observedAt: receipt?.observed_at,
      effective: effective?.[cap] ?? null,
    };
  });
}

/** The next-request clause alone; empty when the service sent no projection. */
export function capabilityEffectiveText(row: BadgeRow): string {
  const effective = row.effective;
  if (!effective) return "";
  return badgesT(
    "badges.next",
    effective.value ? badgesT("badges.on") : badgesT("badges.off"),
    badgesT("badges.source." + effective.source),
  );
}

export function capabilityBadgeText(row: BadgeRow): string {
  let text = `${row.label} · ${row.state}`;
  if (row.unknownReason) text += ` — ${row.unknownReason}`;
  if (row.stale) text += ` · ${badgesT("badges.stale")}`;
  if (row.observedAt) {
    text += ` · ${badgesT("badges.observed", capabilityObservedTime(row.observedAt))}`;
  }
  const effective = capabilityEffectiveText(row);
  if (effective) text += ` · ${effective}`;
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

export type { CapabilityReceipt, EffectiveCapability, EffectiveCapabilities, Evidence };
export { readCapabilityReceipt, readEffectiveCapabilities };
