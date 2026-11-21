import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defaultModel, defaultModelName, models } from "../../stores/customize";
import { resetStoreFields } from "../../stores/signal-field";
import {
  chooseComposerModel,
  loadModels,
  loadSessionModelPin,
  modelT,
  composerChoiceMark,
  noteAdmittedModelBinding,
  resetSessionModelState,
  sessionModelPin,
} from "../../features/customize/models";
import { currentId } from "../../stores/session";
import { running } from "../../stores/stream";
import { ModelKeyNotice, ModelSelect } from "./ModelSelect";

const openCustomize = vi.hoisted(() => vi.fn(() => Promise.resolve()));
vi.mock("../../features/customize", () => ({ openCustomize }));

type Node = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };
const fetchMock = vi.fn();
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function options(tree: Node): Node[] {
  const kids = tree.props?.children;
  return (Array.isArray(kids) ? kids : [kids]).filter(
    (node): node is Node => !!node && typeof node === "object" && (node as Node).type === "option",
  );
}

beforeEach(() => {
  resetStoreFields();
  resetSessionModelState();
  let savedDefault = "doubao-seed-2.0-pro";
  fetchMock.mockReset().mockImplementation((url: string, init?: RequestInit) => {
    if (url === "/api/v1/models") {
      return Promise.resolve(
        response({
          models: {
            default: [
              { id: "doubao-seed-2.0-pro", name: "doubao-seed-2.0-pro" },
              { id: "mp-claude", name: "Claude", model: "claude-sonnet-4-5" },
            ],
          },
          default_model_id: savedDefault,
        }),
      );
    }
    if (url === "/api/v1/models/default" && init?.method === "PUT") {
      savedDefault = String(JSON.parse(String(init.body)).model_id);
      return Promise.resolve(response({ default_model_id: savedDefault }));
    }
    return Promise.resolve(response({}, 404));
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  resetStoreFields();
});

describe("#model-select", () => {
  it("renders one option per configured model, the default selected", async () => {
    await loadModels();
    const tree = ModelSelect() as Node;
    expect(tree.type).toBe("select");
    expect(tree.props?.id).toBe("model-select");
    expect(options(tree).map((node) => node.props?.value)).toEqual(["doubao-seed-2.0-pro", "mp-claude"]);
    expect(tree.props?.value).toBe("doubao-seed-2.0-pro");
  });

  it("renders a single empty option before anything is configured", () => {
    const tree = ModelSelect() as Node;
    expect(options(tree)).toHaveLength(1);
    expect(options(tree)[0]?.props?.value).toBe("");
  });

  it("choosing an entry sets it as the server default and names it by model", async () => {
    await loadModels();
    const tree = ModelSelect() as Node;
    const onChange = tree.props?.onChange as (event: unknown) => void;
    onChange({ currentTarget: { value: "mp-claude" } });
    await vi.waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/models/default",
        expect.objectContaining({ method: "PUT", body: JSON.stringify({ model_id: "mp-claude" }) }),
      ),
    );
    expect(defaultModel.value).toBe("mp-claude");
    expect(defaultModelName.value).toBe("claude-sonnet-4-5");
    expect((ModelSelect() as Node).props?.value).toBe("mp-claude");
  });

  it("a refused choice puts the previous selection back", async () => {
    await loadModels();
    fetchMock.mockImplementation(() => Promise.resolve(response({ error: "no" }, 409)));
    const onChange = (ModelSelect() as Node).props?.onChange as (event: unknown) => void;
    onChange({ currentTarget: { value: "mp-claude" } });
    await vi.waitFor(() => expect(defaultModel.value).toBe("doubao-seed-2.0-pro"));
    expect(defaultModelName.value).toBe("doubao-seed-2.0-pro");
    expect((models.value as unknown[]).length).toBe(2);
  });
});

