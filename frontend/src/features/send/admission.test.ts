import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import {
  ADMISSION_GRACE_MS,
  ADMISSION_LEGACY_KEY,
  ADMISSION_PREFIX,
  MESSAGE_RECEIPT_PREFIX,
  MESSAGE_RECEIPT_RETRY_MS,
  admissionSettled,
  admissionWithinGrace,
  executionPosition,
  forgetAdmission,
  forgetMessageReceipt,
  mintClientRequestId,
  outstandingAdmissions,
  outstandingMessageReceipts,
  queryMessageReceipt,
  reconcileLastAdmission,
  reconcileMessageReceipts,
  rememberAdmission,
  rememberMessageReceipt,
  resetAdmissionRetries,
  resetMessageReceipts,
} from "./admission";

class MemoryStorage {
  private readonly data = new Map<string, string>();
  get length(): number {
    return this.data.size;
  }
  key(index: number): string | null {
    return [...this.data.keys()][index] ?? null;
  }
  getItem(key: string): string | null {
    return this.data.has(key) ? (this.data.get(key) as string) : null;
  }
  setItem(key: string, value: string): void {
    this.data.set(key, String(value));
  }
  removeItem(key: string): void {
    this.data.delete(key);
  }
  clear(): void {
    this.data.clear();
  }
}

describe("admission localStorage keys", () => {
  let store: MemoryStorage;

  beforeEach(() => {
    resetStoreFields();
    resetAdmissionRetries();
    store = new MemoryStorage();
    vi.stubGlobal("localStorage", store);
    vi.useFakeTimers();
    vi.setSystemTime(1_700_000_000_000);
  });

  afterEach(() => {
    resetAdmissionRetries();
    vi.unstubAllGlobals();
    vi.useRealTimers();
    resetStoreFields();
  });

  it("writes independent openai4s.admission.{fid}.{id} keys, never a container", () => {
    rememberAdmission("frame-1", "resv-b");
    vi.setSystemTime(1_700_000_000_500);
    rememberAdmission("frame-1", "resv-a");
    expect(store.getItem(ADMISSION_LEGACY_KEY("frame-1"))).toBeNull();
    expect(store.getItem(ADMISSION_PREFIX("frame-1") + "resv-a")).toBe("1700000000500");
    expect(store.getItem(ADMISSION_PREFIX("frame-1") + "resv-b")).toBe("1700000000000");
    for (let i = 0; i < store.length; i++) {
      const key = store.key(i);
      expect(key).toMatch(/^openai4s\.admission\./);
    }
    expect(outstandingAdmissions("frame-1")).toEqual(["resv-b", "resv-a"]);
  });

  it("migrates a legacy scalar key into an independent reservation", () => {
    store.setItem(ADMISSION_LEGACY_KEY("frame-1"), "resv-old");
    expect(outstandingAdmissions("frame-1")).toEqual(["resv-old"]);
    expect(store.getItem(ADMISSION_LEGACY_KEY("frame-1"))).toBeNull();
    expect(store.getItem(ADMISSION_PREFIX("frame-1") + "resv-old")).toBeTruthy();
  });

  it("migrates a legacy JSON array into independent keys, oldest-first by stamp then id", () => {
    store.setItem(ADMISSION_LEGACY_KEY("frame-1"), JSON.stringify(["z-id", "a-id"]));
    const ids = outstandingAdmissions("frame-1");
    expect(ids).toEqual(["a-id", "z-id"]);
    expect(store.getItem(ADMISSION_LEGACY_KEY("frame-1"))).toBeNull();
    expect(store.getItem(ADMISSION_PREFIX("frame-1") + "a-id")).toBeTruthy();
    expect(store.getItem(ADMISSION_PREFIX("frame-1") + "z-id")).toBeTruthy();
  });

  it("admissionSettled is sent/released/none only", () => {
    expect(admissionSettled("sent")).toBe(true);
    expect(admissionSettled("released")).toBe(true);
    expect(admissionSettled("none")).toBe(true);
    expect(admissionSettled("pending")).toBe(false);
    expect(admissionSettled("unknown")).toBe(false);
    expect(admissionSettled(null)).toBe(false);
  });

  it("grace window is 60s; a 404 inside it keeps the key, past it drops it", async () => {
    expect(ADMISSION_GRACE_MS).toBe(60_000);
    rememberAdmission("frame-1", "fresh");
    expect(admissionWithinGrace("frame-1", "fresh")).toBe(true);

    store.setItem(ADMISSION_PREFIX("frame-1") + "stale", String(Date.now() - 60_000));
    expect(admissionWithinGrace("frame-1", "stale")).toBe(false);

    currentId.value = "frame-1";
    vi.stubGlobal("fetch", async () => ({
      ok: false,
      status: 404,
      text: async () => "{}",
    }));

    await reconcileLastAdmission("frame-1");
    expect(outstandingAdmissions("frame-1")).toEqual(["fresh"]);
    expect(store.getItem(ADMISSION_PREFIX("frame-1") + "stale")).toBeNull();
    expect(store.getItem(ADMISSION_PREFIX("frame-1") + "fresh")).toBeTruthy();
  });

  it("forgetAdmission removes one independent key", () => {
    rememberAdmission("frame-1", "keep");
    rememberAdmission("frame-1", "drop");
    forgetAdmission("frame-1", "drop");
    expect(outstandingAdmissions("frame-1")).toEqual(["keep"]);
  });

  it("a settled GET forgets the reservation", async () => {
    rememberAdmission("frame-1", "resv-1");
    currentId.value = "frame-1";
    vi.stubGlobal("fetch", async () => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ state: "sent" }),
    }));
    await reconcileLastAdmission("frame-1");
    expect(outstandingAdmissions("frame-1")).toEqual([]);
  });
});

