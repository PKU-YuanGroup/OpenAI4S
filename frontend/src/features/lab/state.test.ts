import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { effect } from "@preact/signals";
import { setLabFetch as setFetch } from "./api";
import { canEnd, canStop, LabController } from "./state";
import { command, deferred, descriptor, detail, device, exported, json, observation, run } from "./fixtures";

const setLabFetch: typeof setFetch = (fn) => setFetch(fn && ((url, init) => url.includes("/observations?") ? Promise.resolve(json({ observations: [], next_after_sequence: -1 })) : fn(url, init)));

let controller: LabController;
let snapshot = detail();
let posts: Array<{ url: string; body: Record<string, unknown> }>;
let send: (url: string, body: Record<string, unknown>) => Promise<Response>;
beforeEach(() => {
  vi.useFakeTimers(); posts = []; snapshot = detail();
  let keys = 0;
  controller = new LabController(() => ++keys === 1 ? "same-key" : `new-key-${keys}`); controller.scope("root", 1);
  send = async () => json({ run: snapshot.run, command: command(), observation: snapshot.observation });
  setLabFetch(async (url, init) => {
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body)); posts.push({ url, body }); return send(url, body);
    }
    return json(url.endsWith("/lab") ? { devices: [device], runs: [snapshot.run], latest_event_seq: 1 } : snapshot);
  });
});
afterEach(() => { setLabFetch(null); vi.clearAllTimers(); vi.useRealTimers(); });
const ready = () => controller.refresh();
const execute = () => controller.execute(descriptor.capabilities[0]!, { volume: 200 });