describe("#model-select with a session open", () => {
  const hint = vi.fn();
  const openCust = vi.fn();
  const sent = () =>
    fetchMock.mock.calls
      .filter(([, init]) => (init as RequestInit | undefined)?.method && (init as RequestInit).method !== "GET")
      .map(([url, init]) => [String(url), (init as RequestInit).method, (init as RequestInit).body]);
  // The daemon's default as `GET /models` reports it: a successful PUT moves it,
  // because the selector reloads the list after setting the default.
  let serverDefault = "doubao-seed-2.0-pro";
  const route = (binding: () => Response, setDefault: () => Response = () => response({ default_model_id: "mp-claude" })) =>
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/v1/models") {
        return Promise.resolve(
          response({
            models: {
              default: [
                { id: "doubao-seed-2.0-pro", name: "doubao-seed-2.0-pro" },
                { id: "mp-claude", name: "Claude", model: "claude-sonnet-4-5", revision: 4 },
              ],
            },
            default_model_id: serverDefault,
          }),
        );
      }
      if (url === "/api/v1/frames/frame_1/model-binding" && init?.method === "POST") return Promise.resolve(binding());
      if (url === "/api/v1/models/default" && init?.method === "PUT") {
        const answer = setDefault();
        if (answer.ok) serverDefault = String(JSON.parse(String(init.body)).model_id);
        return Promise.resolve(answer);
      }
      return Promise.resolve(response({}, 404));
    });
  const choose = (value: string) =>
    ((ModelSelect() as Node).props?.onChange as (event: unknown) => void)({ currentTarget: { value } });

  beforeEach(async () => {
    serverDefault = "doubao-seed-2.0-pro";
    hint.mockReset();
    openCust.mockReset();
    vi.stubGlobal("window", { hint, openCust });
    await loadModels();
    currentId.value = "frame_1";
    sessionModelPin.value = { frameId: "frame_1", profileId: "doubao-seed-2.0-pro", revision: 1 };
  });

  it("shows the session's own pin, not the server default", () => {
    sessionModelPin.value = { frameId: "frame_1", profileId: "mp-claude", revision: 4 };
    expect(defaultModel.value).toBe("doubao-seed-2.0-pro");
    expect((ModelSelect() as Node).props?.value).toBe("mp-claude");
  });

  it("shows the default for a session that is not pinned yet, or a pin for another session", () => {
    sessionModelPin.value = { frameId: "frame_1", profileId: "", revision: 0 };
    expect((ModelSelect() as Node).props?.value).toBe("doubao-seed-2.0-pro");
    sessionModelPin.value = { frameId: "frame_other", profileId: "mp-claude", revision: 4 };
    expect((ModelSelect() as Node).props?.value).toBe("doubao-seed-2.0-pro");
  });

  it("says so when the session is pinned to a model the list no longer offers", () => {
    sessionModelPin.value = { frameId: "frame_1", profileId: "mp-deleted", revision: 2 };
    const tree = ModelSelect() as Node;
    expect(tree.props?.value).toBe("");
    const first = options(tree)[0];
    expect(first?.props?.value).toBe("");
    expect(first?.props?.disabled).toBe(true);
    expect(options(tree).map((node) => node.props?.value)).toEqual(["", "doubao-seed-2.0-pro", "mp-claude"]);
  });

  it("re-pins the open session first, then makes the choice the default", async () => {
    route(() => response({ ok: true, binding: { model_profile_id: "mp-claude", model_profile_revision: 4, bound: true } }));
    choose("mp-claude");
    await vi.waitFor(() => expect(defaultModel.value).toBe("mp-claude"));

    expect(sent()).toEqual([
      ["/api/v1/frames/frame_1/model-binding", "POST", JSON.stringify({ model_id: "mp-claude" })],
      ["/api/v1/models/default", "PUT", JSON.stringify({ model_id: "mp-claude" })],
    ]);
    expect(sessionModelPin.value).toEqual({ frameId: "frame_1", profileId: "mp-claude", revision: 4 });
    expect((ModelSelect() as Node).props?.value).toBe("mp-claude");
    expect(hint).toHaveBeenCalledWith(expect.stringContaining("Claude"), false);
    // Activating a profile rewrites the live entry, so the list is reloaded
    // after the default is set rather than left naming a model the next pick
    // of that entry would be refused for.
    const reads = fetchMock.mock.calls.map(([url, init]) => [String(url), (init as RequestInit | undefined)?.method || "GET"]);
    const put = reads.findIndex(([url]) => url === "/api/v1/models/default");
    expect(reads.slice(put + 1)).toContainEqual(["/api/v1/models", "GET"]);
  });

  it("says the switch applies from the next turn while one is running", async () => {
    route(() => response({ ok: true, binding: { model_profile_id: "mp-claude", model_profile_revision: 4, bound: true } }));
    running.value = true;
    choose("mp-claude");
    await vi.waitFor(() => expect(hint).toHaveBeenCalled());
    expect(hint.mock.calls[0]?.[0]).toBe(modelT("model.session.switchedNextTurn", "Claude"));

    running.value = false;
    hint.mockReset();
    sessionModelPin.value = { frameId: "frame_1", profileId: "doubao-seed-2.0-pro", revision: 1 };
    choose("mp-claude");
    await vi.waitFor(() => expect(hint).toHaveBeenCalled());
    expect(hint.mock.calls[0]?.[0]).toBe(modelT("model.session.switched", "Claude"));
  });

  it("a refused re-pin changes nothing -- not the session, not the default", async () => {
    route(() =>
      response(
        { error: "model profile 'Claude' has no API key", code: "model_profile_needs_key" },
        409,
      ),
    );
    choose("mp-claude");
    await vi.waitFor(() => expect(hint).toHaveBeenCalledWith(expect.stringContaining("no API key"), true));

    expect(sent().map(([url]) => url)).toEqual(["/api/v1/frames/frame_1/model-binding"]);
    // What it reverted to was a guess; the server is asked what the pin is.
    await vi.waitFor(() =>
      expect(fetchMock.mock.calls.map(([url]) => String(url))).toContain("/api/v1/frames/frame_1"),
    );
    expect(sessionModelPin.value).toEqual({ frameId: "frame_1", profileId: "doubao-seed-2.0-pro", revision: 1 });
    expect(defaultModel.value).toBe("doubao-seed-2.0-pro");
    expect((ModelSelect() as Node).props?.value).toBe("doubao-seed-2.0-pro");
    expect(openCust).toHaveBeenCalledWith("models");
  });

  it("a refused default (team mode: admin only) keeps the session's switch", async () => {
    route(
      () => response({ ok: true, binding: { model_profile_id: "mp-claude", model_profile_revision: 4, bound: true } }),
      () => response({ error: "admin only", code: "admin_only" }, 403),
    );
    choose("mp-claude");
    await vi.waitFor(() => expect(sent()).toHaveLength(2));
    await vi.waitFor(() => expect(defaultModel.value).toBe("doubao-seed-2.0-pro"));

    expect(sessionModelPin.value?.profileId).toBe("mp-claude");
    expect((ModelSelect() as Node).props?.value).toBe("mp-claude");
    expect(hint).not.toHaveBeenCalledWith(expect.anything(), true);
  });

  it("an answer for a session the user has left does not land on the one now open", async () => {
    let answer: (value: Response) => void = () => {};
    route(() => response({}));
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    );
    choose("mp-claude");
    await vi.waitFor(() => expect(sent().map(([url]) => url)).toContain("/api/v1/frames/frame_1/model-binding"));
    currentId.value = "frame_2";
    sessionModelPin.value = { frameId: "frame_2", profileId: "doubao-seed-2.0-pro", revision: 1 };
    answer(response({ ok: true, binding: { model_profile_id: "mp-claude", model_profile_revision: 4, bound: true } }));
    // The answer has been handled once the default is sent on after it.
    await vi.waitFor(() => expect(sent().map(([url]) => url)).toContain("/api/v1/models/default"));

    expect(sessionModelPin.value).toEqual({ frameId: "frame_2", profileId: "doubao-seed-2.0-pro", revision: 1 });
    expect(hint).not.toHaveBeenCalled();
  });

  it("with no session open, it only sets the default", async () => {
    currentId.value = null;
    route(() => response({}, 500));
    choose("mp-claude");
    await vi.waitFor(() => expect(defaultModel.value).toBe("mp-claude"));
    expect(sent().map(([url]) => url)).toEqual(["/api/v1/models/default"]);
  });

  it("a session pinned to an earlier revision of a listed profile says so, so choosing it re-pins", async () => {
    route(() => response({ ok: true, binding: { model_profile_id: "mp-claude", model_profile_revision: 7, bound: true } }));
    models.value = (models.value as Array<Record<string, unknown>>).map((entry) =>
      entry.id === "mp-claude" ? { ...entry, revision: 7 } : entry,
    );
    sessionModelPin.value = { frameId: "frame_1", profileId: "mp-claude", revision: 6 };
    const tree = ModelSelect() as Node;
    expect(tree.props?.value).toBe("");
    expect(options(tree)[0]?.props?.disabled).toBe(true);
    expect(String(options(tree)[0]?.props?.children)).toContain("Claude");

    choose("mp-claude");
    await vi.waitFor(() => expect(sessionModelPin.value?.revision).toBe(7));
    expect((ModelSelect() as Node).props?.value).toBe("mp-claude");
  });

  it("two quick choices go out in order, and only the latest finishes", async () => {
    const answers: Array<(value: Response) => void> = [];
    route(() => response({}));
    const routed = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: string, init?: RequestInit) =>
      String(url).endsWith("/model-binding")
        ? new Promise<Response>((resolve) => answers.push(resolve))
        : routed(url, init),
    );
    choose("mp-claude");
    choose("doubao-seed-2.0-pro");
    await vi.waitFor(() => expect(answers).toHaveLength(1));
    // The second re-pin waits for the first: sent together, the server could
    // apply them in either order and stay pinned to the first.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(answers).toHaveLength(1);
    answers[0]!(response({ ok: true, binding: { model_profile_id: "mp-claude", model_profile_revision: 4, bound: true } }));
    await vi.waitFor(() => expect(answers).toHaveLength(2));
    answers[1]!(response({ ok: true, binding: { model_profile_id: "", model_profile_revision: 0, bound: false } }));
    await vi.waitFor(() => expect(sent().filter(([url]) => url === "/api/v1/models/default")).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(sent().map(([url, , body]) => [url, body])).toEqual([
      ["/api/v1/frames/frame_1/model-binding", JSON.stringify({ model_id: "mp-claude" })],
      ["/api/v1/frames/frame_1/model-binding", JSON.stringify({ model_id: "doubao-seed-2.0-pro" })],
      ["/api/v1/models/default", JSON.stringify({ model_id: "doubao-seed-2.0-pro" })],
    ]);
    expect(sessionModelPin.value).toEqual({ frameId: "frame_1", profileId: "", revision: 0 });
  });

  it("a refusal for a choice already superseded changes nothing", async () => {
    const answers: Array<(value: Response) => void> = [];
    route(() => response({}));
    const routed = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: string, init?: RequestInit) =>
      String(url).endsWith("/model-binding")
        ? new Promise<Response>((resolve) => answers.push(resolve))
        : routed(url, init),
    );
    choose("mp-claude");
    choose("doubao-seed-2.0-pro");
    await vi.waitFor(() => expect(answers).toHaveLength(1));
    answers[0]!(response({ error: "no key", code: "model_profile_needs_key" }, 409));
    await vi.waitFor(() => expect(answers).toHaveLength(2));
    // The refused first choice neither reverted the optimistic second one nor spoke.
    expect(sessionModelPin.value?.profileId).toBe("doubao-seed-2.0-pro");
    expect(hint).not.toHaveBeenCalled();
    expect(openCust).not.toHaveBeenCalled();
    answers[1]!(response({ ok: true, binding: { model_profile_id: "", model_profile_revision: 0, bound: false } }));
    await vi.waitFor(() => expect(sent().map(([url]) => url)).toContain("/api/v1/models/default"));
  });

  it("does not overtake a re-pin that takes more than fifteen seconds", async () => {
    vi.useFakeTimers();
    try {
      const answers: Array<(value: Response) => void> = [];
      route(() => response({}));
      const routed = fetchMock.getMockImplementation()!;
      fetchMock.mockImplementation((url: string, init?: RequestInit) =>
        url.endsWith("/model-binding")
          ? new Promise<Response>((resolve) => answers.push(resolve))
          : routed(url, init),
      );
      const first = chooseComposerModel("mp-claude");
      const second = chooseComposerModel("doubao-seed-2.0-pro");
      await vi.advanceTimersByTimeAsync(16_000);
      expect(answers).toHaveLength(1);
      answers[0]!(response({ binding: { model_profile_id: "mp-claude", model_profile_revision: 4 } }));
      await first;
      await vi.advanceTimersByTimeAsync(0);
      expect(answers).toHaveLength(2);
      answers[1]!(response({ binding: { model_profile_id: "", model_profile_revision: 0 } }));
      await second;
    } finally {
      vi.useRealTimers();
    }
  });

  it("a refusal for a session the user has left leaves the open one alone", async () => {
    let refuse: (value: Response) => void = () => {};
    route(() => response({}));
    const routed = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: string, init?: RequestInit) =>
      String(url).endsWith("/model-binding")
        ? new Promise<Response>((resolve) => (refuse = resolve))
        : routed(url, init),
    );
    choose("mp-claude");
    await vi.waitFor(() => expect(sent().map(([url]) => url)).toContain("/api/v1/frames/frame_1/model-binding"));
    currentId.value = "frame_2";
    sessionModelPin.value = { frameId: "frame_2", profileId: "mp-claude", revision: 4 };
    refuse(response({ error: "no key", code: "model_profile_needs_key" }, 409));
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(sessionModelPin.value).toEqual({ frameId: "frame_2", profileId: "mp-claude", revision: 4 });
    expect(hint).not.toHaveBeenCalled();
    expect(openCust).not.toHaveBeenCalled();
    expect(sent().map(([url]) => url)).not.toContain("/api/v1/models/default");
  });

  it("a choice from a list that has changed reloads the list", async () => {
    route(() => response({ error: "the model list has changed", code: "model_selection_stale" }, 409));
    const before = fetchMock.mock.calls.filter(([url]) => url === "/api/v1/models").length;
    choose("doubao-seed-2.0-pro");
    await vi.waitFor(() =>
      expect(fetchMock.mock.calls.filter(([url]) => url === "/api/v1/models").length).toBe(before + 1),
    );
    expect(openCust).not.toHaveBeenCalled();
  });

  it("a slow read of the session's pin does not undo the user's switch", async () => {
    let late: (value: Response) => void = () => {};
    route(() => response({ ok: true, binding: { model_profile_id: "mp-claude", model_profile_revision: 4, bound: true } }));
    const routed = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: string, init?: RequestInit) =>
      url === "/api/v1/frames/frame_1" ? new Promise<Response>((resolve) => (late = resolve)) : routed(url, init),
    );
    const reading = loadSessionModelPin("frame_1");
    choose("mp-claude");
    await vi.waitFor(() => expect(sessionModelPin.value?.revision).toBe(4));
    late(response({ id: "frame_1", model_profile_id: "doubao-seed-2.0-pro", model_profile_revision: 1 }));
    await reading;
    expect(sessionModelPin.value).toEqual({ frameId: "frame_1", profileId: "mp-claude", revision: 4 });
  });
});

