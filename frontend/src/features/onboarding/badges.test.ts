import { describe, expect, it } from "vitest";
import { CapabilityBadges } from "../../components/onboarding/CapabilityBadges";
import {
  badgesFromProbe,
  capabilityBadgeRows,
  capabilityBadgeText,
} from "./badges";
import type { CapabilityReceipt, EffectiveCapabilities } from "../customize/models";

type Pill = { props: Record<string, string> & { children: string } };

/** The pills `CapabilityBadges` renders for a receipt. */
function pills(receipt: CapabilityReceipt, unknownReason = ""): Pill[] {
  const tree = CapabilityBadges({ receipt, unknownReason }) as { props: { children: Pill[] } } | null;
  return tree ? tree.props.children : [];
}

const UNKNOWN: CapabilityReceipt = {
  native_tool_call: "unknown",
  streaming: "unknown",
  stale: false,
  native_completion: false,
  reachable: false,
};

describe("M-01 capability badges", () => {
  it("renders the three evidence states as data-state true/false/unknown", () => {
    const mixed: CapabilityReceipt = {
      native_tool_call: "true",
      streaming: "false",
      stale: true,
      native_completion: true,
      reachable: true,
    };
    const rows = capabilityBadgeRows(mixed);
    expect(rows.map((row) => row.state)).toEqual(["true", "false"]);
    const [native, streaming] = pills(mixed);
    expect(native!.props["data-cap"]).toBe("native_tool_call");
    expect(native!.props["data-state"]).toBe("true");
    expect(native!.props.children).toContain(" · stale");
    expect(streaming!.props["data-cap"]).toBe("streaming");
    expect(streaming!.props["data-state"]).toBe("false");
    expect(streaming!.props["data-stale"]).toBe("true");
  });

  it("shows unknown as unknown and keeps the raw reason (no beautify)", () => {
    const reason = "timed out contacting the endpoint";
    const rows = capabilityBadgeRows(UNKNOWN, reason);
    expect(rows[0]!.state).toBe("unknown");
    expect(rows[1]!.state).toBe("unknown");
    const native = capabilityBadgeText(rows[0]!);
    expect(native).toBe("native tool call · unknown — timed out contacting the endpoint");
    expect(native.toLowerCase()).not.toMatch(/does not support|unsupported|cannot stream/);
    expect(pills(UNKNOWN, reason)[0]!.props["data-state"]).toBe("unknown");
  });

  it("does not invent a reason when the probe left unknown without detail", () => {
    const rows = capabilityBadgeRows(UNKNOWN, "");
    expect(capabilityBadgeText(rows[0]!)).toBe("native tool call · unknown");
    expect(rows[0]!.unknownReason).toBe("");
  });

  it("reads B-04 capability_receipt and keeps a 5xx/auth reason verbatim", () => {
    const rows = badgesFromProbe(
      {
        native_tool_call: "unknown",
        streaming: "unknown",
        stale: false,
        native_completion: false,
        reachable: false,
      },
      "upstream returned 503",
    );
    expect(capabilityBadgeText(rows[0]!)).toContain("upstream returned 503");
    expect(capabilityBadgeText(rows[0]!).toLowerCase()).not.toContain("temporarily unavailable");
    const auth = badgesFromProbe(
      { native_tool_call: "unknown", streaming: "false" },
      "the provider rejected the credential; check the API key for this profile in Customize -> Models",
    );
    expect(capabilityBadgeText(auth[0]!)).toContain("the provider rejected the credential");
    expect(auth[1]!.state).toBe("false");
    expect(auth[1]!.unknownReason).toBe("");
  });

  it("returns no badges when there is no receipt", () => {
    expect(capabilityBadgeRows(null)).toEqual([]);
    expect(badgesFromProbe(null)).toEqual([]);
  });
});

// M2 / P0-01: one badge states the last probe observation, the other what the
// next request will use. A catalogue default is not a measured pass, so its
// provenance has to be on the text; without the additive projection the old
// display is unchanged.
describe("M2 observation vs next-request badge", () => {
  const RECEIPT: CapabilityReceipt = {
    native_tool_call: "unknown",
    streaming: "unknown",
    stale: false,
    native_completion: false,
    reachable: true,
    observed_at: 1761177600000,
  };
  const CATALOG: EffectiveCapabilities = {
    native_tool_call: { value: true, source: "catalog" },
  };

  it("states the next request separately and never presents a catalogue default as measured", () => {
    const rows = capabilityBadgeRows(RECEIPT, "", CATALOG);
    const native = rows[0]!;
    expect(native.effective).toEqual({ value: true, source: "catalog" });
    expect(native.observedAt).toBe(1761177600000);
    const text = capabilityBadgeText(native);
    expect(text).toContain("next request: on");
    expect(text).toContain("catalogue default");
    expect(text).not.toContain("tested");
    // The observation time is shown, so "unknown" cannot read as "just asked".
    expect(text).toContain("observed ");
  });

  it("labels probe and explicit-override provenance, and never claims a probe it did not do", () => {
    const probe = capabilityBadgeText(
      capabilityBadgeRows(RECEIPT, "", {
        streaming: { value: false, source: "probe" },
      })[1]!,
    );
    expect(probe).toContain("next request: off");
    expect(probe).toContain("tested");
    expect(probe).not.toContain("catalogue");

    const explicit = capabilityBadgeText(
      capabilityBadgeRows(RECEIPT, "", {
        native_tool_call: { value: true, source: "explicit_override" },
      })[0]!,
    );
    expect(explicit).toContain("explicit config");
    expect(explicit).not.toContain("tested");
  });

  it("shows the next-request decision even when nothing was ever probed", () => {
    const rows = capabilityBadgeRows(null, "", CATALOG);
    expect(rows).toHaveLength(1);
    expect(rows[0]!.cap).toBe("native_tool_call");
    expect(rows[0]!.state).toBe("unknown");
    expect(rows[0]!.observed).toBe(false);
    const text = capabilityBadgeText(rows[0]!);
    expect(text).toContain("catalogue default");
    expect(text).not.toContain("tested");
  });

  it("keeps the old display when the service sends no projection", () => {
    const rows = capabilityBadgeRows(RECEIPT, "");
    expect(rows.every((row) => row.effective == null)).toBe(true);
    expect(capabilityBadgeText(rows[0]!)).not.toContain("next request");
    // The observation time still shows: it is a fact about the receipt, not
    // part of the additive next-request projection.
    expect(capabilityBadgeText(rows[0]!)).toContain("observed ");
  });
});
