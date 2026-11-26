import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// O1 / PLAN §8: the first-run readiness step re-reads `GET /api/v1/onboarding`
// in place. Reusing `load()` is not allowed — it dispatches `hydrate`, which
// resets the step, the chosen path and the Test evidence. The refresh therefore
// owns its own pending/error/time state and request sequence: a double click may
// issue two local reads, and only the newest response may shape the snapshot.
// Skip and Continue are not `busy` because a local read can wait on a slow
// filesystem scan.
//
// This file also keeps the pre-existing "existing profile continuation" tests:
// the same hook harness drives both.

const hooks = vi.hoisted(() => {
  const slots: unknown[] = [];
  let cursor = 0;
  const set = (index: number, value: unknown) => {
    slots[index] =
      typeof value === "function"
        ? (value as (v: unknown) => unknown)(slots[index])
        : value;
  };
  return {
    slots,
    actions: [] as string[],
    reset() {
      slots.length = 0;
      cursor = 0;
      this.actions.length = 0;
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
      return [
        slots[index],
        (action: { type: string }) => {
          hooks.actions.push(action.type);
          set(index, reducer(slots[index], action));
        },
      ];
    },
  };
});

const api = vi.hoisted(() => ({
  fetchOnboarding: vi.fn(),
  probeModelProfile: vi.fn(),
  completeOnboarding: vi.fn(),
  activateExistingModelProfile: vi.fn(),
  // NV-04: the save-then-reload scenario below.
  saveModelProfile: vi.fn(),
  updateModelProfile: vi.fn(),
  activateModelProfile: vi.fn(),
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

const projectsApi = vi.hoisted(() => ({
  createProject: vi.fn(async () => ({})),
  openProject: vi.fn(async () => {}),
}));

vi.mock("../sessions/projects", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../sessions/projects")>()),
  ...projectsApi,
}));

import { useEffect } from "preact/hooks";
import { t } from "../../i18n";
import { ReadinessPanel } from "../../components/onboarding/ReadinessPanel";
import { SearchSetupCard } from "../../components/onboarding/SearchSetupCard";
import { WizardHost } from "../../components/onboarding/Wizard";
import { ot } from "./copy";
import {
  INITIAL_WIZARD,
  RESUME_STORAGE_KEY,
  type PathChoice,
  type WizardReceipt,
  type WizardState,
} from "./machine";
import type { OnboardingStatus } from "./status";

// Slot order in WizardHost: reducer, status, busy, testing, probeRun, alive,
// savedProfiles, refreshing, refreshedAt, refreshError, refreshRun,
// readinessEpoch, wasReadinessShown, searchPending.
const REDUCER = 0;
const STATUS = 1;
const ALIVE = 5;
const REFRESHED_AT = 8;
const REFRESH_ERROR = 9;

type VNode = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };
type Button = { text: string; disabled: boolean; onClick: () => void };
type RefreshPanelProps = {
  onRefresh?: () => void;
  refreshing?: boolean;
  refreshedAt?: number | null;
  refreshError?: string;
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

function findSolidButton(node: unknown): VNode | null {
  return findVNode(
    node,
    (vnode) => vnode.type === "button" && vnode.props?.class === "solid-btn",
  );
}

function refreshProps(tree: unknown): RefreshPanelProps {
  const panel = findVNode(tree, (node) => node.type === ReadinessPanel);
  expect(panel, "readiness panel not rendered").toBeTruthy();
  return panel!.props as RefreshPanelProps;
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

describe("existing-profile Wizard continuation", () => {
  beforeEach(() => {
    hooks.reset();
    Object.values(api).forEach((spy) => spy.mockReset());
    render();
    hooks.slots[REDUCER] = {
      ...INITIAL_WIZARD,
      surface: "wizard",
      step: "path",
      decided: ["path"],
      path: {
        kind: "existing",
        profileId: "profile/selected",
        provider: "chatgpt",
        model: "gpt-test",
        baseUrl: "",
        name: "Selected",
      },
    } satisfies WizardState;
    hooks.slots[STATUS] = status({ active_id: "profile/selected", profiles: [] });
    render();
  });

  it("refreshes an edited profile's model identity before entering the test step", async () => {
    api.activateExistingModelProfile.mockResolvedValueOnce({
      ...status(),
      profiles: [
        {
          id: "profile/selected",
          name: "Edited",
          provider: "chatgpt",
          model: "replacement-model",
          base_url: "http://127.0.0.1:9/v1",
        },
      ],
    });
    (findSolidButton(render())!.props!.onClick as () => void)();
    await vi.waitFor(() => expect(hooks.actions).toContain("next"));
    expect(hooks.actions).toContain("choosePath");
    expect(hooks.actions.indexOf("choosePath")).toBeLessThan(hooks.actions.indexOf("next"));
    const after = hooks.slots[REDUCER] as WizardState;
    expect(after.step).toBe("test");
    expect(after.path).toMatchObject({
      profileId: "profile/selected",
      name: "Edited",
      model: "replacement-model",
    });
  });

  it("activates the chosen profile before dispatching NEXT", async () => {
    let releaseActivation!: () => void;
    api.activateExistingModelProfile.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseActivation = () => resolve(status());
        }),
    );

    const button = findSolidButton(render());
    expect(button?.props?.onClick).toBeTypeOf("function");
    (button!.props!.onClick as () => void)();

    expect(api.activateExistingModelProfile).toHaveBeenCalledWith("profile/selected");
    expect(hooks.actions).not.toContain("next");
    expect((hooks.slots[REDUCER] as WizardState).step).toBe("path");

    releaseActivation();
    await vi.waitFor(() => expect(hooks.actions).toContain("next"));
    // The activation answered before the next step was dispatched.
    expect(hooks.actions.indexOf("choosePath")).toBeLessThan(hooks.actions.indexOf("next"));
    expect((hooks.slots[REDUCER] as WizardState).step).toBe("test");
  });
});

