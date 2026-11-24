import { describe, expect, it } from "vitest";
import { ApiError } from "../customize/api";
import {
  INITIAL_WIZARD,
  REQUIRED_STEPS,
  formatWizardError,
  reduceWizard,
  requiredStepCount,
  wizardErrorFromUnknown,
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
      // TEAM-10: step 1 is ticked once the choice is saved.
      { type: "choosePath", path: PATH, saved: true },
    ]);
    expect(listed.decided).toEqual(["path"]);
    const jumped = reduceWizard(listed, { type: "goto", step: "project" });
    expect(jumped.surface).toBe("wizard");
    expect(jumped.step).toBe("project");
    expect(jumped.providerRequests).toBe(0);
  });

  it("ticks step 1 only once the choice is saved, not on a radio click (TEAM-10)", () => {
    const picked = run([{ type: "hydrate", complete: false }, { type: "choosePath", path: PATH }]);
    expect(picked.decided).not.toContain("path");
    // A refused Continue: still not ticked.
    const refused = reduceWizard(picked, { type: "fail", message: "refused", requestId: "" });
    expect(refused.decided).not.toContain("path");
    // Continue succeeded: the wizard moves on and step 1 is done.
    expect(reduceWizard(picked, { type: "next" }).decided).toContain("path");
    // A profile that is already saved and active is a decided step 1.
    expect(reduceWizard(picked, { type: "choosePath", path: PATH, saved: true }).decided).toContain("path");
  });

  it("says who may change the setup instead of a bare admin_only refusal (TEAM-10)", () => {
    const refusal = wizardErrorFromUnknown(
      new ApiError({ error: "admin only", code: "admin_only", request_id: "req-7" }, 403),
    );
    expect(refusal.requestId).toBe("");
    expect(formatWizardError(refusal)).not.toContain("admin only");
    expect(formatWizardError(refusal)).toMatch(/administrator|管理员/);
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

  it("ticks Test only for a reachable result, never while the probe is still running", () => {
    const receipt = { native_tool_call: "true", streaming: "true", stale: false, native_completion: true, reachable: true } as const;
    const testing = run([
      { type: "choosePath", path: PATH, saved: true },
      { type: "next" },
      { type: "startTest" },
    ]);
    // "Contacting the endpoint..." is on screen: no result yet, so no tick.
    expect(testing.decided).not.toContain("test");
    // An unreachable endpoint (or a request that threw) is not a passed test.
    const failed = reduceWizard(testing, { type: "testResult", receipt: null, detail: "refused", reachable: false });
    expect(failed.decided).not.toContain("test");
    expect(failed.probeDetail).toBe("refused");
    const ok = reduceWizard(testing, { type: "testResult", receipt, detail: "", reachable: true });
    expect(ok.decided).toContain("test");
    // Continue past the step without a passing test still records the decision.
    const skipped = reduceWizard(failed, { type: "next" });
    expect(skipped.decided).toContain("test");
    expect(skipped.step).toBe("readiness");
  });

  it("will not grow decided past the four required steps", () => {
    const receipt = { native_tool_call: "true", streaming: "true", stale: false, native_completion: true, reachable: true } as const;
    let state = INITIAL_WIZARD;
    for (const step of REQUIRED_STEPS) {
      state = reduceWizard(state, { type: "choosePath", path: PATH, saved: true });
      if (step === "test") {
        state = reduceWizard(state, { type: "startTest" });
        state = reduceWizard(state, { type: "testResult", receipt, detail: "", reachable: true });
      }
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
