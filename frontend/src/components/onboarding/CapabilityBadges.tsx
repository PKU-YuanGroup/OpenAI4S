import {
  capabilityBadgeRows,
  capabilityBadgeText,
  type BadgeRow,
} from "../../features/onboarding/badges";
import type {
  CapabilityReceipt,
  EffectiveCapabilities,
} from "../../features/customize/models";

/**
 * One pill per capability. Its text is the last observation (`state`, reason,
 * stale flag, observation time) and, when the server sends the M2 projection,
 * a separate clause for what the next request will use and why
 * (`catalog | explicit_override | probe`). The data attributes expose both
 * facts independently for tests and for the response contract.
 */
export function CapabilityBadges({
  receipt,
  unknownReason = "",
  effective = null,
}: {
  receipt: CapabilityReceipt | null;
  unknownReason?: string;
  effective?: EffectiveCapabilities | null;
}) {
  const rows: BadgeRow[] = capabilityBadgeRows(receipt, unknownReason, effective);
  if (!rows.length) return null;
  return (
    <div class="ds prof-caps">
      {rows.map((row) => (
        <span
          key={row.cap}
          class="pill prof-cap"
          data-cap={row.cap}
          data-effective-field={row.field}
          data-state={row.observed ? row.state : undefined}
          data-stale={row.stale ? "true" : "false"}
          data-observed={row.observed ? "true" : "false"}
          data-observed-at={row.observedAt ? String(row.observedAt) : undefined}
          data-effective={
            row.effective ? (row.effective.value ? "true" : "false") : undefined
          }
          data-source={row.effective ? row.effective.source : undefined}
        >
          {capabilityBadgeText(row)}
        </span>
      ))}
    </div>
  );
}