describe("#model-select keeps up with the session and the list", () => {
  beforeEach(async () => {
    vi.stubGlobal("window", { hint: vi.fn() });
    await loadModels();
    currentId.value = "frame_1";
  });

  it("a send's admitted pair is taken unless a model was chosen after the send started", async () => {
    const mark = composerChoiceMark();
    noteAdmittedModelBinding("frame_1", { model_profile_id: "mp-claude", model_profile_revision: 4 }, mark);
    expect(sessionModelPin.value).toEqual({ frameId: "frame_1", profileId: "mp-claude", revision: 4 });
    // A choice made while that send was in flight is newer than its 202.
    const later = composerChoiceMark();
    fetchMock.mockImplementation(() =>
      Promise.resolve(response({ ok: true, binding: { model_profile_id: "", model_profile_revision: 0, bound: false } })),
    );
    await chooseComposerModel("doubao-seed-2.0-pro");
    noteAdmittedModelBinding("frame_1", { model_profile_id: "mp-claude", model_profile_revision: 4 }, later);
    expect(sessionModelPin.value).toEqual({ frameId: "frame_1", profileId: "", revision: 0 });
  });

  it("a pin the list does not know reloads the list once before calling it unavailable", async () => {
    const lists = () => fetchMock.mock.calls.filter(([url]) => url === "/api/v1/models").length;
    const before = lists();
    noteAdmittedModelBinding("frame_1", { model_profile_id: "mp-made-in-another-tab", model_profile_revision: 1 }, composerChoiceMark());
    await vi.waitFor(() => expect(lists()).toBe(before + 1));
    noteAdmittedModelBinding("frame_1", { model_profile_id: "mp-made-in-another-tab", model_profile_revision: 1 }, composerChoiceMark());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(lists()).toBe(before + 1);
  });

  it("a pin newer than the list's entry is the entry, not an earlier configuration", async () => {
    models.value = (models.value as Array<Record<string, unknown>>).map((entry) =>
      entry.id === "mp-claude" ? { ...entry, revision: 3 } : entry,
    );
    const lists = () => fetchMock.mock.calls.filter(([url]) => url === "/api/v1/models").length;
    const before = lists();
    sessionModelPin.value = { frameId: "frame_1", profileId: "mp-claude", revision: 5 };
    expect((ModelSelect() as Node).props?.value).toBe("mp-claude");
    currentId.value = "frame_2";
    noteAdmittedModelBinding("frame_2", { model_profile_id: "mp-claude", model_profile_revision: 5 }, composerChoiceMark());
    await vi.waitFor(() => expect(lists()).toBe(before + 1));
  });

  it("an older choice's refused default does not undo a newer choice", async () => {
    currentId.value = null;
    let refuse: (value: Response) => void = () => {};
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/v1/models/default" && init?.method === "PUT") {
        const body = String(init.body);
        return body.includes("mp-claude")
          ? new Promise<Response>((resolve) => (refuse = resolve))
          : Promise.resolve(response({ default_model_id: "mp-third" }));
      }
      return Promise.resolve(response({}, 404));
    });
    // Three distinct values, or a rollback to the first would look like the second.
    models.value = [...(models.value as unknown[]), { id: "mp-third", name: "Third", description: "", model: "m3", revision: 1 }];
    expect(defaultModel.value).toBe("doubao-seed-2.0-pro");
    const first = chooseComposerModel("mp-claude");
    const second = chooseComposerModel("mp-third");
    await vi.waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(1));
    expect(defaultModel.value).toBe("mp-third");
    refuse(response({ error: "no" }, 409));
    await first;
    await second;
    expect(defaultModel.value).toBe("mp-third");
  });
});

