import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../customize/api";
import {
  INITIAL_WIZARD,
  REQUIRED_STEPS,
  RESUME_STORAGE_KEY,
  clearResumeMarker,
  formatWizardError,
  readResumeMarker,
  reduceWizard,
  requiredStepCount,
  resolveResume,
  wizardErrorFromUnknown,
  writeResumeMarker,
  type PathChoice,
  type WizardState,
} from "./machine";
import { sanitizeOnboardingStatus } from "./status";

const PATH: PathChoice = {
  kind: "cloud",
  profileId: "p1",
  provider: "chatgpt",
  model: "gpt-4o",
  baseUrl: "https://api.openai.com/v1",
  name: "prod",
};

function run(actions: Parameters<typeof reduceWizard>[1][], start = INITIAL_WIZARD): WizardState {
  return actions.reduce((state, action) => reduceWizard(state, action), start);
}

describe("M-01 wizard state machine", () => {
  it("has exactly four required decision steps", () => {
    expect(REQUIRED_STEPS).toEqual(["path", "test", "readiness", "project"]);
    expect(requiredStepCount()).toBe(4);
    expect(requiredStepCount()).toBeLessThanOrEqual(4);
  });

  it("hydrates a finished instance to hidden with no provider request", () => {
    const next = reduceWizard(INITIAL_WIZARD, { type: "hydrate", complete: true });
    expect(next.surface).toBe("hidden");
    expect(next.complete).toBe(true);
    expect(next.providerRequests).toBe(0);
  });

  it("skip completes without a provider request", () => {
    const next = run([{ type: "hydrate", complete: false }, { type: "skip" }]);
    expect(next.surface).toBe("done");
    expect(next.skipped).toBe(true);
    expect(next.complete).toBe(true);
    expect(next.providerRequests).toBe(0);
  });

  it("the step rail jumps to project without going back, and marks only what was decided", () => {
    const listed = run([
      { type: "hydrate", complete: false },
      { type: "choosePath", path: PATH },
    ]);
    expect(listed.decided).toEqual(["path"]);
    const jumped = reduceWizard(listed, { type: "goto", step: "project" });
    expect(jumped.surface).toBe("wizard");
    expect(jumped.step).toBe("project");
    expect(jumped.providerRequests).toBe(0);
  });

  it("errors keep the request id in the display string", () => {
    const failed = reduceWizard(INITIAL_WIZARD, {
      type: "fail",
      message: "admin only",
      requestId: "req-1",
    });
    expect(failed.error).toEqual({ message: "admin only", requestId: "req-1" });
    expect(formatWizardError(failed.error)).toBe("admin only [req-1]");
    const fromApi = wizardErrorFromUnknown(
      new ApiError({ error: "probe failed", request_id: "req-9f3a" }, 502),
    );
    expect(fromApi.requestId).toBe("req-9f3a");
    expect(formatWizardError(fromApi)).toBe("probe failed [req-9f3a]");
  });

  it("keeps providerRequests at 0 until the explicit Test action", () => {
    const beforeTest = run([
      { type: "hydrate", complete: false },
      { type: "choosePath", path: PATH },
      { type: "next" },
      { type: "next" },
      { type: "markReadinessSeen" },
      { type: "next" },
      { type: "goto", step: "project" },
    ]);
    expect(beforeTest.providerRequests).toBe(0);
    expect(beforeTest.testClicked).toBe(false);
    expect(beforeTest.decided.length).toBeLessThanOrEqual(4);
    const after = reduceWizard(beforeTest, { type: "startTest" });
    expect(after.providerRequests).toBe(1);
    expect(after.testClicked).toBe(true);
  });

  it("counts a model-list fetch as a provider request without deciding Test", () => {
    const start = run([{ type: "hydrate", complete: false }]);
    const fetched = reduceWizard(start, { type: "providerRequest" });
    expect(fetched.providerRequests).toBe(1);
    expect(fetched.testClicked).toBe(false);
    expect(fetched.decided).toEqual(start.decided);
  });

  it("drops extra credential fields from path choice so state JSON cannot hold a key", () => {
    const sneaky = {
      ...PATH,
      apiKey: "sk-fake-onboarding-must-not-leak-9f3a",
    } as PathChoice & { apiKey: string };
    const next = reduceWizard(INITIAL_WIZARD, { type: "choosePath", path: sneaky });
    const blob = JSON.stringify(next);
    expect(blob).not.toContain("sk-fake-onboarding-must-not-leak-9f3a");
    expect(blob).not.toContain("apiKey");
    expect(next.path).toEqual(PATH);
  });


  it.each([
    { profileId: "p2" },
    { provider: "claude" },
    { model: "different-model" },
    { baseUrl: "http://127.0.0.1:9/v1" },
  ])("clears capability evidence when the selected model identity changes: %j", (changed) => {
    const state = run([
      { type: "hydrate", complete: false },
      { type: "choosePath", path: PATH },
      { type: "next" },
      { type: "startTest" },
      { type: "testResult", receipt: { native_tool_call: "true", streaming: "true", stale: false, native_completion: true, reachable: true }, detail: "model A receipt", reachable: true },
      { type: "goto", step: "path" },
      { type: "choosePath", path: { ...PATH, ...changed } },
    ]);
    expect(state.receipt).toBeNull();
    expect(state.probeDetail).toBe("");
    expect(state.testClicked).toBe(false);
    expect(state.decided).not.toContain("test");
    // This counts requests actually sent during the wizard, across profiles.
    expect(state.providerRequests).toBe(1);
  });

  it("keeps the receipt when only the same model's display name or path kind changes", () => {
    const receipt = { native_tool_call: "true", streaming: "true", stale: false, native_completion: true, reachable: true } as const;
    const state = run([
      { type: "choosePath", path: PATH },
      { type: "startTest" },
      { type: "testResult", receipt, detail: "", reachable: true },
      { type: "choosePath", path: { ...PATH, name: "Renamed", kind: "existing" } },
    ]);
    expect(state.receipt).toEqual(receipt);
    expect(state.testClicked).toBe(true);
    expect(state.decided).toContain("test");
  });

  it("files a probe result only under the profile it was measured for", () => {
    const receipt = { native_tool_call: "true", streaming: "true", stale: false, native_completion: true, reachable: true } as const;
    const waiting = run([
      { type: "choosePath", path: PATH },
      { type: "startTest" },
    ]);
    // A result for some other profile -- a superseded probe, or one that fell
    // back to the active profile -- is not this selection's capability.
    const foreign = reduceWizard(waiting, { type: "testResult", receipt, detail: "", reachable: true, profileId: "someone-else" });
    expect(foreign).toBe(waiting);
    expect(foreign.receipt).toBeNull();
    const own = reduceWizard(waiting, { type: "testResult", receipt, detail: "", reachable: true, profileId: PATH.profileId });
    expect(own.receipt).toEqual(receipt);
    // With no selection there is no profile a result could belong to.
    const unselected = reduceWizard(INITIAL_WIZARD, { type: "testResult", receipt, detail: "", reachable: true, profileId: "p1" });
    expect(unselected.receipt).toBeNull();
  });

  it("will not grow decided past the four required steps", () => {
    let state = INITIAL_WIZARD;
    for (const step of REQUIRED_STEPS) {
      state = reduceWizard(state, { type: "choosePath", path: PATH });
      if (step === "test") state = reduceWizard(state, { type: "startTest" });
      if (step === "readiness") state = reduceWizard(state, { type: "markReadinessSeen" });
      if (step === "project") state = reduceWizard(state, { type: "markProjectOpened" });
    }
    const extra = reduceWizard(state, { type: "markProjectOpened" });
    expect(extra.decided).toEqual(REQUIRED_STEPS);
    expect(extra.decided.length).toBe(4);
  });
});

