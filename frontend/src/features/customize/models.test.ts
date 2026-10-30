import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defaultModel, defaultModelName, models } from "../../stores/customize";
import { resetStoreFields } from "../../stores/signal-field";
import { loadModels } from "./host";
import { currentId } from "../../stores/session";
import {
  readCapabilityReceipt,
  readEffectiveCapabilities,
  resetSessionModelState,
  sessionModelPin,
  watchSessionModelPin,
} from "./models";
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
  resetSessionModelState();
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

describe("the open session's model pin", () => {
  let stop: (() => void) | null = null;
  afterEach(() => {
    stop?.();
    stop = null;
  });

  function frames(answers: Record<string, () => Promise<Response>>) {
    fetchMock.mockImplementation((url: string) => {
      const match = /^\/api\/v1\/frames\/([^/]+)$/.exec(String(url));
      if (match && answers[match[1]!]) return answers[match[1]!]!();
      return Promise.resolve(url === "/api/v1/models" ? response(PAYLOAD) : response({}, 404));
    });
  }

  it("is read when a session opens, and again when another one does", async () => {
    frames({
      frame_1: () => Promise.resolve(response({ id: "frame_1", model_profile_id: "mp-claude", model_profile_revision: 3 })),
      frame_2: () => Promise.resolve(response({ id: "frame_2", model_profile_id: null, model_profile_revision: null })),
    });
    stop = watchSessionModelPin();
    currentId.value = "frame_1";
    await vi.waitFor(() =>
      expect(sessionModelPin.value).toEqual({ frameId: "frame_1", profileId: "mp-claude", revision: 3 }),
    );
    currentId.value = "frame_2";
    await vi.waitFor(() => expect(sessionModelPin.value).toEqual({ frameId: "frame_2", profileId: "", revision: 0 }));
    currentId.value = null;
    await vi.waitFor(() => expect(sessionModelPin.value).toBeNull());
  });

  it("drops a late answer for a session that is no longer open", async () => {
    let late: (value: Response) => void = () => {};
    frames({
      frame_1: () => new Promise<Response>((resolve) => (late = resolve)),
      frame_2: () => Promise.resolve(response({ id: "frame_2", model_profile_id: "mp-b", model_profile_revision: 1 })),
    });
    stop = watchSessionModelPin();
    currentId.value = "frame_1";
    currentId.value = "frame_2";
    await vi.waitFor(() => expect(sessionModelPin.value?.frameId).toBe("frame_2"));
    late(response({ id: "frame_1", model_profile_id: "mp-claude", model_profile_revision: 3 }));
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(sessionModelPin.value).toEqual({ frameId: "frame_2", profileId: "mp-b", revision: 1 });
  });

  it("is one watch however many times boot runs", async () => {
    frames({
      frame_7: () => Promise.resolve(response({ id: "frame_7", model_profile_id: "mp-claude", model_profile_revision: 2 })),
    });
    stop = watchSessionModelPin();
    expect(watchSessionModelPin()).toBe(stop);
    currentId.value = "frame_7";
    await vi.waitFor(() => expect(sessionModelPin.value?.frameId).toBe("frame_7"));
    expect(calls().filter(([url]) => url === "/api/v1/frames/frame_7")).toHaveLength(1);
  });

  it("an unreadable session leaves no unhandled rejection and shows nothing it did not read", async () => {
    frames({ frame_8: () => Promise.resolve(response({ error: "not found" }, 404)) });
    stop = watchSessionModelPin();
    currentId.value = "frame_8";
    await vi.waitFor(() => expect(calls()).toContainEqual(["/api/v1/frames/frame_8", "GET"]));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(sessionModelPin.value).toBeNull();
  });

  it("is wired at boot", async () => {
    frames({
      frame_9: () => Promise.resolve(response({ id: "frame_9", model_profile_id: "mp-claude", model_profile_revision: 2 })),
    });
    currentId.value = "frame_9";
    bootCustomize({});
    await vi.waitFor(() => expect(calls()).toContainEqual(["/api/v1/frames/frame_9", "GET"]));
    await vi.waitFor(() => expect(sessionModelPin.value?.profileId).toBe("mp-claude"));
  });
});

// M2 / P0-01: the badge shows two different facts -- what the last probe
// observed, and what the next request will actually use. The projection is an
// additive server field; without it the old display is unchanged.
describe("effective capability projection", () => {
  it("reads exactly the two B.3 fields with their provenance", () => {
    const effective = readEffectiveCapabilities({
      effective_capabilities: {
        tool_calling: { value: true, source: "probe" },
        streaming: { value: false, source: "catalog" },
      },
    });
    expect(effective).toEqual({
      tool_calling: { value: true, source: "probe" },
      streaming: { value: false, source: "catalog" },
    });
  });

  it("ignores the projection when an extra capability field appears", () => {
    expect(
      readEffectiveCapabilities({
        effective_capabilities: {
          tool_calling: { value: true, source: "probe" },
          streaming: { value: true, source: "probe" },
          vision: { value: true, source: "catalog" },
        },
      }),
    ).toBeNull();
  });

  it("ignores a projection that is not the B.3 top-level shape", () => {
    expect(
      readEffectiveCapabilities({
        capability_receipt: {
          native_tool_call: "true",
          streaming: "unknown",
          effective_capabilities: {
            tool_calling: { value: true, source: "probe" },
            streaming: { value: false, source: "catalog" },
          },
        },
      }),
    ).toBeNull();
    // The receipt-era spelling is not the contract field name.
    expect(
      readEffectiveCapabilities({
        effective_capabilities: {
          native_tool_call: { value: true, source: "probe" },
          streaming: { value: false, source: "catalog" },
        },
      }),
    ).toBeNull();
  });

  it("returns null for a missing field or an explicit null", () => {
    expect(readEffectiveCapabilities(null)).toBeNull();
    expect(readEffectiveCapabilities({})).toBeNull();
    expect(readEffectiveCapabilities({ effective_capabilities: null })).toBeNull();
  });

  it("ignores the whole projection when one entry is malformed", () => {
    const streaming = { value: false, source: "catalog" };
    const cases: unknown[] = [
      // extra key inside an entry
      { tool_calling: { value: true, source: "probe", extra: 1 }, streaming },
      // non-boolean value
      { tool_calling: { value: "true", source: "probe" }, streaming },
      // unknown source
      { tool_calling: { value: true, source: "guessed" }, streaming },
      // one of the two contract fields missing
      { tool_calling: { value: true, source: "probe" } },
    ];
    for (const effective_capabilities of cases) {
      expect(readEffectiveCapabilities({ effective_capabilities })).toBeNull();
    }
  });

  it("keeps the probe observation time on the receipt", () => {
    const receipt = readCapabilityReceipt({
      native_tool_call: "true",
      streaming: "unknown",
      observed_at: 1761177600000,
    });
    expect(receipt?.observed_at).toBe(1761177600000);
  });
});
