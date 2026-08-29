import { afterEach, describe, expect, it, vi } from "vitest";
import { isReady } from "../../compat/stub";
import { contractStub } from "../../compat/stub";
import { installCustomize } from "./index";
import {
  createTimerLease,
  disposeTimerLease,
  liveLeaseCount,
  pendingTimerCount,
  resetTimerLeases,
  scheduleInterval,
  scheduleTimeout,
} from "./timers";
import {
  startVolcengineKeyPolling,
  VOLC_KEY_POLL_EVERY_MS,
  VOLC_KEY_POLL_FIRST_MS,
  VOLC_KEY_POLL_MAX,
} from "./volcengine";
import {
  dataproIndexComplete,
  dataproResponseCode,
  dataproResultText,
  doubaoSearchAvailable,
  doubaoSearchResultText,
} from "./vendors";
import { sanitizeLocalModelDiscovery, loopbackModelBase } from "./models";

describe("F-19 timer lease", () => {
  afterEach(() => {
    resetTimerLeases();
    vi.useRealTimers();
  });

  it("dispose leaves zero live leases and zero pending timers", () => {
    vi.useFakeTimers();
    const lease = createTimerLease();
    let fired = 0;
    scheduleTimeout(lease, () => {
      fired += 1;
    }, 1000);
    scheduleInterval(lease, () => {
      fired += 1;
    }, 250);
    expect(liveLeaseCount()).toBe(1);
    expect(pendingTimerCount()).toBe(2);
    disposeTimerLease(lease);
    expect(liveLeaseCount()).toBe(0);
    expect(pendingTimerCount()).toBe(0);
    vi.advanceTimersByTime(10_000);
    expect(fired).toBe(0);
  });

  it("a tick after unmount is a no-op (tab switch / closeCust)", () => {
    vi.useFakeTimers();
    const lease = createTimerLease();
    let polls = 0;
    const poll = () => {
      polls += 1;
      scheduleTimeout(lease, poll, 1500);
    };
    scheduleTimeout(lease, poll, 1500);
    vi.advanceTimersByTime(1500);
    expect(polls).toBe(1);
    disposeTimerLease(lease);
    vi.advanceTimersByTime(30_000);
    expect(polls).toBe(1);
    expect(pendingTimerCount()).toBe(0);
  });
});

describe("F-19 Volcengine key poll", () => {
  afterEach(() => {
    resetTimerLeases();
    vi.useRealTimers();
  });

  it("stops scheduling after the lease is disposed", async () => {
    vi.useFakeTimers();
    const lease = createTimerLease();
    let refreshes = 0;
    const handle = startVolcengineKeyPolling(lease, {
      isAlive: () => true,
      refresh: async () => {
        refreshes += 1;
        return { access: { state: "key_missing" } };
      },
    });
    await vi.advanceTimersByTimeAsync(VOLC_KEY_POLL_FIRST_MS);
    expect(refreshes).toBe(1);
    disposeTimerLease(lease);
    handle.stop();
    await vi.advanceTimersByTimeAsync(VOLC_KEY_POLL_EVERY_MS * VOLC_KEY_POLL_MAX);
    expect(refreshes).toBe(1);
    expect(pendingTimerCount()).toBe(0);
  });

  it("stops when access leaves the wait set", async () => {
    vi.useFakeTimers();
    const lease = createTimerLease();
    let refreshes = 0;
    startVolcengineKeyPolling(lease, {
      isAlive: () => true,
      refresh: async () => {
        refreshes += 1;
        return { access: { state: refreshes >= 2 ? "ready" : "key_missing" } };
      },
    });
    await vi.advanceTimersByTimeAsync(VOLC_KEY_POLL_FIRST_MS);
    await vi.advanceTimersByTimeAsync(VOLC_KEY_POLL_EVERY_MS);
    await vi.advanceTimersByTimeAsync(VOLC_KEY_POLL_EVERY_MS * 4);
    expect(refreshes).toBe(2);
    disposeTimerLease(lease);
  });
});

describe("F-19 vendor helpers (app.js:11394-11415, 11798-11887)", () => {
  it("dataproIndexComplete requires matching leaf counts and digests", () => {
    expect(
      dataproIndexComplete({
        index: {
          complete: true,
          entry_count: 3,
          source_leaf_count: 3,
          indexed_leaf_count: 3,
          source_digest: "abc",
          indexed_digest: "abc",
        },
      }),
    ).toBe(true);
    expect(
      dataproIndexComplete({
        index: {
          complete: true,
          entry_count: 3,
          source_leaf_count: 3,
          indexed_leaf_count: 2,
          source_digest: "abc",
          indexed_digest: "abc",
        },
      }),
    ).toBe(false);
    expect(dataproResponseCode({ structuredContent: { code: 4011 } })).toBe(4011);
    expect(dataproResultText({ structuredContent: { a: 1 } })).toBe(
      JSON.stringify({ a: 1 }, null, 2),
    );
  });

  it("doubaoSearchAvailable refuses a Tavily-shaped fallback", () => {
    const results = [{ title: "t", url: "https://n", snippet: "s" }];
    expect(
      doubaoSearchAvailable({
        available: true,
        source: "doubao",
        count: 1,
        results,
      }),
    ).toBe(true);
    expect(
      doubaoSearchAvailable({
        available: true,
        source: "tavily",
        count: 1,
        results,
      }),
    ).toBe(false);
    expect(doubaoSearchResultText({ results })).toBe("1. t\nhttps://n\ns");
  });

  it("local discovery only accepts loopback chatgpt endpoints", () => {
    const cleaned = sanitizeLocalModelDiscovery({
      probed: 4,
      endpoints: [
        {
          kind: "ollama",
          local: true,
          provider: "chatgpt",
          base_url: "http://127.0.0.1:11434",
          label: "Ollama",
          models: ["llama3"],
          default_model: "llama3",
        },
        {
          kind: "ollama",
          local: true,
          provider: "chatgpt",
          base_url: "https://evil.example/v1",
          models: ["x"],
        },
      ],
    });
    expect(cleaned.endpoints).toHaveLength(1);
    expect(cleaned.endpoints[0]?.base_url).toBe("http://127.0.0.1:11434");
    expect(loopbackModelBase("http://evil.example")).toBe("");
  });
});

describe("F-19 window exports", () => {
  it("installCustomize assigns real openCust / custTab / telemetryRow", () => {
    const target: Record<string, unknown> = {
      openCust: contractStub("openCust"),
      custTab: contractStub("custTab"),
      telemetryRow: contractStub("telemetryRow"),
    };
    expect(isReady(target.openCust)).toBe(false);
    installCustomize(target);
    expect(isReady(target.openCust)).toBe(true);
    expect(isReady(target.custTab)).toBe(true);
    expect(isReady(target.telemetryRow)).toBe(true);
  });
});