describe("#model-select with no session open", () => {
  it("persists quick default choices in order", async () => {
    const answers: Array<() => void> = [];
    let savedDefault = "doubao-seed-2.0-pro";
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith("/default")) {
        const chosen = String(JSON.parse(String(init?.body)).model_id);
        return new Promise<Response>((resolve) => answers.push(() => {
          savedDefault = chosen;
          resolve(response({ default_model_id: chosen }));
        }));
      }
      return Promise.resolve(response({
        models: { default: [{ id: "alpha" }, { id: "beta" }] },
        default_model_id: savedDefault,
      }));
    });
    const first = chooseComposerModel("alpha");
    const second = chooseComposerModel("beta");
    await vi.waitFor(() => expect(answers).toHaveLength(1));
    answers[0]!();
    await first;
    await vi.waitFor(() => expect(answers).toHaveLength(2));
    answers[1]!();
    await second;
    expect(savedDefault).toBe("beta");
    expect(defaultModel.value).toBe("beta");
  });

  it("ignores a model list read before a newer choice", async () => {
    await loadModels();
    const routed = fetchMock.getMockImplementation()!;
    let stale: (value: Response) => void = () => {};
    fetchMock.mockImplementationOnce(() => new Promise<Response>((resolve) => { stale = resolve; }));
    const reading = loadModels();
    fetchMock.mockImplementation(routed);
    await chooseComposerModel("mp-claude");
    stale(response({ models: { default: [{ id: "old" }] }, default_model_id: "old" }));
    await reading;
    expect(defaultModel.value).toBe("mp-claude");
    expect((models.value as Array<{ id: string }>).some((entry) => entry.id === "mp-claude")).toBe(true);
  });

  it("tells a member that the default is an admin's", async () => {
    const hint = vi.fn();
    vi.stubGlobal("window", { hint });
    await loadModels();
    fetchMock.mockImplementation((url: string, init?: RequestInit) =>
      Promise.resolve(
        url === "/api/v1/models/default" && init?.method === "PUT"
          ? response({ error: "admin only", code: "admin_only" }, 403)
          : response({}, 404),
      ),
    );
    ((ModelSelect() as Node).props?.onChange as (event: unknown) => void)({ currentTarget: { value: "mp-claude" } });
    await vi.waitFor(() => expect(hint).toHaveBeenCalledWith(modelT("model.default.adminOnly"), false));
    expect(defaultModel.value).toBe("doubao-seed-2.0-pro");
  });

  it("says so beside the selector when no API key is configured", async () => {
    // A daemon nobody configured still lists its default model, which read
    // as "ready" until the first send failed (usability review FR-1).
    await loadModels();
    expect(ModelKeyNotice()).toBeNull();

    fetchMock.mockImplementation(() =>
      Promise.resolve(
        response({
          models: { default: [{ id: "doubao-seed-2.0-pro", name: "doubao-seed-2.0-pro" }] },
          default_model_id: "doubao-seed-2.0-pro",
          credential_missing: true,
        }),
      ),
    );
    await loadModels();
    const notice = ModelKeyNotice() as Node;
    expect(notice.type).toBe("button");
    expect(notice.props?.children).toBe(modelT("model.nokey"));
    expect(notice.props?.title).toBe(modelT("model.nokey.title"));
    (notice.props?.onClick as () => void)();
    expect(openCustomize).toHaveBeenCalledWith("models");
    // The selector itself is unchanged: it still names the default model.
    expect((ModelSelect() as Node).props?.value).toBe("doubao-seed-2.0-pro");
  });
});