describe("O1 readiness refresh", () => {
  beforeEach(() => {
    hooks.reset();
    Object.values(api).forEach((spy) => spy.mockReset());
    render();
    hooks.slots[REDUCER] = readinessWizard();
    hooks.slots[STATUS] = status();
    render();
  });

  it("updates only the four status fields and keeps the wizard's place, path, receipt and request count", async () => {
    const gate = deferred<OnboardingStatus>();
    api.fetchOnboarding.mockReturnValueOnce(gate.promise);
    const before = hooks.slots[REDUCER];

    refreshProps(render()).onRefresh!();
    expect(api.fetchOnboarding).toHaveBeenCalledTimes(1);
    expect(refreshProps(render()).refreshing).toBe(true);

    // Pending is its own state: Skip and Continue stay usable, and no model
    // or completion request rides along with the local read.
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
    expect(hooks.actions).toEqual([]);

    const settled = refreshProps(render());
    expect(settled.refreshing).toBe(false);
    expect(settled.refreshError).toBe("");
    expect(settled.refreshedAt).toBeTypeOf("number");
    expect(hooks.slots[REFRESHED_AT]).toBeTypeOf("number");
  });

  it("keeps the newest response when a double click issues two local reads out of order", async () => {
    const first = deferred<OnboardingStatus>();
    const second = deferred<OnboardingStatus>();
    api.fetchOnboarding
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);

    // Two clicks, two requests: the sequence number retires the older answer.
    refreshProps(render()).onRefresh!();
    refreshProps(render()).onRefresh!();
    expect(api.fetchOnboarding).toHaveBeenCalledTimes(2);
    expect(refreshProps(render()).refreshing).toBe(true);

    second.resolve(status({ platform: "darwin", environment: REPAIRED }));
    await second.promise;
    await Promise.resolve();

    first.resolve(status({ platform: "freebsd", environment: REPAIRED }));
    await first.promise;
    await Promise.resolve();

    const merged = hooks.slots[STATUS] as OnboardingStatus;
    expect(merged.platform).toBe("darwin");
    expect(refreshProps(render()).refreshing).toBe(false);
    expect(refreshProps(render()).refreshError).toBe("");
  });

  it("lets an older failure arrive without overwriting the newer success", async () => {
    const first = deferred<OnboardingStatus>();
    const second = deferred<OnboardingStatus>();
    api.fetchOnboarding
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);

    refreshProps(render()).onRefresh!();
    refreshProps(render()).onRefresh!();
    second.resolve(status({ platform: "darwin", environment: REPAIRED }));
    await second.promise;
    await Promise.resolve();
    first.reject(new Error("old request failed"));
    await first.promise.catch(() => undefined);
    await Promise.resolve();

    expect((hooks.slots[STATUS] as OnboardingStatus).platform).toBe("darwin");
    expect(hooks.slots[REFRESH_ERROR]).toBe("");
    expect(refreshProps(render()).refreshing).toBe(false);
  });

  it("drops the answer after unmount", async () => {
    const gate = deferred<OnboardingStatus>();
    api.fetchOnboarding.mockReturnValueOnce(gate.promise);
    const before = hooks.slots[STATUS];
    refreshProps(render()).onRefresh!();
    (hooks.slots[ALIVE] as { current: boolean }).current = false;
    gate.resolve(status({ platform: "darwin", environment: REPAIRED }));
    await gate.promise;
    await Promise.resolve();
    expect(hooks.slots[STATUS]).toBe(before);
  });

  it("keeps the old snapshot, says 未刷新 with the reason, and retries on the next click", async () => {
    const before = hooks.slots[STATUS];
    api.fetchOnboarding.mockRejectedValueOnce(new Error("offline"));
    refreshProps(render()).onRefresh!();
    await vi.waitFor(() => expect(String(hooks.slots[REFRESH_ERROR])).toContain("offline"));

    expect(hooks.slots[STATUS]).toBe(before);
    const failed = refreshProps(render());
    expect(failed.refreshing).toBe(false);
    // No success has happened yet, so there is no "last read" claim.
    expect(failed.refreshedAt).toBeNull();

    // The action is the retry: a second read is issued only on a new click.
    api.fetchOnboarding.mockResolvedValueOnce(status({ platform: "darwin" }));
    refreshProps(render()).onRefresh!();
    await vi.waitFor(() =>
      expect((hooks.slots[STATUS] as OnboardingStatus).platform).toBe("darwin"),
    );
    expect(api.fetchOnboarding).toHaveBeenCalledTimes(2);
    expect(hooks.slots[REFRESH_ERROR]).toBe("");
    expect(hooks.slots[REFRESHED_AT]).toBeTypeOf("number");
  });
});

