/**
 * The terminal plan card. It read "PLAN COMPLETE" and "Plan execution complete
 * (1/2)" next to a step still drawn with the pulsing in-progress glyph: the row
 * said status completed with s2 in_progress, and the card rendered both halves
 * as stored. The server no longer writes that pair, but rows written before it
 * did still carry it, so the card must not pair a completion claim with live
 * work that nothing is running.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

type FakeNode = {
  tag: string;
  className: string;
  textContent: string;
  title: string;
  innerHTML: string;
  dataset: Record<string, string>;
  children: FakeNode[];
  appendChild: (child: FakeNode) => FakeNode;
  remove: () => void;
  firstChild: FakeNode | null;
};

const fake = vi.hoisted(() => {
  const make = (tag: string, className?: string | null, text?: string | null) => {
    const node: Record<string, unknown> = {
      tag,
      className: className || "",
      textContent: text != null ? text : "",
      title: "",
      innerHTML: "",
      dataset: {},
      children: [] as unknown[],
      firstChild: null,
      remove() {},
      querySelectorAll: () => [],
    };
    node.appendChild = (child: unknown) => {
      (node.children as unknown[]).push(child);
      return child;
    };
    return node;
  };
  return { make, host: make("div", "messages") };
});

vi.mock("../messages/dom", () => ({
  el: fake.make,
  $: (selector: string) => (selector === "#messages" ? fake.host : null),
  messagesHost: () => fake.host,
}));
vi.mock("../messages/scroll", () => ({ down: () => {} }));

import { t } from "../../i18n/runtime";
import { currentId } from "../../stores/session";
import { planPending, planReady } from "../../stores/stream";
import { renderPlanCard } from "./plan";
import { turnDone } from "./turn";

function walk(node: FakeNode, found: FakeNode[] = []): FakeNode[] {
  found.push(node);
  for (const child of node.children || []) walk(child, found);
  return found;
}

function lastCard(): FakeNode {
  const host = fake.host as unknown as FakeNode;
  const card = host.children[host.children.length - 1];
  if (!card) throw new Error("renderPlanCard appended no card");
  return card;
}

function byClass(card: FakeNode, cls: string): FakeNode[] {
  return walk(card).filter((node) => node.className.split(" ").includes(cls));
}

function one(card: FakeNode, cls: string): FakeNode {
  const [node] = byClass(card, cls);
  if (!node) throw new Error(`no .${cls} on the card`);
  return node;
}

function plan(statuses: string[]) {
  return {
    title: "Mean and standard deviation",
    steps: statuses.map((status, index) => ({
      id: `s${index + 1}`,
      title: `step ${index + 1}`,
      status,
    })),
  };
}

describe("terminal plan card", () => {
  beforeEach(() => {
    (fake.host as unknown as FakeNode).children = [];
    currentId.value = "f-plan";
  });

  it("does not show a completed plan with a step in progress as complete", () => {
    renderPlanCard(plan(["completed", "in_progress"]), "completed");
    const card = lastCard();

    const eyebrow = one(card, "pc-eyebrow");
    expect(eyebrow.textContent).not.toBe(t("plan.eyebrow.completed"));
    const status = one(card, "pc-status");
    expect(status.textContent).not.toBe(t("plan.status.completed", 1, 2));
    expect(status.className).not.toContain("completed");
    expect(status.textContent).toContain("1/2");

    const rows = byClass(card, "pc-step");
    expect(rows.map((row) => row.className)).toEqual([
      "pc-step completed",
      "pc-step unconfirmed",
    ]);
    expect(rows[1]?.title).not.toBe("");
  });

  it("keeps the live glyph while the plan is executing", () => {
    renderPlanCard(plan(["completed", "in_progress"]), "executing");
    const rows = byClass(lastCard(), "pc-step");
    expect(rows.map((row) => row.className)).toEqual([
      "pc-step completed",
      "pc-step in_progress",
    ]);
  });

  it("still reads complete when every step settled", () => {
    renderPlanCard(plan(["completed", "completed"]), "completed");
    const card = lastCard();
    expect(one(card, "pc-eyebrow").textContent).toBe(t("plan.eyebrow.completed"));
    expect(one(card, "pc-status").textContent).toBe(t("plan.status.completed", 2, 2));
  });
});

describe("the approval card after a plan-mode turn without a structured plan", () => {
  function approvalCards(): FakeNode[] {
    return (fake.host as unknown as FakeNode).children.filter((node) => node.className === "plan-card");
  }

  beforeEach(() => {
    (fake.host as unknown as FakeNode).children = [];
    // No session: turnDone's artifact and execution-log reloads stay idle.
    currentId.value = null;
    planReady.value = null;
  });

  it.each(["cancelled", "blocked_by_guardian", "failed"])(
    "a %s plan turn offers nothing to approve and leaves nothing for the next turn",
    (status) => {
      planPending.value = true;
      turnDone(status);
      expect(approvalCards()).toHaveLength(0);
      expect(planPending.value).toBe(false);
      // The next, ordinary turn finishes.
      turnDone("completed");
      expect(approvalCards()).toHaveLength(0);
    },
  );

  it("a plan turn that finished offers it", () => {
    planPending.value = true;
    turnDone("completed");
    expect(approvalCards()).toHaveLength(1);
    expect(planPending.value).toBe(false);
  });
});