describe("confirmed Lab state", () => {
  it("discards reads after root, navigation generation, or selected run changes", async () => {
    for (const change of [() => controller.scope("other", 1), () => controller.scope("root", 2), () => controller.state.value = { ...controller.state.value, selectedRunId: "other-run" }]) {
      controller.scope("root", 1, true); await ready();
      const held = deferred<Response>();
      setLabFetch(async (url) => url.endsWith("/lab") ? json({ devices: [device], runs: [run()], latest_event_seq: 1 }) : held.promise);
      const pending = controller.refresh(); await vi.advanceTimersByTimeAsync(0);
      change(); const before = controller.state.value.detail;
      held.resolve(json(detail({ run: run({ revision: 10 }) }))); await pending;
      expect(controller.state.value.detail).toBe(before);
      setLabFetch(async (url) => json(url.endsWith("/lab") ? { devices: [device], runs: [run()], latest_event_seq: 1 } : detail()));
    }
  });

  it("does not let an earlier same-revision GET overwrite a confirmed stop", async () => {
    await ready();
    const held = deferred<Response>(); let hold = true;
    setLabFetch(async (url, init) => {
      if (init?.method === "POST") { snapshot = detail({ run: run({ status: "ended", end_reason: "stopped" }) }); return json({ run: snapshot.run, stopped: true, semantics: "end_session" }); }
      if (url.endsWith("/lab")) return json({ devices: [device], runs: [snapshot.run], latest_event_seq: 1 });
      if (hold) { hold = false; return held.promise; }
      return json(snapshot);
    });
    const pending = controller.refresh(); await vi.advanceTimersByTimeAsync(0);
    const stop = controller.stop(); await vi.advanceTimersByTimeAsync(0);
    const statuses: string[] = [];
    const dispose = effect(() => { statuses.push(controller.state.value.detail?.run.status || "none"); });
    held.resolve(json(detail())); await Promise.all([stop, pending]); dispose();
    expect(statuses).not.toContain("ready");
    expect(controller.state.value.detail?.run.status).toBe("ended");
  });

  it("rejects lower revisions and earlier same-revision run metadata", async () => {
    snapshot = detail({ run: run({ revision: 4, command_count: 6, updated_at: 1790000000006 }) }); await ready();
    for (const old of [run({ revision: 3, command_count: 6, updated_at: 1790000000010 }), run({ revision: 4, command_count: 5, updated_at: 1790000000005 })]) {
      snapshot = detail({ run: old }); await ready();
      expect(controller.state.value.detail?.run).toMatchObject({ revision: 4, command_count: 6 });
    }
  });

  it("retries an immutable original command and key after a lost response, including session return", async () => {
    await ready(); send = async () => { throw new TypeError("network lost"); };
    await execute(); const original = posts[0];
    expect(controller.state.value.detail?.run.revision).toBe(0);
    controller.scope("elsewhere", 2); expect(controller.state.value.pending).toBeNull();
    controller.scope("root", 3);
    snapshot = detail({ run: run({ revision: 2 }) }); await ready();
    await execute(); expect(posts).toHaveLength(1);
    await controller.retry();
    expect(posts).toEqual([original, original]);
    expect(posts[1]!.body.expected_revision).toBe(0);
  });

  it("retains a timed-out create request and prevents new creation until same-key retry", async () => {
    send = async () => { throw new TypeError("response lost"); };
    await controller.create(device, device.profiles[0]!, 0);
    await controller.create(device, device.profiles[1]!, 5);
    await controller.retry();
    expect(posts).toHaveLength(2); expect(posts[1]).toEqual(posts[0]);
    expect(posts[0]!.body).toMatchObject({ seed: 0, idempotency_key: "same-key" });
  });

  it("drops a create its server refused definitively and keeps one whose outcome is uncertain", async () => {
    send = async () => json({ error: "Lab provider creation failed", code: "provider_unavailable" }, 503);
    await controller.create(device, device.profiles[0]!, 0);
    expect(controller.state.value.pending).toBeNull();
    expect(controller.state.value.error).toContain("Lab provider creation failed");
    // The refused key ended its run as failed, so the next attempt uses a new key.
    send = async () => json({ error: "Store unavailable", code: "persistence_unavailable" }, 503);
    await controller.create(device, device.profiles[0]!, 0);
    expect(posts.map((p) => p.body.idempotency_key)).toEqual(["same-key", "new-key-2"]);
    expect(controller.state.value.pending?.intent.body.idempotency_key).toBe("new-key-2");
  });

  it("keeps an execute whose 503 never proves the command was not dispatched", async () => {
    await ready();
    send = async () => json({ error: "Lab service is unavailable", code: "provider_unavailable" }, 503);
    await execute();
    expect(controller.state.value.pending?.intent.kind).toBe("execute");
    await controller.retry();
    expect(posts).toHaveLength(2); expect(posts[1]).toEqual(posts[0]);
  });

  it("marks a scope loaded only after its first index read", async () => {
    expect(controller.state.value.loaded).toBe(false);
    await ready(); expect(controller.state.value.loaded).toBe(true);
    controller.scope("other", 1); expect(controller.state.value.loaded).toBe(false);
  });

  it("accepts HTTP 200 outcome_unknown and queries the command without executing again", async () => {
    await ready(); const unknown = command();
    send = async (_url, body) => {
      if (body.operation) snapshot = detail({ run: run({ status: "quarantined", command_count: 1 }), commands: [unknown] });
      else snapshot = detail({ run: run({ revision: 1, command_count: 1 }), commands: [command({ state: "succeeded", error: null })] });
      return json({ run: snapshot.run, command: snapshot.commands[0], observation: snapshot.observation });
    };
    await execute();
    expect(controller.state.value.detail?.commands[0]?.state).toBe("outcome_unknown");
    expect(controller.state.value.pending).toBeNull();
    await execute(); expect(posts).toHaveLength(1);
    await controller.reconcile(unknown.command_id);
    expect(posts[1]!.url).toMatch(/\/commands\/labcmd-one\/reconcile$/);
    expect(posts[1]!.body).toEqual({});
    expect(controller.state.value.detail?.commands[0]?.idempotency_key).toBe("same-key");
  });

  it("replaces a lost-response retry with reconciliation once REST confirms the command", async () => {
    await ready(); send = async () => { throw new TypeError("lost"); };
    await execute(); expect(controller.state.value.pending).not.toBeNull();
    snapshot = detail({ run: run({ status: "quarantined", command_count: 1 }), commands: [command()] });
    await ready(); expect(controller.state.value.pending).toBeNull();
    await controller.retry(); expect(posts).toHaveLength(1);
  });

  it("remembers command confirmation received before the original POST fails", async () => {
    await ready();
    const held = deferred<Response>(); send = () => held.promise;
    const pending = execute(); await vi.advanceTimersByTimeAsync(0);
    snapshot = detail({ run: run({ status: "quarantined", command_count: 1 }), commands: [command()] });
    await ready();
    held.resolve(json({ error: "Receipt transport lost", code: "persistence_unavailable" }, 503));
    await pending;
    expect(controller.state.value.pending).toBeNull();
    await controller.retry(); expect(posts).toHaveLength(1);
    expect(controller.state.value.detail?.commands[0]?.state).toBe("outcome_unknown");
  });

  it("keeps the latest observation after null or old command receipts when the refresh fails", async () => {
    for (const incoming of [null, observation({ sequence: 1 })]) {
      controller.scope("root", 1, true);
      snapshot = detail({ run: run({ revision: 3 }), observation: observation({ sequence: 3 }) });
      await ready();
      send = async () => {
        setLabFetch(async () => json({ error: "read unavailable" }, 503));
        return json({ run: snapshot.run, command: command({ state: "rejected" }), observation: incoming });
      };
      await execute();
      expect(controller.state.value.detail?.observation?.sequence).toBe(3);
      setLabFetch(async (url, init) => init?.method === "POST" ? send(url, {}) : json(url.endsWith("/lab") ? { devices: [device], runs: [snapshot.run] } : snapshot));
      snapshot.commands = [command()]; await ready();
      await controller.reconcile("labcmd-one");
      expect(controller.state.value.detail?.observation?.sequence).toBe(3);
      setLabFetch(async (url, init) => init?.method === "POST" ? send(url, {}) : json(url.endsWith("/lab") ? { devices: [device], runs: [snapshot.run] } : snapshot));
    }
  });

  it("preserves full sensor data when a command returns an equal or newer array summary", async () => {
    for (const sequence of [3, 4]) {
      controller.scope("root", 1, true);
      snapshot = detail({ run: run({ revision: 3 }), observation: observation({ sequence: 3 }) });
      await ready();
      const summarized = observation({ sequence });
      summarized.channels[0]!.value = { shape: [2, 4], summary: { min: .1, max: .8, mean: .4 }, truncated: true };
      send = async () => {
        setLabFetch(async () => json({ error: "read unavailable" }, 503));
        return json({ run: run({ revision: sequence }), command: command({ state: "succeeded" }), observation: summarized });
      };
      await execute();
      expect(controller.state.value.detail?.observation?.sequence).toBe(3);
      expect(Array.isArray(controller.state.value.detail?.observation?.channels[0]?.value)).toBe(true);
      await vi.advanceTimersByTimeAsync(0);
      setLabFetch(async (url, init) => init?.method === "POST" ? send(url, {}) : json(url.endsWith("/lab") ? { devices: [device], runs: [snapshot.run] } : snapshot));
    }
  });

  it("releases stop and query controls after the POST settles while REST remains pending", async () => {
    for (const action of ["stop", "reconcile"] as const) {
      controller.scope("root", 1, true); snapshot = detail({ commands: [command()] }); await ready();
      const held = deferred<void>();
      send = async () => {
        setLabFetch(async (url) => { await held.promise; return json(url.endsWith("/lab") ? { devices: [device], runs: [snapshot.run] } : snapshot); });
        return json({ run: snapshot.run, command: command(), observation: snapshot.observation, stopped: true, semantics: "end_session" });
      };
      const pending = action === "stop" ? controller.stop() : controller.reconcile("labcmd-one");
      await vi.advanceTimersByTimeAsync(0);
      try {
        expect(controller.state.value.stopping).toBe(false);
        expect(controller.state.value.querying).toBeNull();
      } finally { held.resolve(); await pending; await controller.refresh(); }
      setLabFetch(async (url, init) => init?.method === "POST" ? send(url, {}) : json(url.endsWith("/lab") ? { devices: [device], runs: [snapshot.run] } : snapshot));
    }
  });

  it("retains rejected commands and their reason instead of declaring execution success", async () => {
    await ready();
    snapshot = detail({ run: run({ command_count: 1 }), commands: [command({ state: "rejected", error_code: "unsupported_action", error: "Allowed volume: 200, 400 mL" })] });
    send = async () => json({ run: snapshot.run, command: snapshot.commands[0], observation: null });
    await execute();
    expect(controller.state.value.detail?.commands[0]).toMatchObject({ state: "rejected", error: "Allowed volume: 200, 400 mL" });
    expect(controller.state.value.pending).toBeNull();
  });

  it("enforces capability levels, current revision, unavailable devices and run status", async () => {
    await ready();
    await controller.create({ ...device, available: false }, device.profiles[0]!);
    await controller.execute(descriptor.capabilities[0]!, { volume: 201 });
    for (const status of ["busy", "quarantined", "ended"] as const) {
      controller.state.value = { ...controller.state.value, detail: detail({ run: run({ status }) }) }; await execute();
    }
    expect(posts).toHaveLength(0);
    controller.state.value = { ...controller.state.value, detail: detail({ run: run({ revision: 7 }) }) };
    await execute(); expect(posts[0]!.body.expected_revision).toBe(7);
  });
});

