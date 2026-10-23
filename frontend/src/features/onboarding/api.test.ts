import { afterEach, describe, expect, it, vi } from "vitest";
import { activateExistingModelProfile, mergeReadinessRefresh } from "./api";
import { sanitizeOnboardingStatus } from "./status";

function snapshot(overrides: Record<string, unknown> = {}) {
  return sanitizeOnboardingStatus({
    provider: "chatgpt",
    model: "gpt-test",
    base_url: "",
    has_api_key: true,
    complete: false,
    platform: "linux",
    native_runtime_supported: true,
    outbound: 2,
    contacted: true,
    network: { allow_network: true, egress: "proxy", contacted: true },
    profiles: [{ id: "p1", name: "saved" }],
    active_id: "p1",
    protocols: [],
    local_model_catalog: { endpoints: [] },
    environment: {
      enabled: true,
      ready: false,
      state: "needs_setup",
      missing_environments: ["python"],
      missing_packages: {},
      remediation: null,
    },
    ...overrides,
  });
}

describe("existing-profile onboarding", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("activates the selected profile before refreshing onboarding status", async () => {
    const calls: Array<{ url: string; method: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, method: String(init?.method || "GET") });
        const body = url.endsWith("/onboarding")
          ? {
              complete: false,
              active_id: "profile/selected",
              profiles: [],
              protocols: [],
              network: {},
              local_model_catalog: {},
            }
          : { ok: true };
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }),
    );

    const status = await activateExistingModelProfile("profile/selected");

    expect(calls).toEqual([
      {
        url: "/api/v1/model-profiles/profile%2Fselected/activate",
        method: "POST",
      },
      { url: "/api/v1/onboarding", method: "GET" },
    ]);
    expect(status.active_id).toBe("profile/selected");
  });
});

describe("readiness refresh projection", () => {
  it("updates only what the local reread measured, never the wizard-owned identity", () => {
    const current = snapshot();
    const refreshed = snapshot({
      platform: "darwin",
      native_runtime_supported: false,
      network: { allow_network: false, egress: "off", contacted: false },
      environment: {
        enabled: true,
        ready: true,
        state: "ready",
        missing_environments: [],
        missing_packages: {},
        remediation: null,
      },
      // A full GET payload also carries model identity and completion. The
      // wizard's step, chosen path, finished tests and receipt must survive a
      // refresh, so none of these may be adopted from the refreshed snapshot.
      provider: "claude",
      model: "other-model",
      has_api_key: false,
      complete: true,
      outbound: 0,
      profiles: [{ id: "replacement", name: "other" }],
      active_id: "replacement",
    });

    const merged = mergeReadinessRefresh(current, refreshed);

    expect(merged.platform).toBe("darwin");
    expect(merged.native_runtime_supported).toBe(false);
    expect(merged.network).toEqual({ allow_network: false, egress: "off", contacted: false });
    expect(merged.environment?.state).toBe("ready");
    expect(merged.environment?.ready).toBe(true);

    expect(merged.provider).toBe("chatgpt");
    expect(merged.model).toBe("gpt-test");
    expect(merged.has_api_key).toBe(true);
    expect(merged.complete).toBe(false);
    expect(merged.outbound).toBe(2);
    expect(merged.active_id).toBe("p1");
    expect(merged.profiles).toEqual([{ id: "p1", name: "saved" }]);
  });
});
