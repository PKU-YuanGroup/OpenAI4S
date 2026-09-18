import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defaultModel, defaultModelName, models } from "../../stores/customize";
import { resetStoreFields } from "../../stores/signal-field";
import { loadModels } from "./host";
import { bootCustomize } from "./index";

const fetchMock = vi.fn();
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

const PAYLOAD = {
  models: {
    default: [
      { id: "doubao-seed-2.0-pro", name: "doubao-seed-2.0-pro", description: "ark (current)" },
      {
        id: "mp-claude",
        name: "Claude profile",
        description: "claude · claude-sonnet-4-5",
        profile_id: "mp-claude",
        model: "claude-sonnet-4-5",
      },
    ],
  },
  default_model_id: "doubao-seed-2.0-pro",
};

function calls(): Array<[string, string]> {
  return fetchMock.mock.calls.map(([url, init]) => [String(url), String((init as RequestInit | undefined)?.method || "GET")]);
}

beforeEach(() => {
  resetStoreFields();
  fetchMock.mockReset().mockImplementation((url: string) =>
    Promise.resolve(url === "/api/v1/models" ? response(PAYLOAD) : response({}, 404)),
  );
  vi.stubGlobal("fetch", fetchMock);
  // A browser window with nothing bridged onto it -- the workbench as it boots.
  vi.stubGlobal("window", globalThis);
});
afterEach(() => {
  vi.unstubAllGlobals();
  resetStoreFields();
});

describe("composer model loading", () => {
  it("loadModels fetches /models and fills the selector stores", async () => {
    await loadModels();
    expect(calls()).toContainEqual(["/api/v1/models", "GET"]);
    expect((models.value as Array<{ id: string }>).map((m) => m.id)).toEqual([
      "doubao-seed-2.0-pro",
      "mp-claude",
    ]);
    expect(defaultModel.value).toBe("doubao-seed-2.0-pro");
    expect(defaultModelName.value).toBe("doubao-seed-2.0-pro");
  });

  it("names a profile entry by its model, not its profile id", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(response({ ...PAYLOAD, default_model_id: "mp-claude" })),
    );
    await loadModels();
    expect(defaultModel.value).toBe("mp-claude");
    // `frames.model` is display-only and must not store a profile id.
    expect(defaultModelName.value).toBe("claude-sonnet-4-5");
  });

  it("keeps model ids verbatim, including ones shaped like a credential prefix", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(
        response({
          models: { default: [{ id: "ark-code-latest", name: "ark-code-latest", description: "ark" }] },
          default_model_id: "ark-code-latest",
        }),
      ),
    );
    await loadModels();
    expect(defaultModel.value).toBe("ark-code-latest");
    expect(defaultModelName.value).toBe("ark-code-latest");
  });

  it("falls back to the first entry -- the daemon's live model -- when the default is not listed", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(response({ ...PAYLOAD, default_model_id: "mp-deleted-profile" })),
    );
    await loadModels();
    expect(defaultModel.value).toBe("doubao-seed-2.0-pro");
    // Never the unlisted id: it would be sent as `model` on session creation.
    expect(defaultModelName.value).toBe("doubao-seed-2.0-pro");
  });

  it("an unreadable /models leaves an empty list instead of throwing", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(response({ error: "down" }, 503)));
    await expect(loadModels()).resolves.toBeUndefined();
    expect(models.value).toEqual([]);
  });

  it("is wired at boot", async () => {
    bootCustomize({});
    await vi.waitFor(() => expect(calls()).toContainEqual(["/api/v1/models", "GET"]));
    await vi.waitFor(() => expect((models.value as unknown[]).length).toBe(2));
  });
});
