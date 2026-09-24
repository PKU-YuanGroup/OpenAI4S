import { beforeEach, describe, expect, it, vi } from "vitest";

// Saving or choosing a model in the first-run wizard: which profile it writes,
// and that the composer's model list hears about it.

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
  saveModelProfile: vi.fn(),
  updateModelProfile: vi.fn(),
  activateModelProfile: vi.fn(),
  activateExistingModelProfile: vi.fn(),
  fetchOnboarding: vi.fn(),
  loadModels: vi.fn(),
}));

vi.mock("preact/hooks", () => ({
  useEffect: vi.fn(),
  useReducer: hooks.useReducer,
  useRef: hooks.useRef,
  useState: hooks.useState,
}));

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return {
    ...original,
    saveModelProfile: api.saveModelProfile,
    updateModelProfile: api.updateModelProfile,
    activateModelProfile: api.activateModelProfile,
    activateExistingModelProfile: api.activateExistingModelProfile,
    fetchOnboarding: api.fetchOnboarding,
  };
});

vi.mock("../customize/models", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../customize/models")>()),
  loadModels: api.loadModels,
}));

import { WizardHost } from "../../components/onboarding/Wizard";
import { ot } from "./copy";
import { INITIAL_WIZARD, type PathChoice, type WizardState } from "./machine";

type VNode = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };

// Slot order in WizardHost: reducer, status, busy, testing, probeRun, alive.
const REDUCER = 0;
const STATUS = 1;

const STATUS_BODY = {
  profiles: [],
  active_id: "",
  protocols: [],
  local_model_catalog: { endpoints: [] },
  network: { allow_network: false, egress: "off", contacted: false },
};

function find(node: unknown, match: (node: VNode) => boolean): VNode | null {
  if (!node || typeof node !== "object") return null;
  if (Array.isArray(node)) {
    for (const item of node) {
      const found = find(item, match);
      if (found) return found;
    }
    return null;
  }
  const vnode = node as VNode;
  if (match(vnode)) return vnode;
  return find(vnode.props?.children, match);
}

function textOf(node: unknown): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  return textOf((node as VNode).props?.children);
}

function render(): VNode {
  hooks.begin();
  return WizardHost() as VNode;
}

function wizard(): WizardState {
  return hooks.slots[REDUCER] as WizardState;
}

function saveNew(path: PathChoice, key: string): Promise<void> {
  const step = find(render(), (node) => typeof node.props?.onSaveNew === "function")!;
  return (step.props!.onSaveNew as (path: PathChoice, key: string) => Promise<void>)(path, key);
}

/** Checklist -> "Choose a model path", the way a user goes back to the step. */
function backToPath(): void {
  const checklist = find(render(), (node) => node.type === "button" && textOf(node) === ot("onboarding.checklist"))!;
  (checklist.props!.onClick as () => void)();
  const step = find(
    render(),
    (node) => node.type === "button" && textOf(node).endsWith(ot("onboarding.step.path")),
  )!;
  (step.props!.onClick as () => void)();
  expect(wizard().step).toBe("path");
}

const CLOUD: PathChoice = {
  kind: "cloud",
  profileId: "",
  provider: "chatgpt",
  model: "gpt-test",
  baseUrl: "https://api.example/v1",
  name: "Cloud",
};

beforeEach(() => {
  hooks.reset();
  for (const fn of Object.values(api)) fn.mockReset();
  api.saveModelProfile.mockResolvedValue({ id: "mp-new" });
  api.updateModelProfile.mockResolvedValue({ ok: true });
  api.activateModelProfile.mockResolvedValue({});
  api.fetchOnboarding.mockResolvedValue(STATUS_BODY);
  api.activateExistingModelProfile.mockResolvedValue(STATUS_BODY);
  render();
  hooks.slots[REDUCER] = { ...INITIAL_WIZARD, surface: "wizard", step: "path" } satisfies WizardState;
  hooks.slots[STATUS] = STATUS_BODY;
});

describe("first-run wizard: the composer model list", () => {
  it("is re-read after a new profile is saved and activated", async () => {
    await saveNew(CLOUD, "sk-test");
    expect(api.activateModelProfile).toHaveBeenCalledWith("mp-new");
    expect(api.loadModels).toHaveBeenCalledTimes(1);
    expect(api.activateModelProfile.mock.invocationCallOrder[0]).toBeLessThan(
      api.loadModels.mock.invocationCallOrder[0]!,
    );
  });

  it("is re-read after an existing profile is activated", async () => {
    hooks.slots[REDUCER] = {
      ...wizard(),
      path: { kind: "existing", profileId: "mp-old", provider: "ark", model: "m", baseUrl: "", name: "Old" },
    } satisfies WizardState;
    const next = find(
      render(),
      (node) => node.type === "button" && node.props?.class === "solid-btn" && textOf(node) === ot("onboarding.next"),
    )!;
    await (next.props!.onClick as () => Promise<void>)();
    await vi.waitFor(() => expect(wizard().step).toBe("test"));
    expect(api.activateExistingModelProfile).toHaveBeenCalledWith("mp-old");
    expect(api.loadModels).toHaveBeenCalledTimes(1);
  });
});

describe("first-run wizard: saving a path again", () => {
  it("updates the profile it saved instead of adding another", async () => {
    await saveNew(CLOUD, "sk-first");
    expect(api.saveModelProfile).toHaveBeenCalledTimes(1);

    // Back to the path step: the form comes back filled, the key field empty.
    backToPath();
    await saveNew({ ...CLOUD, name: "Cloud (renamed)" }, "");
    expect(api.saveModelProfile).toHaveBeenCalledTimes(1);
    expect(api.updateModelProfile).toHaveBeenCalledWith("mp-new", {
      name: "Cloud (renamed)",
      provider: "chatgpt",
      base_url: "https://api.example/v1",
      model: "gpt-test",
    });
    expect(api.activateModelProfile).toHaveBeenLastCalledWith("mp-new");
    expect(wizard().path?.profileId).toBe("mp-new");
  });

  it("reuses a saved profile that already is this configuration", async () => {
    hooks.slots[STATUS] = {
      ...STATUS_BODY,
      profiles: [{ id: "mp-old", name: "Cloud", provider: "chatgpt", base_url: "https://api.example/v1/", model: "gpt-test" }],
    };
    await saveNew(CLOUD, "");
    expect(api.saveModelProfile).not.toHaveBeenCalled();
    expect(api.updateModelProfile).toHaveBeenCalledWith("mp-old", expect.not.objectContaining({ api_key: expect.anything() }));
    expect(api.activateModelProfile).toHaveBeenCalledWith("mp-old");
  });

  it("saves afresh when the profile it saved has since been deleted", async () => {
    await saveNew(CLOUD, "sk-first");
    api.updateModelProfile.mockRejectedValueOnce(Object.assign(new Error("profile not found"), { status: 404 }));
    api.saveModelProfile.mockResolvedValueOnce({ id: "mp-again" });
    backToPath();
    await saveNew(CLOUD, "sk-second");
    expect(api.saveModelProfile).toHaveBeenCalledTimes(2);
    expect(api.activateModelProfile).toHaveBeenLastCalledWith("mp-again");
    expect(wizard().error).toBeNull();
  });
});
