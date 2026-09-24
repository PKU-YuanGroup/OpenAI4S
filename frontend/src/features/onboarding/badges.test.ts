import { describe, expect, it } from "vitest";
import { CapabilityBadges } from "../../components/onboarding/CapabilityBadges";
import {
  badgesFromProbe,
  capabilityBadgeRows,
  capabilityBadgeText,
} from "./badges";
import type { CapabilityReceipt } from "../customize/models";

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
