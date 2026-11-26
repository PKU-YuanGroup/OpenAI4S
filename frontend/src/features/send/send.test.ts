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
import { annotations, currentId, project } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { running } from "../../stores/stream";
import { clearMentions, composerMentions, rememberMention } from "../autocomplete/mentions";
import { UPLOAD_STATE } from "../chrome/upload";
import { resetMessageReceipts } from "./admission";
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

  it("Continue's canned text is sent as the message but shown as a platform line (TURN-6)", async () => {
    const seeded = (row: FakeEl) =>
      (row.children as FakeEl[]).some((c) => (c.dataset as Record<string, string>).continueSeed === "1");
    await send("Continue with the approved tool call.");
    expect(seeded(nodes.messages!.children[0] as FakeEl)).toBe(false);

    const canned =
      "Continue from the completed work. Follow the stopped-turn recovery guidance, choose a different approach if the last attempt repeated itself, and finish only the remaining work.";
    await send(canned);
    // The model still gets the instruction; only the bubble is not the user's.
    expect((posted()[1]!.input_data as { request?: string }).request).toBe(canned);
    expect(seeded(nodes.messages!.children[1] as FakeEl)).toBe(true);
  });

  it("the composer's own text is cleared once it is sent", async () => {
    nodes.composer!.value = "  plot the growth curve \n";
    await send(nodes.composer!.value);
    expect(posted()).toHaveLength(1);
    expect(nodes.composer!.value).toBe("");
  });

  it("a message queued behind a running turn says so on its bubble (TURN-5)", async () => {
    type Row = { className: string; textContent: string; children: Row[] };
    const badges = (): string[] =>
      (nodes.messages!.children as Row[])
        .flatMap((row) => row.children)
        .filter((kid) => kid.className === "msg-queued")
        .map((kid) => kid.textContent);
    await send("fit the curve");
    // Sent with nothing running: an ordinary bubble.
    expect(badges()).toEqual([]);
    running.value = true;
    await send("use the log scale instead");
    expect(posted()).toHaveLength(2);
    expect(badges()).toEqual(["Queued · not sent yet"]);
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

describe("send() submission receipts (NV-01)", () => {
  const nodes: Record<string, FakeEl> = {};
  type Answer = { status: number; body: unknown } | "offline";
  let calls: Array<{ path: string; method: string; body: Record<string, unknown>; stored: string[] }> = [];
  let answers: Record<string, Answer[]>;
  let store: Map<string, string>;

  const storage = {
    get length() {
      return store.size;
    },
    key: (i: number) => [...store.keys()][i] ?? null,
    getItem: (k: string) => (store.has(k) ? (store.get(k) as string) : null),
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
  };
  const receiptKeys = () => [...store.keys()].filter((k) => k.startsWith("openai4s.message-receipt.frame_1."));
  const posts = () => calls.filter((c) => c.method === "POST" && c.path === "/frames/frame_1/message");
  const sentId = () => String(posts()[0]?.body.client_request_id || "");
  const receiptPath = () => `/frames/frame_1/message-admissions/${sentId()}`;
  const lastHint = () => {
    // An error line ends with its dismiss button (SYNC-29); the text is before it.
    const kids = (nodes["composer-hint"]!.children as FakeEl[]).filter((k) => k.className !== "hint-dismiss");
    return String((kids[kids.length - 1] as FakeEl | undefined)?.textContent || "");
  };
  const flush = async () => {
    for (let i = 0; i < 40; i++) await Promise.resolve();
  };

  beforeEach(async () => {
    await setLang("en");
    resetStoreFields();
    resetMessageReceipts();
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
    store = new Map();
    vi.stubGlobal("localStorage", storage);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    calls = [];
    answers = {};
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      const path = String(url).replace("/api/v1", "");
      calls.push({
        path,
        method: String(init?.method || "GET"),
        body: JSON.parse(String(init?.body || "{}")),
        stored: receiptKeys(),
      });
      const queue = answers[path];
      const answer: Answer =
        queue && queue.length ? (queue.length > 1 ? (queue.shift() as Answer) : queue[0]!) : { status: 200, body: {} };
      if (answer === "offline") return Promise.reject(new TypeError("Failed to fetch"));
      return Promise.resolve({
        ok: answer.status < 400,
        status: answer.status,
        text: () => Promise.resolve(JSON.stringify(answer.body)),
      });
    });
    currentId.value = "frame_1";
    project.value = "proj_1";
  });

  afterEach(() => {
    resetMessageReceipts();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("a plain send stores a fresh 32-hex receipt id before the POST and drops it on the 202", async () => {
    answers["/frames/frame_1/message"] = [
      { status: 202, body: { request_id: "req-1", execution_id: "exec-1", admission_state: "accepted" } },
    ];
    nodes.composer!.value = "fit the curve";
    await send(nodes.composer!.value);
    const id = sentId();
    expect(id).toMatch(/^[0-9a-f]{32}$/);
    // Durable BEFORE dispatch: the POST already saw its own key in storage.
    expect(posts()[0]!.stored).toEqual([`openai4s.message-receipt.frame_1.${id}`]);
    expect(posts()[0]!.body.wait).toBe(false);
    expect(receiptKeys()).toEqual([]);
    // A new user-initiated send is a new submission, with a new id.
    closeTurnTicket();
    nodes.composer!.value = "now the residuals";
    await send(nodes.composer!.value);
    expect(posts()).toHaveLength(2);
    expect(posts()[1]!.body.client_request_id).toMatch(/^[0-9a-f]{32}$/);
    expect(posts()[1]!.body.client_request_id).not.toBe(id);
  });

  it("an annotation send keeps its own admission id and carries no receipt", async () => {
    annotations.value = [{ annotation_id: "an-1", status: "open", number: 1, body: "this peak" }];
    await send("look here");
    expect(posts()).toHaveLength(1);
    expect(posts()[0]!.body.client_request_id).toBeUndefined();
    expect(String(posts()[0]!.body.annotation_reservation_id)).toMatch(/^resv-/);
  });

  it("refuses to send without a CSPRNG rather than mint a weak id", async () => {
    vi.stubGlobal("crypto", undefined);
    nodes.composer!.value = "fit the curve";
    await send(nodes.composer!.value);
    expect(posts()).toEqual([]);
    expect(nodes.composer!.value).toBe("fit the curve");
    expect(running.value).toBe(false);
  });

  it("a lost answer is asked about, and a still-running turn is reattached, not called failed", async () => {
    answers["/frames/frame_1/message"] = ["offline"];
    answers["/frames/frame_1/execution-queue"] = [
      { status: 200, body: { owner: { execution_id: "exec-7" }, queue: [] } },
    ];
    // The receipt path is only known once the id is minted; answer every receipt read.
    const realFetch = globalThis.fetch;
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      const path = String(url).replace("/api/v1", "");
      if (path.includes("/message-admissions/")) {
        answers[path] = [
          { status: 200, body: { admission_state: "accepted", request_id: "req-7", execution_id: "exec-7" } },
        ];
      }
      return (realFetch as (u: string, i?: RequestInit) => Promise<unknown>)(url, init);
    });
    nodes.composer!.value = "fit the curve";
    await send(nodes.composer!.value);
    expect(posts()).toHaveLength(1);
    expect(calls.map((c) => c.path)).toContain(receiptPath());
    const row = nodes.messages!.children[0] as FakeEl;
    expect((row.dataset as Record<string, string>).executionId).toBe("exec-7");
    expect(running.value).toBe(true);
    expect(receiptKeys()).toEqual([]);
    expect(lastHint()).not.toContain("failed");
    expect(nodes.composer!.value).toBe("");
  });

  it("a 5xx whose receipt says refused puts the draft back and says why", async () => {
    answers["/frames/frame_1/message"] = [{ status: 502, body: { error: "bad gateway" } }];
    const realFetch = globalThis.fetch;
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      const path = String(url).replace("/api/v1", "");
      if (path.includes("/message-admissions/")) {
        answers[path] = [
          { status: 200, body: { admission_state: "refused", refusal: { code: "queue_full", status: 429 } } },
        ];
      }
      return (realFetch as (u: string, i?: RequestInit) => Promise<unknown>)(url, init);
    });
    nodes.composer!.value = "fit the curve";
    await send(nodes.composer!.value);
    expect(posts()).toHaveLength(1);
    expect(nodes.composer!.value).toBe("fit the curve");
    expect(lastHint()).toContain("Not accepted: queue_full");
    expect(running.value).toBe(false);
    expect(receiptKeys()).toEqual([]);
  });

  it("an unanswerable receipt shows 'Submission pending confirmation' and 'Check again' resolves it", async () => {
    answers["/frames/frame_1/message"] = [{ status: 503, body: { error: "unavailable" } }];
    const recover = vi.fn();
    vi.stubGlobal("recoverConversation", recover);
    const realFetch = globalThis.fetch;
    let receiptReads = 0;
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      const path = String(url).replace("/api/v1", "");
      if (path.includes("/message-admissions/")) {
        receiptReads += 1;
        answers[path] = [
          receiptReads === 1
            ? { status: 404, body: { error: "no such submission receipt" } }
            : { status: 200, body: { admission_state: "accepted", request_id: "req-8", execution_id: "exec-8" } },
        ];
      }
      if (path === "/frames/frame_1/execution-queue") {
        answers[path] = [{ status: 200, body: { owner: null, queue: [] } }];
      }
      return (realFetch as (u: string, i?: RequestInit) => Promise<unknown>)(url, init);
    });
    nodes.composer!.value = "fit the curve";
    await send(nodes.composer!.value);
    const bubble = nodes.messages!.children[0] as FakeEl;
    expect((bubble.dataset as Record<string, string>).sendState).toBe("unconfirmed");
    const notice = (bubble.children as FakeEl[]).find((c) => c.className === "msg-receipt") as FakeEl;
    expect((notice.children as FakeEl[]).map((c) => c.textContent)).toEqual([
      "Submission pending confirmation",
      "Check again",
    ]);
    expect(lastHint()).toContain("Submission pending confirmation");
    // Nothing is known to be running, so the composer is released...
    expect(running.value).toBe(false);
    // ...and nothing was resent, and the id is still held for a reload.
    expect(posts()).toHaveLength(1);
    expect(receiptKeys()).toHaveLength(1);

    const again = (notice.children as FakeEl[])[1] as FakeEl & { onclick: () => void };
    again.onclick();
    await flush();
    expect(receiptReads).toBe(2);
    expect((bubble.dataset as Record<string, string>).sendState).toBeUndefined();
    expect((bubble.dataset as Record<string, string>).executionId).toBe("exec-8");
    expect(receiptKeys()).toEqual([]);
    // Finished while unknown: history, not the live view.
    expect(recover).toHaveBeenCalledWith("frame_1", expect.any(Number));
    expect(posts()).toHaveLength(1);
  });

  it("an unconfirmed receipt is re-asked on the backoff, without a resend", async () => {
    answers["/frames/frame_1/message"] = ["offline"];
    let receiptReads = 0;
    const realFetch = globalThis.fetch;
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      const path = String(url).replace("/api/v1", "");
      if (path.includes("/message-admissions/")) {
        receiptReads += 1;
        answers[path] = [{ status: 200, body: { admission_state: "indeterminate" } }];
      }
      return (realFetch as (u: string, i?: RequestInit) => Promise<unknown>)(url, init);
    });
    nodes.composer!.value = "fit the curve";
    await send(nodes.composer!.value);
    expect(receiptReads).toBe(1);
    for (let round = 0; round < 6; round++) {
      await vi.advanceTimersByTimeAsync(3000);
    }
    // Bounded: three automatic re-asks, then only "Check again".
    expect(receiptReads).toBe(4);
    expect(posts()).toHaveLength(1);
    expect(receiptKeys()).toHaveLength(1);
  });
});
