import {
  capabilityBadgeRows,
  capabilityBadgeText,
  type BadgeRow,
} from "../../features/onboarding/badges";
import type { CapabilityReceipt } from "../../features/customize/models";

export function CapabilityBadges({
  receipt,
  unknownReason = "",
}: {
  receipt: CapabilityReceipt | null;
  unknownReason?: string;
}) {
  const rows: BadgeRow[] = capabilityBadgeRows(receipt, unknownReason);
  if (!rows.length) return null;
  return (
    <div class="ds prof-caps">
      {rows.map((row) => (
        <span
          key={row.cap}
          class="pill prof-cap"
          data-cap={row.cap}
          data-state={row.state}
          data-stale={row.stale ? "true" : "false"}
        >
          {capabilityBadgeText(row)}
        </span>
      ))}
    </div>
  );
}