// ONB-2: Continue on the readiness step dropped a search choice that was made
// but not applied, with no message.
describe("ONB-2 Continue applies the search card's pending choice", () => {
  beforeEach(() => {
    hooks.reset();
    Object.values(api).forEach((spy) => spy.mockReset());
    render();
    hooks.slots[REDUCER] = readinessWizard();
    hooks.slots[STATUS] = status();
  });

  function pendingRef(tree: unknown): { current: (() => Promise<boolean>) | null } {
    const card = findVNode(tree, (node) => node.type === SearchSetupCard);
    expect(card, "search card not rendered").toBeTruthy();
    return card!.props!.pendingRef as { current: (() => Promise<boolean>) | null };
  }
  const next = (tree: unknown) => buttons(tree).find((b) => b.text === ot("onboarding.next"))!;

  it("stays on the step when the card refuses (a missing key, a failed save)", async () => {
    const tree = render();
    const apply = vi.fn(async () => false);
    pendingRef(tree).current = apply;
    next(tree).onClick();
    await vi.waitFor(() => expect(apply).toHaveBeenCalledTimes(1));
    await Promise.resolve();
    await Promise.resolve();
    expect(hooks.actions).not.toContain("next");
    expect((hooks.slots[REDUCER] as WizardState).step).toBe("readiness");
  });

  it("moves on once the pending choice is applied", async () => {
    const tree = render();
    const apply = vi.fn(async () => true);
    pendingRef(tree).current = apply;
    next(tree).onClick();
    await vi.waitFor(() => expect(hooks.actions).toContain("next"));
    expect(apply).toHaveBeenCalledTimes(1);
    const after = hooks.slots[REDUCER] as WizardState;
    expect(after.step).toBe("project");
    expect(after.decided).toContain("readiness");
  });
});

