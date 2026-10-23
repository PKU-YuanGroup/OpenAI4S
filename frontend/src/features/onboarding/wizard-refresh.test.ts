import { beforeEach, describe, expect, it, vi } from "vitest";

// O1: the readiness step can re-read the local environment snapshot in place.
// That is one GET /api/v1/onboarding -- never the wizard's initialising `load`
// -- and only environment/network/platform may change. The current step, the
// chosen path, the finished Test and its capability receipt belong to the
// reducer and must survive it. Skip and Continue are not `busy` because a
// local read can wait on a slow filesystem scan.

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

const api = vi.hoisted(() => ({
  fetchOnboarding: vi.fn(),
  probeModelProfile: vi.fn(),
  completeOnboarding: vi.fn(),
  saveModelProfile: vi.fn(),
  updateModelProfile: vi.fn(),
  activateModelProfile: vi.fn(),
  activateExistingModelProfile: vi.fn(),
}));

vi.mock("preact/hooks", () => ({
  useEffect: vi.fn(),
  useReducer: hooks.useReducer,
  useRef: hooks.useRef,
  useState: hooks.useState,
}));

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return { ...original, ...api };
});

import { ReadinessPanel } from "../../components/onboarding/ReadinessPanel";
import { WizardHost } from "../../components/onboarding/Wizard";
import { ot } from "./copy";
import {
  INITIAL_WIZARD,
  type PathChoice,
  type WizardReceipt,
  type WizardState,
} from "./machine";
import type { OnboardingStatus } from "./status";

// Slot order in WizardHost: reducer, status, busy, testing, probeRun, alive,
// savedProfiles, refresh state, refreshRun, refreshPending, readinessEpoch,
// wasReadinessShown.
const REDUCER = 0;
const STATUS = 1;
const REFRESH = 7;
const ALIVE = 5;

type VNode = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };
type Button = { text: string; disabled: boolean; onClick: () => void };
type RefreshProps = {
  busy: boolean;
  error: string;
  lastReadAt: number | null;
  onRefresh: () => void;
};

function render(): unknown {
  hooks.begin();
  return WizardHost();
}

function findVNode(node: unknown, match: (node: VNode) => boolean): VNode | null {
  if (!node || typeof node !== "object") return null;
  if (Array.isArray(node)) {
    for (const item of node) {
      const found = findVNode(item, match);
      if (found) return found;
    }
    return null;
  }
  const vnode = node as VNode;
  if (match(vnode)) return vnode;
  return findVNode(vnode.props?.children, match);
}

function textOf(node: unknown): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  return textOf((node as VNode).props?.children);
}

function buttons(node: unknown, out: Button[] = []): Button[] {
  if (!node || typeof node !== "object") return out;
  if (Array.isArray(node)) {
    for (const child of node) buttons(child, out);
    return out;
  }
  const vnode = node as VNode;
  if (vnode.type === "button") {
    out.push({
      text: textOf(vnode.props?.children).trim(),
      disabled: Boolean(vnode.props?.disabled),
      onClick: vnode.props?.onClick as () => void,
    });
  }
  buttons(vnode.props?.children, out);
  return out;
}