describe("plain-message submission receipts (NV-01)", () => {
  let store: MemoryStorage;
  let responses: Record<string, { status: number; body: unknown } | "offline">;
  let asked: string[];

  const reply = (path: string) => {
    asked.push(path);
    const found = responses[path];
    if (found === "offline") return Promise.reject(new TypeError("Failed to fetch"));
    const answer = found || { status: 404, body: { error: "not found" } };
    return Promise.resolve({
      ok: answer.status < 400,
      status: answer.status,
      text: async () => JSON.stringify(answer.body),
    });
  };

  beforeEach(() => {
    resetStoreFields();
    resetMessageReceipts();
    store = new MemoryStorage();
    vi.stubGlobal("localStorage", store);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    vi.setSystemTime(1_700_000_000_000);
    responses = {};
    asked = [];
    vi.stubGlobal("fetch", (url: string) => reply(String(url).replace("/api/v1", "")));
    currentId.value = "frame-1";
  });

  afterEach(() => {
    resetMessageReceipts();
    vi.unstubAllGlobals();
    vi.useRealTimers();
    resetStoreFields();
  });

  const receiptPath = (id: string) => `/frames/frame-1/message-admissions/${id}`;

  it("mints 32 lowercase hex from the CSPRNG and refuses without one", () => {
    const first = mintClientRequestId();
    const second = mintClientRequestId();
    expect(first).toMatch(/^[0-9a-f]{32}$/);
    expect(second).toMatch(/^[0-9a-f]{32}$/);
    expect(first).not.toBe(second);
    vi.stubGlobal("crypto", undefined);
    expect(() => mintClientRequestId()).toThrow(/getRandomValues/);
  });

  it("stores {frame, id, time} under its own prefix, never the frozen admission keys", () => {
    rememberMessageReceipt("frame-1", "b".repeat(32));
    vi.setSystemTime(1_700_000_000_500);
    rememberMessageReceipt("frame-1", "a".repeat(32));
    expect(store.getItem(MESSAGE_RECEIPT_PREFIX("frame-1") + "a".repeat(32))).toBe("1700000000500");
    expect(outstandingMessageReceipts("frame-1")).toEqual(["b".repeat(32), "a".repeat(32)]);
    expect(outstandingAdmissions("frame-1")).toEqual([]);
    for (let i = 0; i < store.length; i++) {
      expect(store.key(i)).toMatch(/^openai4s\.message-receipt\.frame-1\./);
    }
    forgetMessageReceipt("frame-1", "b".repeat(32));
    expect(outstandingMessageReceipts("frame-1")).toEqual(["a".repeat(32)]);
  });

  it("reads where an execution stands in a session snapshot", () => {
    const snapshot = {
      owner: { execution_id: "exec-run" },
      queue: [{ execution_id: "exec-q", queue_position: 2 }],
    };
    expect(executionPosition(snapshot, "exec-run")).toBe(0);
    expect(executionPosition(snapshot, "exec-q")).toBe(2);
    expect(executionPosition(snapshot, "exec-gone")).toBeNull();
    expect(executionPosition(null, "exec-run")).toBeNull();
    expect(executionPosition(snapshot, null)).toBeNull();
  });

  it("answers accepted only with the snapshot's word on whether it still runs", async () => {
    const id = "c".repeat(32);
    responses[receiptPath(id)] = {
      status: 200,
      body: { admission_state: "accepted", execution_id: "exec-1", request_id: "req-1" },
    };
    responses["/frames/frame-1/execution-queue"] = {
      status: 200,
      body: { owner: { execution_id: "exec-1" }, queue: [] },
    };
    expect(await queryMessageReceipt("frame-1", id)).toMatchObject({
      kind: "accepted",
      active: true,
      queuePosition: 0,
    });
    responses["/frames/frame-1/execution-queue"] = { status: 200, body: { owner: null, queue: [] } };
    expect(await queryMessageReceipt("frame-1", id)).toMatchObject({ kind: "accepted", active: false });
    // An unreadable snapshot is not evidence that it still runs.
    responses["/frames/frame-1/execution-queue"] = { status: 503, body: {} };
    expect(await queryMessageReceipt("frame-1", id)).toMatchObject({ kind: "accepted", active: false });
  });

  it("tells refused, pending, indeterminate, 404, 5xx and offline apart", async () => {
    const id = "d".repeat(32);
    const ask = async (answer: { status: number; body: unknown } | "offline") => {
      responses[receiptPath(id)] = answer;
      return queryMessageReceipt("frame-1", id);
    };
    expect(await ask({ status: 200, body: { admission_state: "refused", refusal: { code: "queue_full" } } }))
      .toMatchObject({ kind: "refused" });
    expect(await ask({ status: 200, body: { admission_state: "pending" } }))
      .toMatchObject({ kind: "unconfirmed", reason: "pending" });
    expect(await ask({ status: 200, body: { admission_state: "indeterminate" } }))
      .toMatchObject({ kind: "unconfirmed", reason: "indeterminate" });
    expect(await ask({ status: 404, body: {} })).toEqual({ kind: "unconfirmed", reason: "not_found" });
    expect(await ask({ status: 503, body: {} })).toEqual({ kind: "unconfirmed", reason: "unavailable" });
    expect(await ask("offline")).toEqual({ kind: "unconfirmed", reason: "offline" });
    // Every one of those was a read; nothing was ever posted.
    expect(asked.every((path) => !path.endsWith("/message"))).toBe(true);
  });

  it("a reopened page reads a finished accepted turn from history and forgets the key", async () => {
    const id = "e".repeat(32);
    rememberMessageReceipt("frame-1", id);
    const recover = vi.fn();
    vi.stubGlobal("recoverConversation", recover);
    responses[receiptPath(id)] = { status: 200, body: { admission_state: "accepted", execution_id: "exec-1" } };
    responses["/frames/frame-1/execution-queue"] = { status: 200, body: { owner: null, queue: [] } };
    await reconcileMessageReceipts("frame-1");
    expect(outstandingMessageReceipts("frame-1")).toEqual([]);
    expect(recover).toHaveBeenCalledWith("frame-1");
  });

  it("a reopened page leaves a still-running accepted turn to the live view", async () => {
    const id = "f".repeat(32);
    rememberMessageReceipt("frame-1", id);
    const recover = vi.fn();
    vi.stubGlobal("recoverConversation", recover);
    responses[receiptPath(id)] = { status: 200, body: { admission_state: "accepted", execution_id: "exec-1" } };
    responses["/frames/frame-1/execution-queue"] = {
      status: 200,
      body: { owner: { execution_id: "exec-1" }, queue: [] },
    };
    await reconcileMessageReceipts("frame-1");
    expect(outstandingMessageReceipts("frame-1")).toEqual([]);
    expect(recover).not.toHaveBeenCalled();
  });

  it("a 404 inside the grace is re-asked quietly, never dropped", async () => {
    const id = "1".repeat(32);
    rememberMessageReceipt("frame-1", id);
    await reconcileMessageReceipts("frame-1");
    expect(outstandingMessageReceipts("frame-1")).toEqual([id]);
    expect(asked.filter((p) => p === receiptPath(id))).toHaveLength(1);
    // Another tab's POST lands; the next sweep finds it accepted.
    responses[receiptPath(id)] = { status: 200, body: { admission_state: "accepted", execution_id: "exec-9" } };
    responses["/frames/frame-1/execution-queue"] = {
      status: 200,
      body: { owner: { execution_id: "exec-9" }, queue: [] },
    };
    await vi.advanceTimersByTimeAsync(MESSAGE_RECEIPT_RETRY_MS);
    expect(asked.filter((p) => p === receiptPath(id))).toHaveLength(2);
    expect(outstandingMessageReceipts("frame-1")).toEqual([]);
  });

  it("past the grace an unconfirmed receipt is shown, not silently kept or resent", async () => {
    const appended: Array<Record<string, unknown>> = [];
    const messages = {
      querySelector: () => null,
      appendChild: (node: Record<string, unknown>) => {
        appended.push(node);
        return node;
      },
    };
    const make = () => {
      const node: Record<string, unknown> & { children: unknown[] } = {
        children: [],
        dataset: {},
        style: {},
        classList: { contains: () => false, add: () => {}, remove: () => {} },
        textContent: "",
        className: "",
        setAttribute: () => {},
        querySelector: () => null,
        appendChild(child: unknown) {
          node.children.push(child);
          return child;
        },
      };
      return node;
    };
    vi.stubGlobal("document", {
      querySelector: (sel: string) => (sel === "#messages" ? messages : null),
      createElement: () => make(),
    });
    const id = "2".repeat(32);
    store.setItem(MESSAGE_RECEIPT_PREFIX("frame-1") + id, String(Date.now() - ADMISSION_GRACE_MS));
    responses[receiptPath(id)] = { status: 200, body: { admission_state: "indeterminate" } };
    await reconcileMessageReceipts("frame-1");
    expect(appended).toHaveLength(1);
    const row = appended[0] as { dataset: Record<string, string>; children: Array<{ children: Array<{ textContent: string }> }> };
    expect(row.dataset.clientRequestId).toBe(id);
    expect(row.dataset.sendState).toBe("unconfirmed");
    expect(row.children[0]!.children.map((c) => c.textContent)).toEqual([
      "Submission pending confirmation",
      "Check again",
    ]);
    // Indeterminate stays asked-about; only the server can settle it.
    expect(outstandingMessageReceipts("frame-1")).toEqual([id]);
    expect(asked.every((path) => !path.endsWith("/message"))).toBe(true);
  });

  it("the session reopen path asks about message receipts too", async () => {
    const id = "3".repeat(32);
    rememberMessageReceipt("frame-1", id);
    responses[receiptPath(id)] = {
      status: 200,
      body: { admission_state: "refused", refusal: { code: "queue_full", status: 429 } },
    };
    await reconcileLastAdmission("frame-1");
    await vi.advanceTimersByTimeAsync(0);
    expect(asked).toContain(receiptPath(id));
    expect(outstandingMessageReceipts("frame-1")).toEqual([]);
  });
});