// NV-04: the wizard saves and activates a profile before the connection test,
// the environment and the project. A saved profile makes the install read
// `complete`, so a page reload used to hide those steps as done. The same tab
// now comes back to the test -- and only the user's clicks contact anyone.
describe("NV-04 a reload part-way through the wizard", () => {
  class TabStorage {
    data = new Map<string, string>();
    getItem(key: string) {
      return this.data.get(key) ?? null;
    }
    setItem(key: string, value: string) {
      this.data.set(key, String(value));
    }
    removeItem(key: string) {
      this.data.delete(key);
    }
  }

  const SAVED_ROW = {
    id: "mp-new",
    name: "Lab key",
    provider: "chatgpt",
    model: "gpt-test",
    base_url: "https://api.example/v1",
    has_api_key: true,
  };
  const CLOUD: PathChoice = {
    kind: "cloud",
    profileId: "",
    provider: "chatgpt",
    model: "gpt-test",
    baseUrl: "https://api.example/v1",
    name: "Lab key",
  };
  // `can_configure: true` is what `sanitizeOnboardingStatus` answers for the
  // single-user install these cases are about (a team member never gets the
  // wizard, marker or not).
  const fresh = (overrides: Record<string, unknown> = {}) =>
    status({
      complete: false,
      completed_explicitly: false,
      can_configure: true,
      profiles: [],
      active_id: "",
      ...overrides,
    });
  const afterSave = (overrides: Record<string, unknown> = {}) =>
    status({
      complete: true,
      completed_explicitly: false,
      can_configure: true,
      profiles: [SAVED_ROW],
      active_id: "mp-new",
      ...overrides,
    });

  let storage: TabStorage;
  let requests: string[];

  /** A page load: fresh component state, then its mount effects. */
  async function pageLoad(): Promise<void> {
    hooks.reset();
    vi.mocked(useEffect).mockClear();
    render();
    for (const [effect] of vi.mocked(useEffect).mock.calls) (effect as () => unknown)();
    await vi.waitFor(() =>
      expect(hooks.actions.some((type) => type === "hydrate" || type === "resume")).toBe(true),
    );
  }

  function wizardState(): WizardState {
    return hooks.slots[REDUCER] as WizardState;
  }

  async function saveCloudProfile(): Promise<void> {
    const step = findVNode(render(), (node) => typeof node.props?.onSaveNew === "function");
    expect(step, "the path step is not rendered").toBeTruthy();
    await (step!.props!.onSaveNew as (path: PathChoice, key: string) => Promise<void>)(
      CLOUD,
      "fake-key-typed-in-the-wizard",
    );
  }

  beforeEach(() => {
    Object.values(api).forEach((spy) => spy.mockReset());
    storage = new TabStorage();
    vi.stubGlobal("sessionStorage", storage);
    requests = [];
    // Everything that is not a mocked wizard API (the composer's model list).
    vi.stubGlobal("fetch", (url: string) => {
      requests.push(String(url));
      return Promise.resolve(new Response("{}", { status: 200 }));
    });
    api.saveModelProfile.mockResolvedValue({ id: "mp-new" });
    api.activateModelProfile.mockResolvedValue({ ok: true, active_id: "mp-new" });
    api.probeModelProfile.mockResolvedValue({ reachable: true, capability_receipt: null });
    api.completeOnboarding.mockResolvedValue(status({ complete: true, completed_explicitly: true }));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("a new user saves, reloads, and is back at the connection test with nothing sent", async () => {
    api.fetchOnboarding.mockResolvedValueOnce(fresh()).mockResolvedValue(afterSave());
    await pageLoad();
    expect(wizardState()).toMatchObject({ surface: "wizard", step: "path" });

    // The marker is written once the save answered, before activation is asked.
    let markerAtActivation: string | null = "unread";
    api.activateModelProfile.mockImplementationOnce(async () => {
      markerAtActivation = storage.getItem(RESUME_STORAGE_KEY);
      return { ok: true, active_id: "mp-new" };
    });
    await saveCloudProfile();
    expect(JSON.parse(markerAtActivation!)).toEqual({ step: "test", profile_id: "mp-new" });
    expect(wizardState().step).toBe("test");
    // Only the step and the id: no key, model, endpoint or probe result.
    const stored = storage.getItem(RESUME_STORAGE_KEY)!;
    expect(JSON.parse(stored)).toEqual({ step: "test", profile_id: "mp-new" });
    expect(stored).not.toContain("fake-key-typed-in-the-wizard");

    await pageLoad();
    const resumed = wizardState();
    expect(resumed).toMatchObject({
      surface: "wizard",
      step: "test",
      decided: ["path"],
      providerRequests: 0,
      testClicked: false,
      receipt: null,
    });
    expect(resumed.path).toMatchObject({ profileId: "mp-new", model: "gpt-test", name: "Lab key" });
    const tree = findVNode(render(), (node) => node.props?.id === "onboarding");
    expect(tree?.props?.["data-onboarding-step"]).toBe("test");
    expect(tree?.props?.["data-provider-requests"]).toBe("0");
    // The reload re-read the local status (readiness included) and saved nothing.
    expect((hooks.slots[STATUS] as OnboardingStatus).profiles.filter((p) => p.id === "mp-new")).toHaveLength(1);
    expect(api.saveModelProfile).toHaveBeenCalledOnce();
    expect(api.updateModelProfile).not.toHaveBeenCalled();
    expect(api.activateModelProfile).toHaveBeenCalledOnce();
    expect(api.activateExistingModelProfile).not.toHaveBeenCalled();
    expect(api.completeOnboarding).not.toHaveBeenCalled();
    // Zero probes before the click.
    expect(api.probeModelProfile).not.toHaveBeenCalled();
    expect(requests.some((url) => /probe|discover/.test(url))).toBe(false);

    const test = buttons(render()).find((button) => button.text === t("cust.models.test"));
    expect(test).toBeTruthy();
    test!.onClick();
    await vi.waitFor(() => expect(api.probeModelProfile).toHaveBeenCalledWith("mp-new"));
    expect(wizardState().providerRequests).toBe(1);
  });

  it("offers the saved profile on the path step when its activation had not happened", async () => {
    storage.setItem(RESUME_STORAGE_KEY, JSON.stringify({ step: "test", profile_id: "mp-new" }));
    api.fetchOnboarding.mockResolvedValue(afterSave({ active_id: "" }));
    api.activateExistingModelProfile.mockResolvedValue(afterSave());
    await pageLoad();
    expect(wizardState()).toMatchObject({ surface: "wizard", step: "path", decided: [], providerRequests: 0 });
    expect(wizardState().path).toMatchObject({ kind: "existing", profileId: "mp-new" });
    // Nothing is activated for the user.
    expect(api.activateExistingModelProfile).not.toHaveBeenCalled();
    expect(api.activateModelProfile).not.toHaveBeenCalled();

    (findSolidButton(render())!.props!.onClick as () => void)();
    await vi.waitFor(() => expect(wizardState().step).toBe("test"));
    expect(api.activateExistingModelProfile).toHaveBeenCalledWith("mp-new");
    expect(api.probeModelProfile).not.toHaveBeenCalled();
  });

  it("an explicit skip clears the marker, and the next load stays closed", async () => {
    storage.setItem(RESUME_STORAGE_KEY, JSON.stringify({ step: "test", profile_id: "mp-new" }));
    api.fetchOnboarding.mockResolvedValue(afterSave());
    await pageLoad();
    expect(wizardState().step).toBe("test");
    const skip = buttons(render()).find((button) => button.text === ot("onboarding.skip"))!;
    skip.onClick();
    await vi.waitFor(() => expect(wizardState().surface).toBe("done"));
    expect(storage.getItem(RESUME_STORAGE_KEY)).toBeNull();

    api.fetchOnboarding.mockResolvedValue(afterSave({ completed_explicitly: true }));
    await pageLoad();
    expect(wizardState().surface).toBe("hidden");
    expect(api.probeModelProfile).not.toHaveBeenCalled();
  });

  it("walking on keeps the marker; finishing on the project step clears it", async () => {
    storage.setItem(RESUME_STORAGE_KEY, JSON.stringify({ step: "test", profile_id: "mp-new" }));
    api.fetchOnboarding.mockResolvedValue(afterSave());
    await pageLoad();
    for (const step of ["readiness", "project"]) {
      buttons(render()).find((button) => button.text === ot("onboarding.next"))!.onClick();
      expect(wizardState().step).toBe(step);
      expect(storage.getItem(RESUME_STORAGE_KEY)).not.toBeNull();
    }
    const project = findVNode(render(), (node) => typeof node.props?.onCreate === "function")!;
    (project.props!.onCreate as (name: string) => void)("First project");
    await vi.waitFor(() => expect(wizardState().surface).toBe("done"));
    expect(projectsApi.createProject).toHaveBeenCalledWith("First project", "", "");
    expect(api.completeOnboarding).toHaveBeenCalledWith({ skip: true });
    expect(storage.getItem(RESUME_STORAGE_KEY)).toBeNull();
    expect(api.probeModelProfile).not.toHaveBeenCalled();
  });

  it.each([
    ["an install configured before (no marker)", null, afterSave()],
    ["a setup already completed", "mp-new", afterSave({ completed_explicitly: true })],
    ["a profile the authenticated list no longer shows", "mp-new", afterSave({ profiles: [], active_id: "" })],
    ["a daemon that predates completed_explicitly", "mp-new", afterSave({ completed_explicitly: null })],
  ])("keeps the original flow for %s", async (_name, markerId, answer) => {
    if (markerId) storage.setItem(RESUME_STORAGE_KEY, JSON.stringify({ step: "test", profile_id: markerId }));
    api.fetchOnboarding.mockResolvedValue(answer);
    await pageLoad();
    expect(hooks.actions).toContain("hydrate");
    expect(hooks.actions).not.toContain("resume");
    expect(wizardState().surface).toBe("hidden");
    expect(render()).toBeNull();
    expect(storage.getItem(RESUME_STORAGE_KEY)).toBeNull();
    expect(api.probeModelProfile).not.toHaveBeenCalled();
  });

  it("writes no marker for a daemon that predates the field, so a reload keeps the original flow", async () => {
    api.fetchOnboarding
      .mockResolvedValueOnce(fresh({ completed_explicitly: null }))
      .mockResolvedValue(afterSave({ completed_explicitly: null }));
    await pageLoad();
    await saveCloudProfile();
    expect(storage.data.size).toBe(0);
    await pageLoad();
    expect(wizardState().surface).toBe("hidden");
  });

  it("keeps the original flow when the browser refuses storage", async () => {
    Object.defineProperty(globalThis, "sessionStorage", {
      configurable: true,
      get() {
        throw new Error("SecurityError: storage disabled");
      },
    });
    api.fetchOnboarding.mockResolvedValueOnce(fresh()).mockResolvedValue(afterSave());
    await pageLoad();
    await saveCloudProfile();
    // The save itself still works and moves on to the test.
    expect(api.activateModelProfile).toHaveBeenCalledWith("mp-new");
    expect(wizardState().step).toBe("test");
    await pageLoad();
    expect(wizardState().surface).toBe("hidden");
    expect(api.probeModelProfile).not.toHaveBeenCalled();
  });
});
