import { describe, expect, it } from "vitest";
import { ApiError } from "../customize/api";
import {
  INITIAL_WIZARD,
  REQUIRED_STEPS,
  checklistItems,
  formatWizardError,
  reduceWizard,
  requiredStepCount,
  wizardErrorFromUnknown,
  type PathChoice,
  type WizardState,
} from "./machine";
import { onboardingStatusHasSecret, sanitizeOnboardingStatus } from "./status";

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

  it("checklist lists four items and jump-to-project does not require going back", () => {
    const listed = run([
      { type: "hydrate", complete: false },
      { type: "choosePath", path: PATH },
      { type: "showChecklist" },
    ]);
    expect(listed.surface).toBe("checklist");
    expect(checklistItems(listed)).toEqual([
      { step: "path", done: true },
      { step: "test", done: false },
      { step: "readiness", done: false },
      { step: "project", done: false },
    ]);
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
    expect(onboardingStatusHasSecret(status, secret)).toBe(false);
    expect(JSON.stringify(status)).not.toContain(secret);
    expect(status.profiles[0] && "api_key" in status.profiles[0]).toBe(false);
  });
});
