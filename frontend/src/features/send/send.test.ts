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
import { clearMentions, composerMentions, rememberMention } from "../autocomplete/mentions";
import { UPLOAD_STATE } from "../chrome/upload";
import { renderRefProblems } from "./problems";
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
    clearMentions();
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

  it("a programmatic send leaves the user's unrelated draft in the composer", async () => {
    // A permission card's Continue and a plan approval call send() with their
    // own text while the user may be half-way through the next message.
    nodes.composer!.value = "half a thought about the next step";
    await send("Continue with the approved tool call.");
    expect(posted()).toHaveLength(1);
    expect((posted()[0]?.input_data as { request?: string }).request).toBe("Continue with the approved tool call.");
    expect(nodes.composer!.value).toBe("half a thought about the next step");
  });

  it("the composer's own text is cleared once it is sent", async () => {
    nodes.composer!.value = "  plot the growth curve \n";
    await send(nodes.composer!.value);
    expect(posted()).toHaveLength(1);
    expect(nodes.composer!.value).toBe("");
  });

  it("a picked @ file is sent as `@name` with its version beside the text (ART-16)", async () => {
    rememberMention({
      filename: "cohort_clinical_markers.csv",
      version_id: "v-9f5c06efc35e",
      artifact_id: "a-cohort",
      root_frame_id: "frame_1",
    });
    nodes.composer!.value = "@cohort_clinical_markers.csv 这个文件一共多少行？";
    await send(nodes.composer!.value);
    const body = posted()[0]!;
    // The words are what the user typed; the hash is not among them.
    expect((body.input_data as { request?: string }).request).toBe("@cohort_clinical_markers.csv 这个文件一共多少行？");
    expect(JSON.stringify(body.input_data)).not.toContain("#v-");
    expect(body.mentions).toEqual([{ filename: "cohort_clinical_markers.csv", version_id: "v-9f5c06efc35e" }]);
    // The live bubble: the text as typed, and the chip a reload draws.
    const row = nodes.messages!.children[0] as FakeEl;
    const bubble = row.children[0] as FakeEl;
    expect(bubble.textContent).toBe("@cohort_clinical_markers.csv 这个文件一共多少行？");
    const chips = row.children[1] as FakeEl;
    expect(JSON.stringify(chips.children.map((chip) => (chip as FakeEl).title))).toContain("v-9f5c06efc35e");
    // The draft went out, and its pins with it.
    expect(nodes.composer!.value).toBe("");
    expect(composerMentions("@cohort_clinical_markers.csv")).toEqual([]);
  });

  it("a typed text-form pin is sent as typed and reads as @name in the live bubble", async () => {
    nodes.composer!.value = "@counts.csv#v-abcdef123456 rows?";
    await send(nodes.composer!.value);
    // The server still resolves the text form; only the bubble hides the hash,
    // exactly as the reloaded row does.
    expect((posted()[0]!.input_data as { request?: string }).request).toBe("@counts.csv#v-abcdef123456 rows?");
    const row = nodes.messages!.children[0] as FakeEl;
    expect((row.children[0] as FakeEl).textContent).toBe("@counts.csv rows?");
  });

  it("a ref problem card names the reference as @name; the server's message keeps the version", () => {
    renderRefProblems([
      { ref: "cohort.csv#v-9f5c06efc35e", code: "not_found", message: "no artifact version v-9f5c06efc35e is available here" },
    ]);
    const card = nodes.messages!.children[0] as FakeEl;
    const row = card.children[1] as FakeEl;
    expect((row.children[0] as FakeEl).textContent).toBe("@cohort.csv");
    expect((row.children[1] as FakeEl).textContent).toContain("v-9f5c06efc35e");
  });

  it("a pin whose token was deleted before send is not sent", async () => {
    rememberMention({ filename: "a.csv", version_id: "v-aaaaaaaaaaaa", artifact_id: "a", root_frame_id: "" });
    nodes.composer!.value = "never mind the file";
    await send(nodes.composer!.value);
    expect(posted()[0]).not.toHaveProperty("mentions");
  });

  it("a programmatic send does not take the composer's pins", async () => {
    rememberMention({ filename: "a.csv", version_id: "v-aaaaaaaaaaaa", artifact_id: "a", root_frame_id: "" });
    nodes.composer!.value = "next: @a.csv";
    await send("Continue with @a.csv as planned.");
    expect(posted()[0]).not.toHaveProperty("mentions");
    expect(composerMentions(nodes.composer!.value)).toHaveLength(1);
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
