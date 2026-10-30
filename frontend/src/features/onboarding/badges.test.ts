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
function textOf(node: unknown): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join(" ");
  return textOf((node as { props?: { children?: unknown } }).props?.children);
}

function descendants(node: unknown, match: (node: Pill) => boolean, out: Pill[] = []): Pill[] {
  if (Array.isArray(node)) {
    node.forEach((child) => descendants(child, match, out));
    return out;
  }
  if (!node || typeof node !== "object") return out;
  const current = node as Pill;
  if (match(current)) out.push(current);
  descendants((current as { props?: { children?: unknown } }).props?.children, match, out);
  return out;
}

describe("M2 observation and next-request rows", () => {
  const RECEIPT: CapabilityReceipt = {
    native_tool_call: "unknown",
    streaming: "unknown",
    stale: false,
    native_completion: false,
    reachable: true,
    observed_at: 1761177600000,
  };
  const FULL: EffectiveCapabilities = {
    tool_calling: { value: true, source: "catalog" },
    streaming: { value: false, source: "probe" },
  };

  it("emits an observation row and a separate next-request row per capability", () => {
    const rows = capabilityBadgeRows(RECEIPT, "", FULL);
    expect(rows.map((row) => `${row.kind}:${row.cap}`)).toEqual([
      "observed:native_tool_call",
      "effective:native_tool_call",
      "observed:streaming",
      "effective:streaming",
    ]);
    const observed = rows[0]!;
    expect(observed.observed).toBe(true);
    expect(capabilityBadgeText(observed)).toContain("native tool call · unknown");
    expect(capabilityBadgeText(observed)).toContain("observed ");
    const effective = rows[1]!;
    expect(effective.observed).toBe(false);
    expect(effective.effective).toEqual({ value: true, source: "catalog" });
    expect(capabilityBadgeText(effective)).toBe("on (catalogue default)");
  });

  it("labels explicit-override and probe provenance, and never calls a catalogue default measured", () => {
    const rows = capabilityBadgeRows(null, "", {
      tool_calling: { value: true, source: "probe" },
      streaming: { value: false, source: "explicit_override" },
    });
    expect(rows).toHaveLength(2);
    expect(rows.every((row) => row.kind === "effective")).toBe(true);
    expect(capabilityBadgeText(rows[0]!)).toBe("on (measured)");
    expect(capabilityBadgeText(rows[1]!)).toBe("off (explicit config)");
  });

  it("keeps the unknown reason on its observation row and never reports 'unsupported'", () => {
    const reason = "timed out contacting the endpoint";
    const rows = capabilityBadgeRows(RECEIPT, reason, FULL);
    const observed = rows.find(
      (row) => row.kind === "observed" && row.cap === "native_tool_call",
    )!;
    expect(capabilityBadgeText(observed)).toContain(reason);
    expect(capabilityBadgeText(observed).toLowerCase()).not.toMatch(
      /does not support|unsupported/,
    );
    // The next-request row does not borrow the observation's reason.
    const effective = rows.find(
      (row) => row.kind === "effective" && row.cap === "native_tool_call",
    )!;
    expect(capabilityBadgeText(effective)).not.toContain(reason);
  });

  it("renders two labelled lines when the projection is present", () => {
    const tree = CapabilityBadges({ receipt: RECEIPT, effective: FULL }) as unknown;
    const text = textOf(tree);
    expect(text).toContain("Last test observation");
    expect(text).toContain("Next request uses");
    expect(text).toContain("on (catalogue default)");
    expect(text).toContain("off (measured)");
    const effectivePills = descendants(
      tree,
      (node) =>
        node.props?.["data-kind"] === "effective" &&
        "data-source" in (node.props || {}),
    );
    expect(effectivePills.map((pill) => pill.props["data-source"])).toEqual([
      "catalog",
      "probe",
    ]);
    const observedPills = descendants(
      tree,
      (node) =>
        node.props?.["data-kind"] === "observed" &&
        "data-state" in (node.props || {}),
    );
    expect(observedPills.map((pill) => pill.props["data-state"])).toEqual([
      "unknown",
      "unknown",
    ]);
  });

  it("keeps the legacy single-line display when the service sends no projection", () => {
    const rows = capabilityBadgeRows(RECEIPT, "");
    expect(rows.map((row) => row.kind)).toEqual(["observed", "observed"]);
    expect(rows.every((row) => row.effective == null)).toBe(true);
    const tree = CapabilityBadges({ receipt: RECEIPT }) as unknown;
    expect(textOf(tree)).not.toContain("Next request uses");
    expect(descendants(tree, (node) => "data-source" in (node.props || {}))).toHaveLength(0);
  });
});
