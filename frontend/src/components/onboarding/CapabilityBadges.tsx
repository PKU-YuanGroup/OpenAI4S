import {
  capabilityBadgeRows,
  capabilityBadgeText,
  type BadgeRow,
} from "../../features/onboarding/badges";
import { ot } from "../../features/onboarding/copy";
import {
  modelT,
  type CapabilityReceipt,
  type EffectiveCapabilities,
} from "../../features/customize/models";

/**
 * One pill per capability by default. When the server sends the M2 projection
 * (appendix B.3) each capability renders two labelled lines instead: the last
 * test observation and what the next request will use, with its provenance.
 * The old service (no projection) keeps exactly the previous single-line
 * display. Data attributes expose both facts independently for the tests.
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

  // Legacy display: only observations, one pill per capability.
  if (!rows.some((row) => row.kind === "effective")) {
    return (
      <div class="ds prof-caps">
        {rows.map((row) => (
          <span
            key={row.cap}
            class="pill prof-cap"
            data-cap={row.cap}
            data-kind="observed"
            data-state={row.state}
            data-stale={row.stale ? "true" : "false"}
            data-observed="true"
            data-observed-at={row.observedAt ? String(row.observedAt) : undefined}
          >
            {capabilityBadgeText(row)}
          </span>
        ))}
      </div>
    );
  }

  return (
    <div class="ds prof-caps prof-caps-effective">
      {rows.map((row) =>
        row.kind === "observed" ? (
          <div
            key={`${row.cap}:observed`}
            class="prof-cap-line"
            data-kind="observed"
            data-cap={row.cap}
          >
            <span class="prof-cap-label">{ot("onboarding.badge.observedLabel")}</span>
            <span
              class="pill prof-cap"
              data-cap={row.cap}
              data-kind="observed"
              data-state={row.state}
              data-stale={row.stale ? "true" : "false"}
              data-observed="true"
              data-observed-at={row.observedAt ? String(row.observedAt) : undefined}
            >
              {capabilityBadgeText(row)}
            </span>
          </div>
        ) : (
          <div
            key={`${row.cap}:effective`}
            class="prof-cap-line"
            data-kind="effective"
            data-cap={row.cap}
          >
            <span class="prof-cap-label">{modelT("model.effective.label")}</span>
            <span
              class="pill prof-cap effective"
              data-cap={row.cap}
              data-kind="effective"
              data-effective-field={row.field}
              data-effective={row.effective!.value ? "true" : "false"}
              data-source={row.effective!.source}
            >
              {capabilityBadgeText(row)}
            </span>
          </div>
        ),
      )}
    </div>
  );
}