describe("Lab evidence and terminal controls", () => {
  it("exports once while pending, defaults to sensor evidence, and keeps exact versions", async () => {
    await ready();
    const held = deferred<Response>(); send = () => held.promise;
    const pending = controller.exportRun();
    expect(controller.state.value.exported?.loading).toBe(true);
    await controller.exportRun(true);
    expect(posts).toEqual([{ url: "/api/v1/frames/root/lab/runs/labrun-one/export", body: { include_evaluation: false } }]);
    held.resolve(json(exported())); await pending;
    expect(controller.state.value.exported).toMatchObject({ loading: false, error: "", result: exported() });
    send = async () => json(exported({ include_evaluation: true }));
    await controller.exportRun(true);
    expect(posts[1]?.body).toEqual({ include_evaluation: true });
    expect(controller.state.value.exported?.result?.include_evaluation).toBe(true);
  });

  it("keeps export errors honest and rejects responses for another run or opt-in", async () => {
    await ready();
    for (const result of [
      json({ error: "capture unavailable" }, 503), json(exported({ run_id: "other-run" })), json(exported({ include_evaluation: true })),
      json(exported({ artifacts: [{ ...exported().artifacts[0]!, version_id: "" }] })),
      json(exported({ artifacts: [{ ...exported().artifacts[0]!, kind: "simulation_ground_truth" }] })),
    ]) {
      send = async () => result;
      await controller.exportRun();
      expect(controller.state.value.exported?.loading).toBe(false);
      expect(controller.state.value.exported?.error).not.toBe("");
      expect(controller.state.value.exported?.result).toBeNull();
    }
  });

  it("loads every history page and moves only between recorded data without POST or live-state changes", async () => {
    await ready(); const live = controller.state.value.detail;
    const reads: string[] = [];
    const initial = observation({ observation_id: "initial" });
    const next = observation({ observation_id: "next", command_id: "c1", sequence: 1, sim_time: 2 });
    const first = command({ command_id: "c1", state: "succeeded", observation_id: "next" });
    const refused = command({ command_id: "c2", seq: 2, state: "rejected", observation_id: null });
    const unknown = command({ command_id: "c3", seq: 3 });
    setFetch(async (url, init) => {
      expect(init?.method).toBe("GET"); reads.push(url);
      const query = new URL(url, "http://local").searchParams;
      if (url.includes("/commands?")) {
        const cursor = Number(query.get("after_seq"));
        return json(cursor === 0 ? { commands: [first, refused], next_after_seq: 2 } : cursor === 2 ?
          { commands: [unknown], next_after_seq: 3 } : { commands: [], next_after_seq: 3 });
      }
      expect(query.get("full")).toBe("true");
      const cursor = Number(query.get("after_sequence"));
      return json(cursor === -1 ? { observations: [initial], next_after_sequence: 0 } : cursor === 0 ?
        { observations: [next], next_after_sequence: 1 } : { observations: [], next_after_sequence: 1 });
    });
    await controller.loadReplay();
    expect(reads).toHaveLength(6);
    expect(controller.state.value.replay?.entries).toEqual([
      { command: null, observation: initial }, { command: first, observation: next },
      { command: refused, observation: null }, { command: unknown, observation: null },
    ]);
    for (const index of [3, 1, 0, 2]) { controller.selectReplay(index); expect(controller.state.value.replay?.index).toBe(index); }
    controller.selectReplay(99); expect(controller.state.value.replay?.index).toBe(2);
    expect(reads).toHaveLength(6); expect(posts).toHaveLength(0);
    expect(controller.state.value.detail).toBe(live);
  });

  it("fails a non-advancing history cursor without publishing partial replay", async () => {
    await ready();
    setFetch(async (url) => json(url.includes("/commands?") ? { commands: [command()], next_after_seq: 0 } : { observations: [], next_after_sequence: -1 }));
    await controller.loadReplay();
    expect(controller.state.value.replay).toMatchObject({ loading: false, entries: [] });
    expect(controller.state.value.replay?.error).not.toBe("");
  });

  it("discards delayed history and export after root, generation, and away-and-back selection changes", async () => {
    for (const change of [
      async () => controller.scope("other", 1),
      async () => controller.scope("root", 2),
      async () => { await controller.select("labrun-two"); await controller.select("labrun-one"); },
    ]) {
      controller.scope("root", 1, true);
      controller.state.value = { ...controller.state.value, selectedRunId: "labrun-one", runs: [run(), run({ run_id: "labrun-two" })], detail: detail() };
      const history = deferred<Response>(), output = deferred<Response>();
      setFetch(async (url, init) => {
        if (init?.method === "POST") return output.promise;
        if (url.includes("/commands?")) return history.promise;
        if (url.includes("/observations?")) return json({ observations: [], next_after_sequence: -1 });
        if (url.endsWith("/lab")) return json({ devices: [device], runs: [run(), run({ run_id: "labrun-two" })] });
        return json(detail({ run: run({ run_id: url.split("/").at(-1) }) }));
      });
      const playback = controller.loadReplay(), exporting = controller.exportRun();
      await change();
      history.resolve(json({ commands: [], next_after_seq: 0 })); output.resolve(json(exported()));
      await Promise.all([playback, exporting]);
      expect(controller.state.value.replay).toBeNull(); expect(controller.state.value.exported).toBeNull();
    }
  });

  it("ends through the pinned terminal capability and records end_action, while stop records stopped", async () => {
    await ready();
    send = async (url, body) => {
      const ending = url.endsWith("/commands");
      snapshot = detail({ run: run({ status: "ended", end_reason: ending ? "end_action" : "stopped", revision: ending ? 1 : 0 }) });
      return json(ending ? { run: snapshot.run, command: command({ operation: String(body.operation), state: "succeeded" }), observation: null } :
        { run: snapshot.run, stopped: true, semantics: "end_session" });
    };
    await controller.end();
    expect(posts[0]).toMatchObject({ url: "/api/v1/frames/root/lab/runs/labrun-one/commands", body: {
      operation: "end_experiment", source: null, target: null, parameters: {}, expected_revision: 0, idempotency_key: "same-key",
    } });
    expect(controller.state.value.detail?.run.end_reason).toBe("end_action");
    await controller.end(); await controller.stop(); expect(posts).toHaveLength(1);
    controller.scope("root", 1, true); snapshot = detail(); await ready();
    await controller.stop();
    expect(posts[1]?.url).toMatch(/\/stop$/);
    expect(controller.state.value.detail?.run.end_reason).toBe("stopped");
  });

  it("blocks ending when pending, querying, unknown or terminal, but retains safety stop", async () => {
    await ready(); const base = controller.state.value;
    const pending = { intent: { kind: "execute" as const, runId: "labrun-one", body: command().request }, sending: true, error: "" };
    for (const patch of [
      { pending }, { querying: "labcmd-one" }, { detail: detail({ commands: [command()] }) },
      ...(["busy", "quarantined"] as const).map((status) => ({ detail: detail({ run: run({ status }) }) })),
    ]) {
      controller.state.value = { ...base, ...patch };
      expect(canEnd(controller.state.value)).toBe(false); expect(canStop(controller.state.value)).toBe(true);
      await controller.end();
    }
    expect(posts).toHaveLength(0);
    for (const status of ["ended", "failed"] as const) {
      controller.state.value = { ...base, detail: detail({ run: run({ status }) }) };
      expect(canEnd(controller.state.value)).toBe(false); expect(canStop(controller.state.value)).toBe(false);
      await controller.end(); await controller.stop();
    }
    controller.state.value = { ...base, detail: detail({ descriptor: { ...descriptor, capabilities: [] } }) };
    await controller.end(); expect(posts).toHaveLength(0);
    controller.state.value = { ...base, pending, detail: detail({ run: run({ status: "quarantined" }) }) };
    await controller.stop(); expect(posts[0]?.url).toMatch(/\/stop$/);
  });
});
