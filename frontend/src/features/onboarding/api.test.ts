import { afterEach, describe, expect, it, vi } from "vitest";
import { activateExistingModelProfile } from "./api";

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
