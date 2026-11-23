import { beforeEach, describe, expect, it, vi } from "vitest";

// M-5: every step repeated the four-line subtitle; step 1 with one choice
// preselected nothing and kept Continue hidden; there was no Back; the Test
// step's result was "native tool call · true · observed …"; step 3 showed
// the terminal commands and their explanations in full.

const hooks = vi.hoisted(() => {
  const slots: unknown[] = [];
  let cursor = 0;
  const set = (index: number, value: unknown) => {
    slots[index] = typeof value === "function" ? (value as (v: unknown) => unknown)(slots[index]) : value;
  };
  return {
    slots,
    reset() {
      slots.length = 0;
      cursor = 0;
    },
    begin() {
      cursor = 0;
    },
    useState(initial: unknown) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], (value: unknown) => set(index, value)];
    },
    useRef(initial: unknown) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initial };
      return slots[index];
    },
    useReducer(reducer: (state: unknown, action: unknown) => unknown, initial: unknown) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], (action: unknown) => set(index, reducer(slots[index], action))];
    },
  };
});

vi.mock("preact/hooks", () => ({
  useEffect: vi.fn(),
  useReducer: hooks.useReducer,
  useRef: hooks.useRef,
  useState: hooks.useState,
}));

import { EnvironmentCard } from "../../components/onboarding/ReadinessPanel";
import { WizardHost, defaultPath } from "../../components/onboarding/Wizard";
import { ot } from "./copy";
import { INITIAL_WIZARD, type WizardState } from "./machine";
import { sanitizeOnboardingStatus } from "./status";

type VNode = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };

function textOf(node: unknown): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  return textOf((node as VNode).props?.children);
}

function all(node: unknown, match: (node: VNode) => boolean, out: VNode[] = []): VNode[] {
  if (Array.isArray(node)) {
    for (const child of node) all(child, match, out);
    return out;
  }
  if (!node || typeof node !== "object") return out;
  const vnode = node as VNode;
  if (match(vnode)) out.push(vnode);
  return all(vnode.props?.children, match, out);
}
const has = (node: unknown, attr: string) => all(node, (n) => !!n.props && attr in n.props);

function status(overrides: Record<string, unknown> = {}) {
  return sanitizeOnboardingStatus({
    provider: "ark",
    model: "doubao-model",
    has_api_key: true,
    profiles: [],
    protocols: ["ark", "chatgpt"],
    local_model_catalog: { endpoints: [] },
    ...overrides,
  });
}

function shell(state: Partial<WizardState>) {
  hooks.reset();
  hooks.slots[0] = { ...INITIAL_WIZARD, surface: "wizard", ...state };
  hooks.slots[1] = status();
  hooks.begin();
  return WizardHost();
}

beforeEach(() => hooks.reset());

describe("first-run wizard steps (M-5)", () => {
  it("starts step 1 on the obvious choice: the model in use, else the only one", () => {
    // Only the environment's model.
    expect(defaultPath(status())?.model).toBe("doubao-model");
    // One saved profile, no environment key.
    const one = status({ has_api_key: false, profiles: [{ id: "mp-1", name: "Lab", provider: "ark", model: "m-1" }] });
    expect(defaultPath(one)?.profileId).toBe("mp-1");
    // Two choices, one of them active.
    const active = status({
      active_id: "mp-2",
      profiles: [
        { id: "mp-1", name: "A", provider: "chatgpt", model: "a" },
        { id: "mp-2", name: "B", provider: "chatgpt", model: "b" },
      ],
    });
    expect(defaultPath(active)?.profileId).toBe("mp-2");
    // Two choices and nothing active: the user picks.
    expect(defaultPath(status({ has_api_key: false, profiles: [
      { id: "mp-1", name: "A", provider: "chatgpt", model: "a" },
      { id: "mp-2", name: "B", provider: "chatgpt", model: "b" },
    ] }))).toBeNull();
  });

  it("says the subtitle once, on the first step, and offers Back after it", () => {
    const first = shell({ step: "path" });
    expect(textOf(first)).toContain(ot("onboarding.subtitle"));
    expect(has(first, "data-onboarding-back")).toHaveLength(0);

    const third = shell({ step: "readiness" });
    expect(textOf(third)).not.toContain(ot("onboarding.subtitle"));
    const back = has(third, "data-onboarding-back")[0]!;
    (back.props!.onClick as () => void)();
    expect((hooks.slots[0] as WizardState).step).toBe("test");
  });

  it("says the connection works in words after a successful test", () => {
    const receipt = { native_tool_call: "true", streaming: "true", stale: false, native_completion: true, reachable: true } as const;
    const ok = shell({ step: "test", testClicked: true, providerRequests: 1, receipt });
    expect(textOf(has(ok, "data-onboarding-test-ok")[0])).toBe(ot("onboarding.test.ok"));
    const failed = shell({ step: "test", testClicked: true, providerRequests: 1, receipt: { ...receipt, reachable: false } });
    expect(has(failed, "data-onboarding-test-ok")).toHaveLength(0);
  });

  it("keeps the environment's terminal steps behind one click", () => {
    const card = EnvironmentCard({
      readiness: {
        enabled: true,
        ready: false,
        state: "needs_setup",
        missing_environments: ["python", "r"],
        missing_packages: {},
        remediation: {
          requires_explicit_action: true,
          commands: [{ command: "openai4s setup --profile standard", label: "setup" }],
        },
      } as never,
    });
    const details = has(card, "data-readiness-commands")[0]!;
    expect(details.type).toBe("details");
    const commands = all(details, (node) => !!node.props && "item" in node.props);
    // FR-3: the one canonical instruction, not the payload's own argv.
    expect(commands.map((node) => (node.props!.item as { command: string }).command)).toEqual([
      "openai4s setup",
    ]);
    expect(textOf(all(details, (node) => node.type === "summary"))).toBe(ot("onboarding.env.showCommand"));
  });
});
