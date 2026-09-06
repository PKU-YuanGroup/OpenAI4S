import { describe, expect, it } from "vitest";
import { actionLabelFor, cardsFromItems, parseAttentionItem } from "./parse";
import { mutationRouteForHint } from "./mutations";
import { DOCK_FOR, SOURCE_KINDS, type AttentionSourceKind } from "./types";

const NOW = 1_700_000_000_000;

const KIND_STATE: Record<AttentionSourceKind, string> = {
  running: "running",
  queued: "queued",
  approval: "pending",
  recovery: "failed",
  blocked: "view_only",
  compute: "unknown",
};

const KIND_HINT: Record<AttentionSourceKind, string> = {
  running: "watch",
  queued: "queue:2",
  approval: "approve",
  recovery: "restore",
  blocked: "inspect",
  compute: "inspect",
};

const KIND_SEVERITY: Record<AttentionSourceKind, string> = {
  running: "medium",
  queued: "low",
  approval: "high",
  recovery: "high",
  blocked: "medium",
  compute: "high",
};

function fixture(kind: AttentionSourceKind, extra: Record<string, unknown> = {}) {
  const frameId = `frame-${kind}`;
  return {
    id: `${kind}:src-${kind}`,
    source_kind: kind,
    source_id: `src-${kind}`,
    state: KIND_STATE[kind],
    severity: KIND_SEVERITY[kind],
    frame_id: frameId,
    project_id: "proj-1",
    title: `Session ${kind}`,
    updated_at: NOW - 60_000,
    target: {
      surface: "session",
      dock: DOCK_FOR[kind],
      frame_id: frameId,
    },
    action_hint: KIND_HINT[kind],
    ...extra,
  };
}

const SIX = SOURCE_KINDS.map((kind) => fixture(kind));

describe("M-02 six-kind state → card mapping", () => {
  it("maps each of the six states to exactly one card", () => {
    const cards = cardsFromItems(SIX, {
      projects: [{ project_id: "proj-1", name: "Attention Lab" }],
      now: NOW,
    });
    expect(cards).toHaveLength(6);
    expect(new Set(cards.map((c) => c.sourceKind)).size).toBe(6);
    expect(cards.map((c) => c.sourceKind).sort()).toEqual([...SOURCE_KINDS].sort());
  });

  it("renders the safe summary, project/session, time, and next-action on each card", () => {
    const cards = cardsFromItems(SIX, {
      projects: [{ project_id: "proj-1", name: "Attention Lab" }],
      now: NOW,
    });
    for (const kind of SOURCE_KINDS) {
      const card = cards.find((c) => c.sourceKind === kind);
      expect(card, kind).toBeTruthy();
      if (!card) continue;
      expect(card.title).toBe(`Session ${kind}`);
      expect(card.projectName).toBe("Attention Lab");
      expect(card.frameId).toBe(`frame-${kind}`);
      expect(card.updatedLabel).toBe("1m");
      expect(card.kindLabel.length).toBeGreaterThan(0);
      expect(card.actionLabel.length).toBeGreaterThan(0);
      expect(card.actionHint).toBe(KIND_HINT[kind]);
      expect(card.state).toBe(KIND_STATE[kind]);
      expect(card.navigation.dock).toBe(DOCK_FOR[kind]);
    }
  });

  it("queued hint shows the queue position as the next action", () => {
    expect(actionLabelFor("queue:2")).toMatch(/2/);
    const cards = cardsFromItems([fixture("queued")], { now: NOW });
    expect(cards[0]?.actionLabel).toMatch(/2/);
  });

  it("drops completed/idle rows (kinds outside the six-state set)", () => {
    const idle = {
      ...fixture("running"),
      id: "idle:x",
      source_kind: "idle",
      source_id: "idle-1",
      state: "idle",
    };
    const completed = {
      ...fixture("compute"),
      id: "completed:x",
      source_kind: "completed",
      source_id: "done-1",
      state: "succeeded",
    };
    const cards = cardsFromItems([...SIX, idle, completed], { now: NOW });
    expect(cards).toHaveLength(6);
    expect(cards.every((c) => (SOURCE_KINDS as readonly string[]).includes(c.sourceKind))).toBe(
      true,
    );
  });

  it("drops an item whose dock is outside the closed set", () => {
    const bad = {
      ...fixture("running"),
      target: { surface: "session", dock: "files", frame_id: "frame-running" },
    };
    expect(cardsFromItems([bad])).toHaveLength(0);
  });

  it("keeps one card per source_kind+source_id", () => {
    const dup = fixture("approval", { title: "Duplicate approval" });
    const cards = cardsFromItems([fixture("approval"), dup], { now: NOW });
    expect(cards).toHaveLength(1);
    expect(cards[0]?.title).toBe("Session approval");
  });

  it("does not copy URL fields onto the card model", () => {
    const sneaky = fixture("running", {
      url: "https://evil.example/hijack",
      href: "/static/app.js",
      target: {
        surface: "session",
        dock: "timeline",
        frame_id: "frame-running",
        url: "https://evil.example/session",
      },
    });
    const item = parseAttentionItem(sneaky);
    expect(item).toBeTruthy();
    expect(item && "url" in item).toBe(false);
    const cards = cardsFromItems([sneaky], { now: NOW });
    expect(cards).toHaveLength(1);
    const blob = JSON.stringify(cards[0]);
    expect(blob).not.toMatch(/https?:/i);
    expect(blob).not.toContain("evil.example");
  });
});

describe("M-02 retry/approve/restore stay on existing mutation routes", () => {
  it("names the existing decision and recovery action routes and no others", () => {
    expect(mutationRouteForHint("approve", "f1", "dec-1")).toEqual({
      kind: "approve",
      method: "POST",
      path: "/frames/f1/decision",
    });
    expect(mutationRouteForHint("restore", "f1")).toEqual({
      kind: "restore",
      method: "POST",
      path: "/frames/f1/recovery/actions/restore",
    });
    expect(mutationRouteForHint("retry", "f1")).toEqual({
      kind: "retry",
      method: "POST",
      path: "/frames/f1/recovery/actions/retry",
    });
    expect(mutationRouteForHint("watch", "f1")).toBeNull();
    expect(mutationRouteForHint("inspect", "f1")).toBeNull();
    expect(mutationRouteForHint("queue:2", "f1")).toBeNull();
  });
});
