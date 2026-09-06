/**
 * The first message of a fresh session shares the first-session creation with
 * Attach. That creation publishes the id as soon as POST /frames answers and
 * then opens the conversation (loadSessions + openConversation). send() used
 * to dispatch on the id alone, so openConversation's reset -- closeTurnTicket,
 * running=false, an unlocked composer, a wiped #messages, a bumped _openGen --
 * landed in the middle of the turn it had just started. It now waits for the
 * opening before it captures its generation and posts.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const wsMock = vi.hoisted(() => ({ sub: vi.fn(), unsub: vi.fn() }));
const loadMock = vi.hoisted(() => ({ loadSessions: vi.fn(async () => {}) }));
vi.mock("../ws/connect", () => wsMock);
vi.mock("../sessions/load", () => loadMock);

import { _openGen, currentId, project } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { pendingRequestId, running } from "../../stores/stream";
import { UPLOAD_STATE } from "../chrome/upload";
import { send } from "./send";
import { closeTurnTicket } from "./ticket";

type FakeEl = Record<string, unknown> & {
  classList: { add: () => void; remove: () => void; toggle: () => void; contains: () => boolean };
  value: string;
  children: unknown[];
};

function fakeEl(): FakeEl {
  const node: FakeEl = {
    classList: { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false },
    value: "",
    children: [],
    dataset: {},
    style: {},
    innerHTML: "",
    textContent: "",
    appendChild(child: unknown) {
      node.children.push(child);
      return child;
    },
    remove() {},
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

describe("send(): first message of a fresh session", () => {
  const nodes: Record<string, FakeEl> = {};
  let composer: FakeEl = fakeEl();
  const order: string[] = [];
  let releaseSessions: () => void = () => {};

  beforeEach(() => {
    resetStoreFields();
    UPLOAD_STATE.pending.clear();
    UPLOAD_STATE.failures.clear();
    UPLOAD_STATE.creations.clear();
    order.length = 0;
    for (const id of ["composer", "messages", "composer-hint", "cancel-btn", "send-btn", "workspace"]) {
      nodes[id] = fakeEl();
    }
    composer = fakeEl();
    composer.value = "hello";
    nodes.composer = composer;
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
    const sessionsHeld = new Promise<void>((resolve) => {
      releaseSessions = resolve;
    });
    vi.stubGlobal("window", {
      loadSessions: () => {
        order.push("loadSessions");
        return sessionsHeld;
      },
      openConversation: (fid: string) => {
        order.push(`openConversation:${fid}`);
        // The reset the real openConversation performs on the way in.
        closeTurnTicket();
        running.value = false;
        _openGen.value = (_openGen.value || 0) + 1;
        return Promise.resolve();
      },
    });
    vi.stubGlobal("fetch", (url: string, opts: { method?: string }) => {
      const path = String(url).replace("/api/v1", "");
      order.push(`${opts?.method || "GET"} ${path}`);
      let body: unknown = {};
      if (path === "/frames") body = { id: "frame_new" };
      else if (path === "/frames/frame_new/message") {
        body = { request_id: "req-1", execution_id: "exec-1", queue_position: 0 };
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
    currentId.value = null;
    project.value = "proj_1";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("dispatches only after the shared creation has opened the conversation", async () => {
    const done = send("hello");
    // Let POST /frames answer and the adoption park on loadSessions().
    for (let i = 0; i < 10; i++) await Promise.resolve();
    expect(currentId.value).toBe("frame_new");
    expect(order).toEqual(["POST /frames", "loadSessions"]);
    expect(order.some((step) => step.startsWith("POST /frames/frame_new/message"))).toBe(false);

    releaseSessions();
    await done;

    expect(order).toEqual([
      "POST /frames",
      "loadSessions",
      "openConversation:frame_new",
      "POST /frames/frame_new/message",
    ]);
    // The turn this send started survived the open: it is still running,
    // its ticket was accepted, and the composer's draft is gone.
    expect(running.value).toBe(true);
    expect(pendingRequestId.value).toBe("req-1");
    expect(composer.value).toBe("");
  });
});
