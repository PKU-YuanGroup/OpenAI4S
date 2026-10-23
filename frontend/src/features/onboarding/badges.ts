/**
 * B-04 capability_receipt → tri-state badge rows, plus M2's next-request
 * projection.
 * unknown is shown as unknown; the reason is the probe/receipt detail as-is.
 *
 * M2 adds a second, separate statement per capability when the server sends
 * its additive `effective_capabilities` projection (M1
 * `EFFECTIVE_BOOLEAN_FIELDS`, seven fields): the *last observation* (probe
 * receipt, tri-state, stale flag, observation time) and the *next request's*
 * decision (boolean + `catalog | explicit_override | probe` provenance). The
 * receipt can only observe `tool_calling` (stored as `native_tool_call`) and
 * `streaming`; the other five fields get a next-request statement only — no
 * observation is invented for them. A catalogue default is therefore never
 * shown as a measured pass, and an old service without the field keeps
 * exactly the previous display.
 */
import { copyLookup, type CopyTable } from "../../i18n/copy";
import { publicText } from "../scrub/scrub";
import type {
  CapabilityReceipt,
  EffectiveCapabilities,
  EffectiveCapability,
  EffectiveCapabilityField,
  Evidence,
} from "../customize/models";
import {
  EFFECTIVE_CAPABILITY_FIELDS,
  readCapabilityReceipt,
  readEffectiveCapabilities,
} from "../customize/models";

/** One row per capability; `native_tool_call` is the receipt-era name of `tool_calling`. */
export type BadgeCap =
  | "native_tool_call"
  | "parallel_tool_calls"
  | "strict_tool_schema"
  | "vision"
  | "audio"
  | "reasoning"
  | "streaming";

export type BadgeRow = {
  cap: BadgeCap;
  /** The effective-capability field (M1 contract) this row explains. */
  field: EffectiveCapabilityField;
  label: string;
  /** Last observation; meaningful only when `observed` is true. */
  state: Evidence;
  stale: boolean;
  /** Raw unknown reason. Empty when the field is not unknown. Never rewritten. */
  unknownReason: string;
  /** Whether a probe receipt observed this capability at all. */
  observed: boolean;
  /** Probe observation start time, ms since epoch; absent when never observed. */
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
    "badges.label.nativeToolCall": "native tool call",
    "badges.label.parallelToolCalls": "parallel tool calls",
    "badges.label.strictToolSchema": "strict tool schema",
    "badges.label.vision": "vision",
    "badges.label.audio": "audio",
    "badges.label.reasoning": "reasoning",
    "badges.label.streaming": "streaming",
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
    "badges.label.nativeToolCall": "原生工具调用",
    "badges.label.parallelToolCalls": "并行工具调用",
    "badges.label.strictToolSchema": "严格工具参数 schema",
    "badges.label.vision": "视觉",
    "badges.label.audio": "音频",
    "badges.label.reasoning": "推理",
    "badges.label.streaming": "流式输出",
  },
};
const badgesT = copyLookup(COPY);

type RowSpec = {
  field: EffectiveCapabilityField;
  cap: BadgeCap;
  labelKey: keyof (typeof COPY)["en"];
  /** Receipt key that observes this field, when the receipt has one. */
  observation?: "native_tool_call" | "streaming";
};

/** Contract order (`EFFECTIVE_BOOLEAN_FIELDS`). */
const ROW_SPECS: readonly RowSpec[] = [
  {
    field: "tool_calling",
    cap: "native_tool_call",
    labelKey: "badges.label.nativeToolCall",
    observation: "native_tool_call",
  },
  {
    field: "parallel_tool_calls",
    cap: "parallel_tool_calls",
    labelKey: "badges.label.parallelToolCalls",
  },
  {
    field: "strict_tool_schema",
    cap: "strict_tool_schema",
    labelKey: "badges.label.strictToolSchema",
  },
  { field: "vision", cap: "vision", labelKey: "badges.label.vision" },
  { field: "audio", cap: "audio", labelKey: "badges.label.audio" },
  { field: "reasoning", cap: "reasoning", labelKey: "badges.label.reasoning" },
  {
    field: "streaming",
    cap: "streaming",
    labelKey: "badges.label.streaming",
    observation: "streaming",
  },
];

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
  // Contract order over the seven backend fields; a receipt-only field of an
  // old service still renders its observation, and a projection-only field
  // renders its next-request statement without inventing an observation.
  EFFECTIVE_CAPABILITY_FIELDS.forEach((field) => {
    const spec = ROW_SPECS.find((candidate) => candidate.field === field);
    if (!spec) return;
    const observation =
      receipt && spec.observation ? receipt[spec.observation] : null;
    const next = effective?.[field] ?? null;
    if (observation == null && !next) return;
    rows.push({
      cap: spec.cap,
      field,
      label: badgesT(spec.labelKey),
      state: observation ?? "unknown",
      stale: observation != null ? receipt!.stale : false,
      unknownReason: observation === "unknown" ? reason : "",
      observed: observation != null,
      observedAt: observation != null ? receipt!.observed_at : undefined,
      effective: next,
    });
  });
  return rows;
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
  let text = row.label;
  if (row.observed) {
    text += ` · ${row.state}`;
    if (row.unknownReason) text += ` — ${row.unknownReason}`;
    if (row.stale) text += ` · ${badgesT("badges.stale")}`;
    if (row.observedAt) {
      text += ` · ${badgesT("badges.observed", capabilityObservedTime(row.observedAt))}`;
    }
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

export type {
  CapabilityReceipt,
  EffectiveCapability,
  EffectiveCapabilities,
  Evidence,
};
export { readCapabilityReceipt, readEffectiveCapabilities };
