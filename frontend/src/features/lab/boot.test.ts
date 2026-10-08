import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { currentId, _openGen } from "../../stores/session";
import { _replayGap, _seqSeen, ws } from "../../stores/stream";
import { resetStoreFields } from "../../stores/signal-field";
import { onEvent, resetWsHandlers } from "../ws/registry";
import { setLabFetch as setFetch } from "./api";
import { bindLabLifecycle, LAB_REFRESH_MS, loadLab, resetLab } from "./boot";
import { lab, labConnected } from "./state";
import { deferred, detail, device, json, run } from "./fixtures";

const setLabFetch: typeof setFetch = (fn) => setFetch(fn && ((url, init) => url.includes("/observations?") ? Promise.resolve(json({ observations: [], next_after_sequence: -1 })) : fn(url, init)));

let dispose: () => void;
let reads: string[];
beforeEach(() => {
  vi.useFakeTimers(); resetStoreFields(); resetWsHandlers(); reads = [];
  setLabFetch(async (url) => { reads.push(url); return json(url.endsWith("/lab") ? { devices: [device], runs: [run()], latest_event_seq: 1 } : detail()); });
  dispose = bindLabLifecycle();
});
afterEach(() => { dispose(); setLabFetch(null); resetWsHandlers(); vi.clearAllTimers(); vi.useRealTimers(); });
const tick = () => vi.advanceTimersByTimeAsync(LAB_REFRESH_MS);
class Socket extends EventTarget { readyState = 0; }

describe("Lab lifecycle refresh", () => {
  it("coalesces hints by current root and never takes the Lab ledger seq for the session seq", async () => {
    currentId.value = "one"; await tick(); reads = [];
    onEvent({ type: "lab_update", root_frame_id: "another", latest_event_seq: 80 });
    await tick(); expect(reads).toHaveLength(0);
    for (let i = 0; i < 10; i++) onEvent({ type: "lab_update", root_frame_id: "one", latest_event_seq: i });
    await tick(); expect(reads).toEqual(["/api/v1/frames/one/lab", "/api/v1/frames/one/lab/runs/labrun-one"]);
    expect(_seqSeen.value).toEqual({});
  });
  it("re-reads after open and reconnect, removes old listeners, and reacts to replay gaps", async () => {
    currentId.value = "one"; await tick(); reads = [];
    const first = new Socket(); ws.value = first; await tick(); expect(reads).toHaveLength(0);
    first.readyState = 1; first.dispatchEvent(new Event("open")); await tick();
    expect(reads).toHaveLength(2); expect(labConnected.value).toBe(true);
    first.readyState = 3; first.dispatchEvent(new Event("close")); expect(labConnected.value).toBe(false);
    const second = new Socket(); ws.value = second; reads = [];
    first.dispatchEvent(new Event("open")); await tick(); expect(reads).toHaveLength(0);
    second.readyState = 1; second.dispatchEvent(new Event("open")); await tick(); expect(reads).toHaveLength(2);
    reads = []; _replayGap.value = "one"; await tick(); expect(reads).toHaveLength(2);
  });
  it("retires old reads on Home, generation changes and reset hooks, and reloads the new scope", async () => {
    currentId.value = "one"; await tick();
    expect(lab.state.value.detail).not.toBeNull();
    currentId.value = null; expect(lab.state.value.detail).toBeNull();
    await tick(); reads = [];
    currentId.value = "two"; _openGen.value++; await tick();
    expect(reads[0]).toBe("/api/v1/frames/two/lab");
    reads = []; _openGen.value++; expect(lab.state.value.detail).toBeNull(); await tick(); expect(reads).toHaveLength(2);
    resetLab(); expect(lab.state.value.detail).toBeNull();
    await loadLab("one"); expect(lab.state.value.detail).toBeNull();
    await loadLab("two"); expect(lab.state.value.detail).not.toBeNull();
  });
  it("does not lose a hint that arrives while a REST read is in flight", async () => {
    currentId.value = "one"; await tick();
    const held = deferred<Response>(); let indexReads = 0;
    setLabFetch(async (url) => {
      if (url.endsWith("/lab")) {
        indexReads++; if (indexReads === 1) return held.promise;
        return json({ devices: [device], runs: [run({ revision: 2 })], latest_event_seq: 2 });
      }
      return json(detail({ run: run({ revision: 2 }) }));
    });
    onEvent({ type: "lab_update", root_frame_id: "one" }); await tick();
    onEvent({ type: "lab_update", root_frame_id: "one" }); await tick();
    expect(indexReads).toBe(1);
    held.resolve(json({ devices: [device], runs: [run()], latest_event_seq: 0 })); await tick();
    expect(indexReads).toBe(2);
    expect(lab.state.value.detail?.run.revision).toBe(2);
  });
  it("makes progress while hints continue more frequently than REST responses", async () => {
    currentId.value = "one"; await tick();
    const pending: Array<ReturnType<typeof deferred<Response>>> = [];
    let revision = 0;
    setLabFetch(async (url) => {
      if (url.endsWith("/lab")) { const held = deferred<Response>(); pending.push(held); return held.promise; }
      return json(detail({ run: run({ revision }) }));
    });
    onEvent({ type: "lab_update", root_frame_id: "one" }); await tick();
    for (let i = 0; i < 3; i++) {
      onEvent({ type: "lab_update", root_frame_id: "one" }); await tick();
      revision = i + 1;
      pending[i]!.resolve(json({ devices: [device], runs: [run({ revision })], latest_event_seq: revision }));
      await vi.advanceTimersByTimeAsync(0);
      expect(lab.state.value.detail?.run.revision).toBe(revision);
      expect(pending).toHaveLength(i + 2);
    }
    pending[3]!.resolve(json({ devices: [device], runs: [run({ revision })], latest_event_seq: revision }));
    await vi.advanceTimersByTimeAsync(0);
  });

});
