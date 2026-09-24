/**
 * send() behaviour outside refusals (refused-send.test.ts) and the first
 * message of a fresh session (first-send.test.ts).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const wsMock = vi.hoisted(() => ({ sub: vi.fn(), unsub: vi.fn() }));
const loadMock = vi.hoisted(() => ({ loadSessions: vi.fn(async () => {}) }));
vi.mock("../ws/connect", () => wsMock);
vi.mock("../sessions/load", () => loadMock);

import { setLang, t } from "../../i18n/runtime";
import { currentId, project } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { UPLOAD_STATE } from "../chrome/upload";
import { send } from "./send";
import { closeTurnTicket } from "./ticket";

type FakeEl = Record<string, unknown> & { value: string; children: unknown[] };

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

describe("send()", () => {
  const nodes: Record<string, FakeEl> = {};
  let requests: Array<{ path: string; body: Record<string, unknown> }> = [];
  let catalogUp = true;

  function posted(): Array<Record<string, unknown>> {
    return requests.filter((r) => r.path === "/frames/frame_1/message").map((r) => r.body);
  }

  beforeEach(async () => {
    await setLang("en");
    resetStoreFields();
    closeTurnTicket();
    UPLOAD_STATE.pending.clear();
    UPLOAD_STATE.failures.clear();
    UPLOAD_STATE.creations.clear();
    for (const id of ["composer", "messages", "composer-hint", "cancel-btn", "send-btn", "workspace"]) {
      nodes[id] = fakeEl();
    }
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
    requests = [];
    catalogUp = true;
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      const path = String(url).replace("/api/v1", "");
      requests.push({ path, body: JSON.parse(String(init?.body || "{}")) });
      if (path === "/skills/catalog" && !catalogUp) {
        return Promise.resolve({ ok: false, status: 503, text: () => Promise.resolve('{"error":"down"}') });
      }
      const body =
        path === "/skills/catalog"
          ? { skills: [{ name: "plot" }] }
          : path === "/frames/frame_1/message"
            ? { request_id: "req-" + requests.length, execution_id: "exec-" + requests.length }
            : {};
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) });
    });
    currentId.value = "frame_1";
    project.value = "proj_1";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("a /skill send after a failed catalog read still gets its directive next time", async () => {
    const directive = t("skill.invokeDirective", "plot");
    catalogUp = false;
    await send("/plot the growth curve");
    expect(String((posted()[0]?.input_data as { request?: string }).request)).not.toContain(directive);

    catalogUp = true;
    closeTurnTicket();
    await send("/plot it again");
    expect(String((posted()[1]?.input_data as { request?: string }).request)).toContain(directive);
    expect(requests.filter((r) => r.path === "/skills/catalog")).toHaveLength(2);
  });
});
