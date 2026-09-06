import { describe, expect, it } from "vitest";
import {
  isAttentionDock,
  isAttentionSurface,
  localSessionPath,
  navigationFromTarget,
  targetHasUrlField,
} from "./navigate";
import { cardsFromItems } from "./parse";
import {
  ATTENTION_PANE,
  DOCK_FOCUS,
  DOCK_FOR,
  DOCKS,
  SOURCE_KINDS,
  SURFACES,
  type AttentionSourceKind,
} from "./types";

const KIND_STATE: Record<AttentionSourceKind, string> = {
  running: "running",
  queued: "queued",
  approval: "pending",
  recovery: "failed",
  blocked: "view_only",
  compute: "unknown",
};

function targetFor(kind: AttentionSourceKind) {
  return {
    surface: "session" as const,
    dock: DOCK_FOR[kind],
    frame_id: `frame-${kind}`,
  };
}

function item(kind: AttentionSourceKind) {
  return {
    id: `${kind}:src-${kind}`,
    source_kind: kind,
    source_id: `src-${kind}`,
    state: KIND_STATE[kind],
    severity: kind === "queued" ? "low" : kind === "approval" || kind === "recovery" || kind === "compute" ? "high" : "medium",
    frame_id: `frame-${kind}`,
    project_id: "proj-1",
    title: kind,
    updated_at: 1_700_000_000_000,
    target: targetFor(kind),
    action_hint: "watch",
  };
}

describe("M-02 target → local navigation closed set", () => {
  it("accepts only surface=session and the four docks", () => {
    expect([...SURFACES]).toEqual(["session"]);
    expect([...DOCKS].sort()).toEqual(["compute", "recovery", "security", "timeline"]);
    expect(isAttentionSurface("session")).toBe(true);
    expect(isAttentionSurface("project")).toBe(false);
    expect(isAttentionDock("files")).toBe(false);
    expect(isAttentionDock("notebook")).toBe(false);
    for (const dock of DOCKS) expect(isAttentionDock(dock)).toBe(true);
  });

  it("maps each of the six kinds onto its exact closed-set dock", () => {
    const cards = cardsFromItems(SOURCE_KINDS.map(item));
    expect(cards).toHaveLength(6);
    for (const card of cards) {
      const expected = DOCK_FOR[card.sourceKind];
      expect(card.navigation.dock).toBe(expected);
      expect(card.navigation.surface).toBe("session");
      expect(card.navigation.frameId).toBe(card.frameId);
      expect(card.navigation.pane).toBe(ATTENTION_PANE);
      expect(card.navigation.focusSelector).toBe(DOCK_FOCUS[expected]);
    }
  });

  it("builds the session path locally from frame_id + project_id", () => {
    const nav = navigationFromTarget(
      { surface: "session", dock: "security", frame_id: "abc" },
      "proj-9",
    );
    expect(nav).toEqual({
      surface: "session",
      dock: "security",
      frameId: "abc",
      projectId: "proj-9",
      pane: ATTENTION_PANE,
      focusSelector: DOCK_FOCUS.security,
    });
    expect(localSessionPath(nav!)).toBe("/projects/proj-9/frames/abc");
  });

  it("rejects docks and surfaces outside the closed set", () => {
    expect(
      navigationFromTarget({ surface: "session", dock: "files", frame_id: "f" }),
    ).toBeNull();
    expect(
      navigationFromTarget({ surface: "session", dock: "notebook", frame_id: "f" }),
    ).toBeNull();
    expect(
      navigationFromTarget({ surface: "workspace", dock: "timeline", frame_id: "f" }),
    ).toBeNull();
    expect(
      navigationFromTarget({ surface: "session", dock: "timeline", frame_id: "" }),
    ).toBeNull();
    expect(navigationFromTarget({ surface: "session", dock: "timeline" })).toBeNull();
    expect(navigationFromTarget(null)).toBeNull();
  });

  it("ignores server URL fields and never copies them into navigation", () => {
    const target = {
      surface: "session",
      dock: "compute",
      frame_id: "job-frame",
      url: "https://evil.example/takeover",
      href: "https://evil.example/takeover",
      uri: "/api/v1/hijack",
      link: "mailto:attacker@example.com",
      path: "/etc/passwd",
    };
    expect(targetHasUrlField(target)).toBe(true);
    const nav = navigationFromTarget(target, "p");
    expect(nav).not.toBeNull();
    expect(nav?.frameId).toBe("job-frame");
    expect(nav?.dock).toBe("compute");
    const blob = JSON.stringify(nav);
    expect(blob).not.toMatch(/https?:/i);
    expect(blob).not.toContain("evil.example");
    expect(blob).not.toContain("mailto:");
    expect(blob).not.toContain("/etc/passwd");
    expect(localSessionPath(nav!)).toBe("/projects/p/frames/job-frame");
    expect(localSessionPath(nav!)).not.toBe(target.url);
  });

  it("hits every closed dock exactly from the six-kind fixture", () => {
    const cards = cardsFromItems(SOURCE_KINDS.map(item));
    const docks = cards.map((c) => c.navigation.dock);
    expect(docks.filter((d) => d === "timeline")).toHaveLength(2);
    expect(docks.filter((d) => d === "recovery")).toHaveLength(2);
    expect(docks.filter((d) => d === "security")).toHaveLength(1);
    expect(docks.filter((d) => d === "compute")).toHaveLength(1);
    expect(docks.every((d) => (DOCKS as readonly string[]).includes(d))).toBe(true);
  });
});