describe("M-01 onboarding status sanitizer", () => {
  it("strips credential keys and never reports the canary secret", () => {
    const secret = "sk-fake-onboarding-must-not-leak-9f3a";
    const status = sanitizeOnboardingStatus({
      provider: "chatgpt",
      model: "gpt-4o",
      api_key: secret,
      has_api_key: true,
      complete: false,
      outbound: 0,
      contacted: false,
      network: { allow_network: true, egress: "off", contacted: false, api_key: secret },
      profiles: [{ id: "p1", name: "prod", api_key: secret, has_api_key: true }],
      local_model_catalog: {
        endpoints: [],
        probed: 0,
        mutated_settings: false,
      },
    });
    expect(status.has_api_key).toBe(true);
    expect(status.outbound).toBe(0);
    expect(status.contacted).toBe(false);
    expect(JSON.stringify(status)).not.toContain(secret);
    expect(status.profiles[0] && "api_key" in status.profiles[0]).toBe(false);
  });
});

/** sessionStorage as one tab holds it. */
class TabStorage {
  data = new Map<string, string>();
  getItem(key: string): string | null {
    return this.data.has(key) ? this.data.get(key)! : null;
  }
  setItem(key: string, value: string): void {
    this.data.set(key, String(value));
  }
  removeItem(key: string): void {
    this.data.delete(key);
  }
}

