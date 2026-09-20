import { beforeEach, describe, expect, it, vi } from "vitest";

// Skip must stay available while "Test connection" is waiting. A probe to an
// endpoint that never answers waits out a connect timeout and its retries,
// which is minutes; the wizard used to hold the whole footer disabled for
// that long, so the only way out of first-run setup did nothing.

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
  probeModelProfile: vi.fn(),
  completeOnboarding: vi.fn(),
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

import { WizardHost } from "../../components/onboarding/Wizard";
import { t } from "../../i18n";
import { ot } from "./copy";
import { INITIAL_WIZARD, type PathChoice, type WizardState } from "./machine";

type VNode = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };
type Button = { text: string; disabled: boolean; onClick: () => void };

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

function render(): Button[] {
  hooks.begin();
  return buttons(WizardHost());
}

function button(label: string): Button {
  const match = render().find((b) => b.text === label);
  expect(match, `no "${label}" button`).toBeTruthy();
  return match!;
}

// Slot order in WizardHost: reducer, status, busy, testing, probeRun, alive.
const REDUCER = 0;

function wizard(): WizardState {
  return hooks.slots[REDUCER] as WizardState;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((r, fail) => {
    resolve = r;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function pathChoice(node: unknown): ((path: PathChoice) => void) | null {
  if (!node || typeof node !== "object") return null;
  if (Array.isArray(node)) {
    for (const item of node) {
      const found = pathChoice(item);
      if (found) return found;
    }
    return null;
  }
  const props = (node as VNode).props;
  if (typeof props?.onChoose === "function") return props.onChoose as (path: PathChoice) => void;
  return pathChoice(props?.children);
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

const UNREACHABLE = {
  reachable: false,
  state: "unreachable",
  detail: "the endpoint could not be reached, or the connection dropped",
  request_id: "req-late",
  capability_receipt: null,
};

describe("first-run wizard: skipping while a connection test is waiting", () => {
  beforeEach(() => {
    hooks.reset();
    api.probeModelProfile.mockReset();
    api.completeOnboarding.mockReset();
    render();
    hooks.slots[REDUCER] = {
      ...INITIAL_WIZARD,
      surface: "wizard",
      step: "test",
      decided: ["path"],
      path: {
        kind: "cloud",
        profileId: "mp-test",
        provider: "chatgpt",
        model: "",
        baseUrl: "http://127.0.0.1:9/v1",
        name: "unreachable",
      },
    } satisfies WizardState;
  });

  it("keeps Skip enabled and completes the skip before the probe answers", async () => {
    const probe = deferred<Record<string, unknown>>();
    api.probeModelProfile.mockReturnValueOnce(probe.promise);
    api.completeOnboarding.mockResolvedValueOnce({});

    button(t("cust.models.test")).onClick();
    expect(api.probeModelProfile).toHaveBeenCalledWith("mp-test");

    const testing = button(t("cust.models.testing"));
    expect(testing.disabled).toBe(true);
    const skip = button(ot("onboarding.skip"));
    expect(skip.disabled).toBe(false);

    skip.onClick();
    await vi.waitFor(() => expect(wizard().surface).toBe("done"));
    expect(api.completeOnboarding).toHaveBeenCalledWith({ skip: true });

    // The probe answers after the wizard has closed: nothing it says may land.
    probe.resolve(UNREACHABLE);
    await probe.promise;
    await Promise.resolve();
    expect(wizard().surface).toBe("done");
    expect(wizard().error).toBeNull();
  });

  it("drops a late answer once the user has moved past the test step", async () => {
    const probe = deferred<Record<string, unknown>>();
    api.probeModelProfile.mockReturnValueOnce(probe.promise);

    button(t("cust.models.test")).onClick();
    const next = button(ot("onboarding.next"));
    expect(next.disabled).toBe(false);
    next.onClick();
    expect(wizard().step).toBe("readiness");

    probe.resolve(UNREACHABLE);
    await probe.promise;
    await Promise.resolve();
    expect(wizard().error).toBeNull();
    expect(wizard().step).toBe("readiness");
  });


  // Characterization, not a regression test for the choosePath change: every
  // exit from the test step already retired the running probe (Checklist, Next,
  // Skip and Finish all call leaveTest), and onTest takes a fresh run id, so
  // this passed before choosePath called leaveTest too and passes without it.
  // It pins the guarantee across a model switch; what is new is pinned in
  // machine.test.ts -- the reducer drops a result measured for another profile.
  it.each(["response", "error"])("does not apply the former model's late %s to the newly tested model", async (outcome) => {
    const old = deferred<Record<string, unknown>>();
    api.probeModelProfile.mockReturnValueOnce(old.promise);
    button(t("cust.models.test")).onClick();
    button(ot("onboarding.checklist")).onClick();
    render().find((b) => b.text.endsWith(ot("onboarding.step.path")))!.onClick();
    hooks.slots[1] = { profiles: [], protocols: [], local_model_catalog: { endpoints: [] } };
    hooks.begin();
    const choose = pathChoice(WizardHost());
    expect(choose).toBeTypeOf("function");
    choose!({ ...wizard().path!, kind: "existing", profileId: "mp-new", model: "new-model" });
    button(ot("onboarding.checklist")).onClick();
    render().find((b) => b.text.endsWith(ot("onboarding.step.test")))!.onClick();
    api.probeModelProfile.mockResolvedValueOnce({
      reachable: true,
      capability_receipt: { native_tool_call: false, streaming: false, reachable: true },
    });
    button(t("cust.models.test")).onClick();
    await vi.waitFor(() => expect(wizard().receipt?.native_tool_call).toBe("false"));
    expect(api.probeModelProfile).toHaveBeenLastCalledWith("mp-new");

    if (outcome === "error") old.reject(new Error("old model failure"));
    else old.resolve({ reachable: true, capability_receipt: { native_tool_call: true, streaming: true } });
    await old.promise.catch(() => {});
    await Promise.resolve();
    expect(wizard().path?.profileId).toBe("mp-new");
    expect(wizard().receipt?.native_tool_call).toBe("false");
    expect(wizard().error).toBeNull();
    expect(button(t("cust.models.test")).disabled).toBe(false);
  });

  it("probes the selected path, never the active profile in its place", async () => {
    // An unsaved local choice has no profile id yet. Falling back to
    // `status.active_id` measured a different model and showed its capability
    // badges -- and ticked "test" -- under the local one.
    hooks.slots[1] = {
      profiles: [{ id: "mp-active", name: "Claude", provider: "claude", model: "claude-x", base_url: "" }],
      active_id: "mp-active",
      protocols: [],
      local_model_catalog: { endpoints: [] },
    };
    hooks.slots[REDUCER] = {
      ...wizard(),
      path: { kind: "local", profileId: "", provider: "ollama", model: "llama3", baseUrl: "http://127.0.0.1:11434/v1", name: "llama3" },
    } satisfies WizardState;

    button(t("cust.models.test")).onClick();
    await Promise.resolve();
    expect(api.probeModelProfile).not.toHaveBeenCalled();
    expect(wizard().error?.message).toBe(ot("onboarding.test.needProfile"));
    expect(wizard().receipt).toBeNull();
    expect(wizard().providerRequests).toBe(0);
  });

  it("treats a local model edited after its save as unsaved again", async () => {
    // Saved as mp-local/llama3, then the model box is edited without pressing
    // Next. The saved profile is still llama3 on the server, so probing its id
    // would measure llama3 and show the result as the edited model's.
    hooks.slots[1] = {
      profiles: [],
      active_id: "mp-local",
      protocols: [],
      local_model_catalog: { endpoints: [{ label: "Ollama", base_url: "http://127.0.0.1:11434/v1", default_model: "llama3" }] },
    };
    hooks.slots[REDUCER] = {
      ...wizard(),
      step: "path",
      path: { kind: "local", profileId: "mp-local", provider: "chatgpt", model: "llama3", baseUrl: "http://127.0.0.1:11434/v1", name: "Ollama" },
    } satisfies WizardState;

    hooks.begin();
    const pathStep = findVNode(WizardHost(), (node) => typeof node.props?.onChoose === "function");
    expect(pathStep).toBeTruthy();
    const rendered = (pathStep!.type as (props: unknown) => unknown)(pathStep!.props);
    const modelBox = findVNode(rendered, (node) => node.type === "input" && node.props?.value === "llama3" && typeof node.props?.onInput === "function");
    expect(modelBox).toBeTruthy();
    (modelBox!.props!.onInput as (event: unknown) => void)({ currentTarget: { value: "qwen3" } });

    expect(wizard().path).toMatchObject({ kind: "local", model: "qwen3", profileId: "" });
    render().find((b) => b.text === ot("onboarding.checklist"))!.onClick();
    render().find((b) => b.text.endsWith(ot("onboarding.step.test")))!.onClick();
    button(t("cust.models.test")).onClick();
    await Promise.resolve();
    expect(api.probeModelProfile).not.toHaveBeenCalled();
    expect(wizard().error?.message).toBe(ot("onboarding.test.needProfile"));
  });

  it("selects the active profile before probing it when no path was chosen", async () => {
    hooks.slots[1] = {
      profiles: [{ id: "mp-active", name: "Claude", provider: "claude", model: "claude-x", base_url: "https://api.anthropic.com" }],
      active_id: "mp-active",
      protocols: [],
      local_model_catalog: { endpoints: [] },
    };
    hooks.slots[REDUCER] = { ...wizard(), path: null, decided: [] } satisfies WizardState;
    api.probeModelProfile.mockResolvedValueOnce({
      reachable: true,
      capability_receipt: { native_tool_call: true, streaming: true, reachable: true },
    });

    button(t("cust.models.test")).onClick();
    await vi.waitFor(() => expect(wizard().receipt?.native_tool_call).toBe("true"));
    expect(api.probeModelProfile).toHaveBeenCalledWith("mp-active");
    // The receipt is filed under the model that was measured.
    expect(wizard().path).toMatchObject({ kind: "existing", profileId: "mp-active", model: "claude-x" });
  });

  it("still reports a failed probe while the user is waiting for it", async () => {
    api.probeModelProfile.mockResolvedValueOnce(UNREACHABLE);

    button(t("cust.models.test")).onClick();
    await vi.waitFor(() => expect(wizard().error?.message).toContain("could not be reached"));
    expect(wizard().error?.requestId).toBe("req-late");
    // The probe is over, so the button is usable again.
    expect(button(t("cust.models.test")).disabled).toBe(false);
  });
});