function refreshProps(tree: unknown): RefreshProps {
  const panel = findVNode(tree, (node) => node.type === ReadinessPanel);
  expect(panel, "readiness panel not rendered").toBeTruthy();
  return panel!.props!.refresh as RefreshProps;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function status(overrides: Record<string, unknown> = {}): OnboardingStatus {
  return {
    provider: "chatgpt",
    model: "gpt-test",
    base_url: "",
    has_api_key: true,
    complete: false,
    platform: "linux",
    native_runtime_supported: true,
    outbound: 2,
    contacted: true,
    network: { allow_network: false, egress: "off", contacted: false },
    profiles: [{ id: "p1", name: "Saved" }],
    active_id: "p1",
    protocols: [],
    local_model_catalog: { endpoints: [] },
    environment: {
      schema_version: 1,
      enabled: true,
      ready: false,
      state: "needs_setup",
      reason: "",
      requirements_digest: "",
      missing_environments: ["python"],
      missing_packages: {},
      remediation: null,
    },
    ...overrides,
  } as unknown as OnboardingStatus;
}

const REPAIRED = {
  schema_version: 1,
  enabled: true,
  ready: true,
  state: "ready",
  reason: "",
  requirements_digest: "",
  missing_environments: [],
  missing_packages: {},
  remediation: null,
};

const PATH: PathChoice = {
  kind: "cloud",
  profileId: "mp-1",
  provider: "chatgpt",
  model: "gpt-test",
  baseUrl: "https://api.example/v1",
  name: "Saved",
};

const RECEIPT: WizardReceipt = {
  native_tool_call: "true",
  streaming: "false",
  stale: false,
  native_completion: true,
  reachable: true,
};

function readinessWizard(): WizardState {
  return {
    ...INITIAL_WIZARD,
    surface: "wizard",
    step: "readiness",
    decided: ["path", "test"],
    providerRequests: 2,
    testClicked: true,
    path: PATH,
    receipt: RECEIPT,
  };
}

beforeEach(() => {
  hooks.reset();
  Object.values(api).forEach((spy) => spy.mockReset());
  render();
  hooks.slots[REDUCER] = readinessWizard();
  hooks.slots[STATUS] = status();
  render();
});

describe("first-run readiness refresh", () => {
  it("updates only the local projection and keeps the wizard's place and evidence", async () => {
    const gate = deferred<OnboardingStatus>();
    api.fetchOnboarding.mockReturnValueOnce(gate.promise);
    const before = hooks.slots[REDUCER];

    refreshProps(render()).onRefresh();
    expect(api.fetchOnboarding).toHaveBeenCalledTimes(1);
    expect(refreshProps(render()).busy).toBe(true);

    // Pending is its own state: Skip and Continue stay usable, and no model
    // or completion request rides along with the read.
    const pending = render();
    expect(buttons(pending).find((b) => b.text === ot("onboarding.skip"))!.disabled).toBe(false);
    expect(buttons(pending).find((b) => b.text === ot("onboarding.next"))!.disabled).toBe(false);
    expect(api.completeOnboarding).not.toHaveBeenCalled();
    expect(api.probeModelProfile).not.toHaveBeenCalled();

    gate.resolve(
      status({
        platform: "darwin",
        native_runtime_supported: false,
        environment: REPAIRED,
        network: { allow_network: true, egress: "proxy", contacted: true },
        // A full payload also carries identity; none of it may be adopted.
        model: "other-model",
        active_id: "replacement",
        profiles: [{ id: "replacement" }],
      }),
    );
    await gate.promise;
    await Promise.resolve();

    const merged = hooks.slots[STATUS] as OnboardingStatus;
    expect(merged.platform).toBe("darwin");
    expect(merged.native_runtime_supported).toBe(false);
    expect(merged.environment?.state).toBe("ready");
    expect(merged.network).toEqual({ allow_network: true, egress: "proxy", contacted: true });
    expect(merged.model).toBe("gpt-test");
    expect(merged.active_id).toBe("p1");
    expect(merged.profiles).toEqual([{ id: "p1", name: "Saved" }]);

    // The reducer -- step, path, finished Test, receipt, request count -- was
    // never re-hydrated: no action was dispatched at all.
    expect(hooks.slots[REDUCER]).toBe(before);
    const after = hooks.slots[REDUCER] as WizardState;
    expect(after.step).toBe("readiness");
    expect(after.path).toEqual(PATH);
    expect(after.receipt).toEqual(RECEIPT);
    expect(after.providerRequests).toBe(2);
    expect(after.providerRequests).not.toBe(0);

    const settled = refreshProps(render());
    expect(settled.busy).toBe(false);
    expect(settled.error).toBe("");
    expect(settled.lastReadAt).toBeTypeOf("number");
  });

  it("sends one read for a double click while the first is in flight", async () => {
    const gate = deferred<OnboardingStatus>();
    api.fetchOnboarding.mockReturnValueOnce(gate.promise);
    const action = refreshProps(render());
    action.onRefresh();
    // The second click lands before the first promise settles.
    refreshProps(render()).onRefresh();
    expect(api.fetchOnboarding).toHaveBeenCalledTimes(1);
    gate.resolve(status());
    await gate.promise;
    await Promise.resolve();
    expect(api.fetchOnboarding).toHaveBeenCalledTimes(1);
    expect(refreshProps(render()).busy).toBe(false);
  });

  it("drops a late answer once the user has left the readiness step", async () => {
    const gate = deferred<OnboardingStatus>();
    api.fetchOnboarding.mockReturnValueOnce(gate.promise);
    refreshProps(render()).onRefresh();

    hooks.slots[REDUCER] = { ...(hooks.slots[REDUCER] as WizardState), step: "test" };
    render();
    gate.resolve(status({ platform: "darwin", environment: REPAIRED }));
    await gate.promise;
    await Promise.resolve();

    expect((hooks.slots[STATUS] as OnboardingStatus).platform).toBe("linux");
    expect((hooks.slots[STATUS] as OnboardingStatus).environment?.state).toBe("needs_setup");
    expect((hooks.slots[REDUCER] as WizardState).step).toBe("test");
    // It is not left spinning for the next visit either.
    expect((hooks.slots[REFRESH] as { busy: boolean }).busy).toBe(false);
  });

  it("drops the answer after unmount", async () => {
    const gate = deferred<OnboardingStatus>();
    api.fetchOnboarding.mockReturnValueOnce(gate.promise);
    const before = hooks.slots[STATUS];
    refreshProps(render()).onRefresh();
    (hooks.slots[ALIVE] as { current: boolean }).current = false;
    gate.resolve(status({ platform: "darwin", environment: REPAIRED }));
    await gate.promise;
    await Promise.resolve();
    expect(hooks.slots[STATUS]).toBe(before);
  });

  it("keeps the old snapshot visible and retryable when the read fails", async () => {
    const before = hooks.slots[STATUS];
    api.fetchOnboarding.mockRejectedValueOnce(new Error("offline"));
    refreshProps(render()).onRefresh();
    await vi.waitFor(() => expect(refreshProps(render()).error).toContain("offline"));

    expect(hooks.slots[STATUS]).toBe(before);
    const failed = refreshProps(render());
    expect(failed.busy).toBe(false);
    expect(failed.lastReadAt).toBeNull();

    // The action is the retry: a second read is issued only on a new click.
    api.fetchOnboarding.mockResolvedValueOnce(status({ platform: "darwin" }));
    refreshProps(render()).onRefresh();
    await vi.waitFor(() =>
      expect((hooks.slots[STATUS] as OnboardingStatus).platform).toBe("darwin"),
    );
    expect(api.fetchOnboarding).toHaveBeenCalledTimes(2);
    expect(refreshProps(render()).error).toBe("");
  });
});
