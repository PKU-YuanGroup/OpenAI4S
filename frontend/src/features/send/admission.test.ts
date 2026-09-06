import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import {
  ADMISSION_GRACE_MS,
  ADMISSION_LEGACY_KEY,
  ADMISSION_PREFIX,
  admissionSettled,
  admissionWithinGrace,
  forgetAdmission,
  outstandingAdmissions,
  reconcileLastAdmission,
  rememberAdmission,
  resetAdmissionRetries,
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