/** Storage the browser refuses (blocked site data, some private modes). */
const REFUSING = {
  getItem: () => {
    throw new Error("SecurityError");
  },
  setItem: () => {
    throw new Error("QuotaExceededError");
  },
  removeItem: () => {
    throw new Error("SecurityError");
  },
};

const SAVED = { id: "mp-saved", name: "Saved", provider: "chatgpt", model: "gpt-test", base_url: "" };

describe("NV-04 resume marker", () => {
  it("holds only the step and the profile id", () => {
    const storage = new TabStorage();
    expect(writeResumeMarker("mp-saved", storage)).toBe(true);
    expect(JSON.parse(storage.getItem(RESUME_STORAGE_KEY)!)).toEqual({ step: "test", profile_id: "mp-saved" });
    expect(readResumeMarker(storage)).toEqual({ step: "test", profile_id: "mp-saved" });
    clearResumeMarker(storage);
    expect(readResumeMarker(storage)).toBeNull();
  });

  it("drops a marker of any other shape instead of trusting it", () => {
    for (const raw of [
      "not json",
      "[]",
      JSON.stringify({ step: "project", profile_id: "mp-saved" }),
      JSON.stringify({ step: "test", profile_id: "" }),
      JSON.stringify({ step: "test", profile_id: 7 }),
      JSON.stringify({ step: "test", profile_id: "mp-saved", model: "gpt-test" }),
    ]) {
      const storage = new TabStorage();
      storage.setItem(RESUME_STORAGE_KEY, raw);
      expect(readResumeMarker(storage), raw).toBeNull();
      expect(storage.getItem(RESUME_STORAGE_KEY), raw).toBeNull();
    }
  });

  it("is a no-op where storage is disabled or refuses every call", () => {
    expect(writeResumeMarker("mp-saved", null)).toBe(false);
    expect(readResumeMarker(null)).toBeNull();
    expect(() => clearResumeMarker(null)).not.toThrow();
    expect(writeResumeMarker("mp-saved", REFUSING)).toBe(false);
    expect(readResumeMarker(REFUSING)).toBeNull();
    expect(() => clearResumeMarker(REFUSING)).not.toThrow();
    // No id, no marker.
    const storage = new TabStorage();
    expect(writeResumeMarker("  ", storage)).toBe(false);
    expect(storage.data.size).toBe(0);
  });

  it("reads the global sessionStorage, and survives one that throws on access", () => {
    const storage = new TabStorage();
    vi.stubGlobal("sessionStorage", storage);
    try {
      expect(writeResumeMarker("mp-saved")).toBe(true);
      expect(readResumeMarker()).toEqual({ step: "test", profile_id: "mp-saved" });
      clearResumeMarker();
      expect(storage.data.size).toBe(0);
    } finally {
      vi.unstubAllGlobals();
    }
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
    Object.defineProperty(globalThis, "sessionStorage", {
      configurable: true,
      get() {
        throw new Error("SecurityError: access denied");
      },
    });
    try {
      expect(writeResumeMarker("mp-saved")).toBe(false);
      expect(readResumeMarker()).toBeNull();
      expect(() => clearResumeMarker()).not.toThrow();
    } finally {
      if (descriptor) Object.defineProperty(globalThis, "sessionStorage", descriptor);
      else delete (globalThis as { sessionStorage?: unknown }).sessionStorage;
    }
  });
});

