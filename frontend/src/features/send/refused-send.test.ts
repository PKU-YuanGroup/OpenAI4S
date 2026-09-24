/**
 * A send the server refuses before admission. `POST /frames/{id}/message`
 * answers 409 `model_profile_needs_key` / `model_revision_unavailable` /
 * `model_revision_ambiguous` synchronously, and nothing is stored for it -- no
 * user row, no job. send() had already cleared the composer, restored the draft
 * only for environment-readiness errors, and let turnDone("failed") paint
 * "This turn failed. Please try again." over the server's actionable message in
 * the same tick. The text was gone and the reason with it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const wsMock = vi.hoisted(() => ({ sub: vi.fn(), unsub: vi.fn() }));
const loadMock = vi.hoisted(() => ({ loadSessions: vi.fn(async () => {}) }));
vi.mock("../ws/connect", () => wsMock);
vi.mock("../sessions/load", () => loadMock);

import { t } from "../../i18n/runtime";
import { _openGen, currentId, project } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { running } from "../../stores/stream";
import { UPLOAD_STATE } from "../chrome/upload";
import { rebindConfirmText, rebindDoneText, send } from "./send";
import { closeTurnTicket } from "./ticket";

type FakeEl = Record<string, unknown> & {
  classList: { add: (name: string) => void; remove: () => void; toggle: () => void; contains: () => boolean };
  /** Every class name `classList.add` was called with (contains() stays inert). */
  added: string[];
  value: string;
  children: unknown[];
  textContent: string;
  removed: boolean;
};

function fakeEl(): FakeEl {
  const added: string[] = [];
  const node: FakeEl = {
    classList: {
      add: (name: string) => void added.push(name),
      remove: () => {},
      toggle: () => {},
      contains: () => false,
    },
    added,
    value: "",
    children: [],
    dataset: {},
    style: {},
    innerHTML: "",
    textContent: "",
    removed: false,
    appendChild(child: unknown) {
      node.children.push(child);
      return child;
    },
    remove() {
      node.removed = true;
    },
    focus() {},
    setAttribute() {},
    removeAttribute() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    scrollTo() {},
  };
  return node;
}

const NEEDS_KEY =
  "model profile 'claude-nokey' has no API key: add one in Customize -> Models, " +
  "set OPENAI4S_CLAUDE_API_KEY for the daemon, or activate another profile";

function refusal(code: string, error: string) {
  return { status: 409, body: { error, code, status: 409, request_id: "r1" } };
}

