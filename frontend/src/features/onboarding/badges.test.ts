import { describe, expect, it } from "vitest";
import {
  badgesFromProbe,
  capabilityBadgeMarkup,
  capabilityBadgeRows,
  capabilityBadgeText,
} from "./badges";
import type { CapabilityReceipt } from "../customize/models";

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
    const native = capabilityBadgeMarkup(rows[0]!);
    const streaming = capabilityBadgeMarkup(rows[1]!);
    expect(native).toContain('data-cap="native_tool_call"');
    expect(native).toContain('data-state="true"');
    expect(native).toContain(" · stale");
    expect(streaming).toContain('data-cap="streaming"');
    expect(streaming).toContain('data-state="false"');
    expect(streaming).toContain('data-stale="true"');
  });

  it("shows unknown as unknown and keeps the raw reason (no beautify)", () => {
    const reason = "timed out contacting the endpoint";
    const rows = capabilityBadgeRows(UNKNOWN, reason);
    expect(rows[0]!.state).toBe("unknown");
    expect(rows[1]!.state).toBe("unknown");
    const native = capabilityBadgeText(rows[0]!);
    expect(native).toBe("native tool call · unknown — timed out contacting the endpoint");
    expect(native.toLowerCase()).not.toMatch(/does not support|unsupported|cannot stream/);
    expect(capabilityBadgeMarkup(rows[0]!)).toContain('data-state="unknown"');
    expect(capabilityBadgeMarkup(rows[0]!)).not.toContain('data-state="false"');
    expect(capabilityBadgeMarkup(rows[0]!)).not.toContain('data-state="true"');
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