describe("NV-04 what a reload does with the marker", () => {
  const marker = { step: "test" as const, profile_id: "mp-saved" };
  const status = (overrides: Record<string, unknown> = {}) => ({
    completed_explicitly: false as boolean | null,
    profiles: [SAVED] as Record<string, unknown>[],
    active_id: "mp-saved",
    ...overrides,
  });

  it("returns to the connection test when the saved profile is the active one", () => {
    expect(resolveResume(marker, status())).toEqual({ kind: "test", profile: SAVED });
  });

  it("offers the existing-profile choice when the saved profile still needs activating", () => {
    expect(resolveResume(marker, status({ active_id: "" }))).toEqual({ kind: "choose", profile: SAVED });
    expect(resolveResume(marker, status({ active_id: "mp-other" }))).toEqual({ kind: "choose", profile: SAVED });
  });

  it("leaves an install with no marker -- configured, upgraded or fresh -- to the original flow", () => {
    expect(resolveResume(null, status())).toEqual({ kind: "none" });
    expect(resolveResume(null, status({ completed_explicitly: true }))).toEqual({ kind: "none" });
  });

  it("clears the marker once setup was finished or skipped, here or in another tab", () => {
    expect(resolveResume(marker, status({ completed_explicitly: true }))).toEqual({ kind: "clear" });
  });

  it("clears the marker when the profile is not in the authenticated list", () => {
    expect(resolveResume(marker, status({ profiles: [] }))).toEqual({ kind: "clear" });
    expect(resolveResume(marker, status({ profiles: [{ ...SAVED, id: "mp-other" }] }))).toEqual({ kind: "clear" });
    // A status without a list (an older or partial answer) proves nothing.
    expect(resolveResume(marker, status({ profiles: undefined }))).toEqual({ kind: "clear" });
  });

  it("clears the marker against a daemon that predates `completed_explicitly`", () => {
    expect(resolveResume(marker, status({ completed_explicitly: null }))).toEqual({ kind: "clear" });
    expect(resolveResume(marker, null)).toEqual({ kind: "clear" });
    const old = sanitizeOnboardingStatus({ complete: true, profiles: [SAVED], active_id: "mp-saved" });
    expect(old.completed_explicitly).toBeNull();
    expect(resolveResume(marker, old)).toEqual({ kind: "clear" });
  });

  it("reads the new field only as a boolean", () => {
    expect(sanitizeOnboardingStatus({ completed_explicitly: false }).completed_explicitly).toBe(false);
    expect(sanitizeOnboardingStatus({ completed_explicitly: true }).completed_explicitly).toBe(true);
    expect(sanitizeOnboardingStatus({ completed_explicitly: "false" }).completed_explicitly).toBeNull();
  });

  it("resumes as a fresh wizard: no receipt, no request counted, only the path decided", () => {
    const path: PathChoice = { kind: "existing", profileId: "mp-saved", provider: "chatgpt", model: "gpt-test", baseUrl: "", name: "Saved" };
    const dirty: WizardState = {
      ...INITIAL_WIZARD,
      providerRequests: 3,
      testClicked: true,
      receipt: { native_tool_call: "true", streaming: "true", stale: false, native_completion: true, reachable: true },
      error: { message: "old", requestId: "" },
    };
    const atTest = reduceWizard(dirty, { type: "resume", step: "test", path });
    expect(atTest).toMatchObject({ surface: "wizard", step: "test", path, decided: ["path"], complete: false });
    expect(atTest.providerRequests).toBe(0);
    expect(atTest.testClicked).toBe(false);
    expect(atTest.receipt).toBeNull();
    expect(atTest.error).toBeNull();
    const choosing = reduceWizard(dirty, { type: "resume", step: "path", path });
    expect(choosing).toMatchObject({ surface: "wizard", step: "path", path, decided: [] });
    expect(choosing.providerRequests).toBe(0);
  });
});