describe("send(): a message the server refuses before admission", () => {
  const nodes: Record<string, FakeEl> = {};
  const openCust = vi.fn();
  let routes: Record<string, { status: number; body: unknown }> = {};

  function lastHint(): string {
    const spans = nodes["composer-hint"]!.children as FakeEl[];
    const last = spans[spans.length - 1];
    return last ? String(last.textContent) : "";
  }

  function userBubble(): FakeEl | undefined {
    return (nodes.messages!.children as FakeEl[])[0];
  }

  beforeEach(() => {
    resetStoreFields();
    UPLOAD_STATE.pending.clear();
    UPLOAD_STATE.failures.clear();
    UPLOAD_STATE.creations.clear();
    openCust.mockReset();
    routes = {};
    for (const id of ["composer", "messages", "composer-hint", "cancel-btn", "send-btn", "workspace"]) {
      nodes[id] = fakeEl();
    }
    nodes.composer!.value = "hello";
    vi.stubGlobal("document", {
      querySelector: (sel: string) => nodes[sel.replace(/^#/, "")] ?? null,
      querySelectorAll: () => [],
      getElementById: (id: string) => nodes[id] ?? null,
      createElement: () => fakeEl(),
      createElementNS: () => fakeEl(),
      createTextNode: (text: string) => ({ text }),
      documentElement: fakeEl(),
      body: fakeEl(),
    });
    vi.stubGlobal("window", {});
    vi.stubGlobal("openCust", openCust);
    vi.stubGlobal("fetch", (url: string) => {
      const path = String(url).replace("/api/v1", "");
      const route = routes[path] ?? { status: 200, body: {} };
      return Promise.resolve({
        ok: route.status < 400,
        status: route.status,
        text: () => Promise.resolve(JSON.stringify(route.body)),
      });
    });
    currentId.value = "frame_1";
    project.value = "proj_1";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps the text and shows the server's reason for model_profile_needs_key", async () => {
    routes["/frames/frame_1/message"] = refusal("model_profile_needs_key", NEEDS_KEY);
    await send("hello");

    expect(nodes.composer!.value).toBe("hello");
    expect(lastHint()).toContain("no API key");
    expect(lastHint()).not.toContain(t("turn.failed"));
    expect(running.value).toBe(false);
    // Nothing was stored for it, so the optimistic bubble does not stay either.
    expect(userBubble()?.removed).toBe(true);
    expect(openCust).toHaveBeenCalledWith("models");
  });

  it("keeps the text when the rebind prompt for model_revision_unavailable is dismissed", async () => {
    routes["/frames/frame_1/message"] = refusal(
      "model_revision_unavailable",
      "this session is pinned to a model configuration that is no longer usable; rebind it to continue",
    );
    vi.stubGlobal("confirm", () => false);
    await send("hello");

    expect(nodes.composer!.value).toBe("hello");
    expect(lastHint()).toContain("rebind it to continue");
    expect(running.value).toBe(false);
    expect(openCust).not.toHaveBeenCalled();
  });

  it("asks about a moved profile without claiming the pinned configuration no longer exists", async () => {
    // gateway._unusable_pin_error for a credential scope mismatch (SEC-2): the
    // profile still exists, it now names a different provider or endpoint.
    routes["/frames/frame_1/message"] = refusal(
      "model_revision_unavailable",
      "this session is pinned to an earlier configuration of its model profile, and the profile now names a " +
        "different provider or endpoint; its credential is not sent to the old one. Rebind the session to continue",
    );
    const asked: string[] = [];
    vi.stubGlobal("confirm", (text: string) => {
      asked.push(text);
      return false;
    });
    await send("hello");

    expect(asked).toHaveLength(1);
    expect(asked[0]).not.toBe(t("model.rebind.confirm"));
    expect(asked[0]).not.toMatch(/no longer exists/);
    expect(asked[0]).toMatch(/provider or endpoint/);
    expect(nodes.composer!.value).toBe("hello");
  });

  it("keeps the text after a confirmed rebind, and says what the rebind actually did", async () => {
    routes["/frames/frame_1/message"] = refusal("model_revision_ambiguous", "more than one model profile matches");
    routes["/frames/frame_1/model-binding"] = {
      status: 200,
      body: { ok: true, binding: { model_profile_id: "", model_profile_revision: 0, bound: false } },
    };
    vi.stubGlobal("confirm", () => true);
    await send("hello");

    expect(nodes.composer!.value).toBe("hello");
    expect(lastHint()).toBe(rebindDoneText({ binding: { bound: false } }));
    expect(lastHint()).not.toBe(t("model.rebind.done"));
    expect(running.value).toBe(false);
  });

  it("shows the rebind's own refusal and opens Models for model_profile_needs_active", async () => {
    routes["/frames/frame_1/message"] = refusal("model_revision_unavailable", "no longer usable; rebind it");
    routes["/frames/frame_1/model-binding"] = refusal(
      "model_profile_needs_active",
      "no model profile is active and more than one matches 'gpt-4o'; activate the one this session continues under in Customize -> Models, then send again",
    );
    vi.stubGlobal("confirm", () => true);
    await send("hello");

    expect(nodes.composer!.value).toBe("hello");
    expect(lastHint()).toContain("no model profile is active");
    expect(openCust).toHaveBeenCalledWith("models");
  });

  it("does not put the text back over something typed while the request was out", async () => {
    let answer: (value: unknown) => void = () => {};
    vi.stubGlobal(
      "fetch",
      () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    );
    const done = send("hello");
    for (let i = 0; i < 10; i++) await Promise.resolve();
    nodes.composer!.value = "a new draft";
    answer({
      ok: false,
      status: 409,
      text: () => Promise.resolve(JSON.stringify(refusal("model_profile_needs_key", NEEDS_KEY).body)),
    });
    await done;
    expect(nodes.composer!.value).toBe("a new draft");
    // The refused text could not go back into the composer, so the bubble is
    // the only place left that holds it: it stays, marked as not sent.
    expect(userBubble()?.removed).toBe(false);
    expect(userBubble()?.added).toContain("cancelled");
    expect((userBubble()?.children[0] as FakeEl | undefined)?.textContent).toBe("hello");
  });

  it("keeps the refused bubble when text typed during the pre-dispatch wait stayed in the composer", async () => {
    // A /skill token makes send() await the skills catalogue before dispatch.
    // Text typed in that window is not the captured draft, so the composer is
    // not cleared, and the refusal cannot put "hello /plot" back over it.
    let catalogue: (value: unknown) => void = () => {};
    vi.stubGlobal("fetch", (url: string) => {
      const path = String(url).replace("/api/v1", "");
      if (path.startsWith("/skills")) {
        return new Promise((resolve) => {
          catalogue = resolve;
        });
      }
      return Promise.resolve({
        ok: false,
        status: 409,
        text: () => Promise.resolve(JSON.stringify(refusal("model_profile_needs_key", NEEDS_KEY).body)),
      });
    });
    nodes.composer!.value = "hello /plot";
    const done = send("hello /plot");
    for (let i = 0; i < 10; i++) await Promise.resolve();
    nodes.composer!.value = "hello /plot and more";
    catalogue({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify({ skills: [] })) });
    await done;
    expect(nodes.composer!.value).toBe("hello /plot and more");
    expect(userBubble()?.removed).toBe(false);
    expect(userBubble()?.added).toContain("cancelled");
  });

  it("keeps the bubble for an environment-readiness refusal the composer cannot take back", async () => {
    let answer: (value: unknown) => void = () => {};
    vi.stubGlobal("fetch", (url: string) => {
      const path = String(url).replace("/api/v1", "");
      if (path === "/frames/frame_1/message") {
        return new Promise((resolve) => {
          answer = resolve;
        });
      }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve("{}") });
    });
    const done = send("hello");
    for (let i = 0; i < 10; i++) await Promise.resolve();
    nodes.composer!.value = "a new draft";
    answer({
      ok: false,
      status: 409,
      text: () =>
        Promise.resolve(
          JSON.stringify({ error: "environment not ready", code: "environment_not_ready", status: 409 }),
        ),
    });
    await done;
    expect(nodes.composer!.value).toBe("a new draft");
    expect(userBubble()?.removed).toBe(false);
    expect(userBubble()?.added).toContain("cancelled");
  });

  it.each([
    ["a missing key", refusal("model_profile_needs_key", NEEDS_KEY).body],
    ["an unusable pin", refusal("model_revision_unavailable", "no longer usable; rebind it").body],
    ["an unready environment", { error: "environment not ready", code: "environment_not_ready", status: 409 }],
  ])("a refusal (%s) that lands after the user opened another session stays out of it", async (_why, body) => {
    let answer: (value: unknown) => void = () => {};
    const paths: string[] = [];
    const asked: string[] = [];
    vi.stubGlobal("confirm", (text: string) => {
      asked.push(text);
      return true;
    });
    vi.stubGlobal("fetch", (url: string) => {
      const path = String(url).replace("/api/v1", "");
      paths.push(path);
      if (path === "/frames/frame_1/message") {
        return new Promise((resolve) => {
          answer = resolve;
        });
      }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve("{}") });
    });
    const done = send("hello");
    for (let i = 0; i < 10; i++) await Promise.resolve();
    // Session B is on screen now (openConversation's reset: the turn ticket
    // closed, nothing running, a new generation); its composer -- the same
    // element -- is empty.
    currentId.value = "frame_2";
    closeTurnTicket();
    running.value = false;
    _openGen.value += 1;
    nodes.composer!.value = "";
    nodes["composer-hint"]!.children.length = 0;
    answer({ ok: false, status: 409, text: () => Promise.resolve(JSON.stringify(body)) });
    await done;

    expect(nodes.composer!.value).toBe("");
    expect(lastHint()).toBe("");
    expect(openCust).not.toHaveBeenCalled();
    expect(asked).toEqual([]);
    expect(paths).not.toContain("/frames/frame_1/model-binding");
    expect(running.value).toBe(false);
  });

  it("still takes the bubble away when the environment refusal's text went back", async () => {
    routes["/frames/frame_1/message"] = {
      status: 409,
      body: { error: "environment not ready", code: "environment_not_ready", status: 409 },
    };
    await send("hello");
    expect(nodes.composer!.value).toBe("hello");
    expect(userBubble()?.removed).toBe(true);
  });
});

describe("rebindConfirmText", () => {
  const unavailable = (error: string) => ({ code: "model_revision_unavailable", message: error });

  it("says 'no longer exists' only when the server said it", () => {
    expect(
      rebindConfirmText(unavailable("this session is pinned to a model configuration that no longer exists; choose one to continue")),
    ).toMatch(/no longer exists/);
    for (const message of [
      "this session is pinned to a model configuration that is no longer usable; rebind it to continue",
      "this session's pinned model configuration could not be read; rebind it to continue",
      "this session is pinned to a model profile whose credential is not available; add its API key in Customize -> Models or rebind the session to continue",
      "this session is pinned to an earlier configuration of its model profile, and the profile now names a different provider or endpoint; its credential is not sent to the old one. Rebind the session to continue",
      "",
    ]) {
      expect(rebindConfirmText(unavailable(message))).not.toMatch(/no longer exists/);
    }
  });

  it("names the actual reason for a moved profile, a missing key and an ambiguous match", () => {
    const moved = rebindConfirmText(unavailable("... the profile now names a different provider or endpoint; ..."));
    const keyless = rebindConfirmText(unavailable("... whose credential is not available; add its API key ..."));
    const ambiguous = rebindConfirmText({ code: "model_revision_ambiguous", message: "more than one model profile matches 'gpt-4o'" });
    expect(new Set([moved, keyless, ambiguous, rebindConfirmText(unavailable(""))]).size).toBe(4);
    expect(keyless).toMatch(/API key/);
    expect(ambiguous).toMatch(/more than one/i);
  });
});

describe("rebindDoneText", () => {
  it("claims a re-bind only when one happened", () => {
    expect(rebindDoneText({ ok: true, binding: { bound: true, model_profile_id: "mp-1" } })).toBe(
      t("model.rebind.done"),
    );
    // An older daemon's answer without a binding keeps the old sentence.
    expect(rebindDoneText({ ok: true })).toBe(t("model.rebind.done"));
    const unbound = rebindDoneText({ binding: { bound: false } });
    const backfilled = rebindDoneText({ binding: { bound: true, backfilled: true } });
    expect(unbound).not.toBe(t("model.rebind.done"));
    expect(backfilled).not.toBe(t("model.rebind.done"));
    expect(unbound).not.toBe(backfilled);
  });
});
