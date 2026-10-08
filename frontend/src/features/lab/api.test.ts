import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../sessions/api";
import {
  createRun, describeDevice, executeCommand, getRun, LAB_CREATE_TIMEOUT_MS, LAB_EXECUTE_TIMEOUT_MS,
  LAB_REQUEST_TIMEOUT_MS, LAB_STOP_TIMEOUT_MS, listCommands, listEvents, listLab, listObservations,
  reconcileCommand, setLabFetch, stopRun, type FetchFn,
} from "./api";
import { labT } from "./copy";
import type { CommandRequest, CreateRequest } from "./types";

const fid = "frame /?#中";
const runId = "run /?#中";
const commandId = "command /?#中";
const root = "/api/v1/frames/frame%20%2F%3F%23%E4%B8%AD/lab";
const runUrl = `${root}/runs/run%20%2F%3F%23%E4%B8%AD`;
const create: CreateRequest = {
  device_id: "chemgym.extractor.01", profile: "WaterOilExtract-v0", seed: 0,
  budgets: { max_steps: 12 }, idempotency_key: "create-intent-1",
};
const command: CommandRequest = {
  operation: "transfer_liquid", source: "beaker_1", target: "extraction_vessel",
  parameters: { volume: { value: 200, unit: "mL" } },
  expected_revision: 7, idempotency_key: "command-intent-1",
};

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  setLabFetch(null);
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Lab REST client", () => {
  it("reads every route with encoded identities, profile and pagination defaults or overrides", async () => {
    const cases: Array<[() => Promise<unknown>, string, unknown]> = [
      [() => listLab(fid), root, { devices: [], runs: [], latest_event_seq: 4 }],
      [() => describeDevice(fid, "device /?#中", "Water Oil/+?"),
        `${root}/devices/device%20%2F%3F%23%E4%B8%AD?profile=Water+Oil%2F%2B%3F`, { descriptor: { device_id: "device /?#中" } }],
      [() => getRun(fid, runId), runUrl,
        { run: { run_id: runId }, descriptor: {}, observation: null, commands: [] }],
      [() => listCommands(fid, runId), `${runUrl}/commands?after_seq=0&limit=50`, { commands: [], next_after_seq: 0 }],
      [() => listCommands(fid, runId, 8, 9), `${runUrl}/commands?after_seq=8&limit=9`, { commands: [], next_after_seq: 8 }],
      [() => listObservations(fid, runId), `${runUrl}/observations?after_sequence=-1&limit=20&full=true`,
        { observations: [], next_after_sequence: -1 }],
      [() => listObservations(fid, runId, 3, 4, false), `${runUrl}/observations?after_sequence=3&limit=4&full=false`,
        { observations: [], next_after_sequence: 3 }],
      [() => listEvents(fid), `${root}/events?after_seq=0&limit=200`, { events: [], next_after_seq: 0, latest_event_seq: 0 }],
      [() => listEvents(fid, 2, 5), `${root}/events?after_seq=2&limit=5`, { events: [], next_after_seq: 2, latest_event_seq: 2 }],
    ];
    for (const [call, url, result] of cases) {
      const fetcher = vi.fn(async () => json(result));
      setLabFetch(fetcher);
      await expect(call()).resolves.toEqual(result);
      expect(fetcher).toHaveBeenCalledExactlyOnceWith(url, {
        method: "GET", headers: { "content-type": "application/json" }, signal: expect.any(AbortSignal),
      });
      expect(vi.getTimerCount()).toBe(0);
    }
  });

  it("posts only REST fields and preserves explicit revision, units, seed and idempotency keys", async () => {
    const commandWithRun = { ...command, run_id: "must-stay-in-path" };
    const createWithOptions = { ...create, options: { sleep_on_execute_s: 1 } };
    const cases: Array<[() => Promise<unknown>, string, unknown]> = [
      [() => createRun(fid, createWithOptions), `${root}/runs`, create],
      [() => createRun(fid, { device_id: create.device_id, profile: create.profile, idempotency_key: create.idempotency_key }),
        `${root}/runs`, { device_id: create.device_id, profile: create.profile, idempotency_key: create.idempotency_key }],
      [() => executeCommand(fid, runId, commandWithRun), `${runUrl}/commands`, command],
      [() => executeCommand(fid, runId, { operation: "end_experiment", source: null, target: null, parameters: {}, expected_revision: 0, idempotency_key: "end-intent" }),
        `${runUrl}/commands`, { operation: "end_experiment", source: null, target: null, parameters: {}, expected_revision: 0, idempotency_key: "end-intent" }],
      [() => reconcileCommand(fid, runId, commandId), `${runUrl}/commands/command%20%2F%3F%23%E4%B8%AD/reconcile`, {}],
      [() => stopRun(fid, runId, "manual stop"), `${runUrl}/stop`, { reason: "manual stop" }],
      [() => stopRun(fid, runId), `${runUrl}/stop`, {}],
    ];
    for (const [call, url, body] of cases) {
      const result = { run: { run_id: runId } };
      const fetcher = vi.fn(async () => json(result));
      setLabFetch(fetcher);
      await expect(call()).resolves.toEqual(result);
      expect(fetcher).toHaveBeenCalledExactlyOnceWith(url, {
        method: "POST", headers: { "content-type": "application/json" },
        signal: expect.any(AbortSignal), body: JSON.stringify(body),
      });
      expect(vi.getTimerCount()).toBe(0);
    }
  });

  it.each(["rejected", "failed", "outcome_unknown"])("preserves a 200 %s command as a normal result", async (state) => {
    const result = {
      run: { run_id: runId, status: state === "outcome_unknown" ? "quarantined" : "ready", revision: 7 },
      command: {
        command_id: commandId, state, idempotency_key: command.idempotency_key,
        error_code: state === "outcome_unknown" ? "outcome_unknown" : "invalid_parameters",
        error: "volume: allowed values are 200, 400 mL", receipt: null,
      },
      observation: null,
    };
    const fetcher = vi.fn(async () => json(result));
    setLabFetch(fetcher);
    await expect(executeCommand(fid, runId, command)).resolves.toEqual(result);
    await expect(reconcileCommand(fid, runId, commandId)).resolves.toEqual(result);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("preserves structured HTTP errors, details and request IDs without retrying", async () => {
    const codes: Array<[number, string]> = [
      [422, "invalid_parameters"], [404, "run_not_found"], [409, "idempotency_conflict"],
      [403, "replay_forbidden"], [423, "recovery_in_progress"], [503, "persistence_unavailable"],
    ];
    for (const [status, code] of codes) {
      const body = { error: "Lab request refused", code, status, request_id: "request-1", details: { command_id: commandId } };
      const fetcher = vi.fn(async () => json(body, status));
      setLabFetch(fetcher);
      const error = await executeCommand(fid, runId, command).catch((e: unknown) => e);
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ message: body.error, code, status, requestId: "request-1", body });
      expect(fetcher).toHaveBeenCalledTimes(1);
      expect(vi.getTimerCount()).toBe(0);
    }
  });

  it("retains non-JSON and empty HTTP error bodies", async () => {
    for (const [text, body] of [["upstream unavailable", "upstream unavailable"], ["", null]] as const) {
      setLabFetch(async () => new Response(text, { status: 503 }));
      const error = await listLab(fid).catch((e: unknown) => e);
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ status: 503, body });
      expect(vi.getTimerCount()).toBe(0);
    }
  });

  it("leaves network retries to the caller and sends the same retained key when called again", async () => {
    const offline = new TypeError("offline");
    const fetcher = vi.fn<FetchFn>()
      .mockRejectedValueOnce(offline)
      .mockResolvedValueOnce(json({ run: { run_id: runId }, command: null, observation: null }));
    setLabFetch(fetcher);
    await expect(executeCommand(fid, runId, command)).rejects.toBe(offline);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
    await executeCommand(fid, runId, command);
    expect(fetcher).toHaveBeenCalledTimes(2);
    for (const [, init] of fetcher.mock.calls) {
      expect(JSON.parse(String(init?.body))).toEqual(command);
    }
    expect(vi.getTimerCount()).toBe(0);
  });

  it("bounds a stalled transport, aborts its request and clears the deadline without retrying", async () => {
    let signal: AbortSignal | null | undefined;
    const fetcher = vi.fn((_input: string, init?: RequestInit) => {
      signal = init?.signal;
      return new Promise<Response>(() => {});
    });
    setLabFetch(fetcher);
    const result = executeCommand(fid, runId, command);
    const failure = expect(result).rejects.toMatchObject({ name: "TimeoutError", message: labT("apiTimeout") });
    expect(vi.getTimerCount()).toBe(1);
    expect(signal?.aborted).toBe(false);
    await vi.advanceTimersToNextTimerAsync();
    await failure;
    expect(signal?.aborted).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("waits for mutations past the provider's own open, execute and stop deadlines", async () => {
    // openai4s_lab_provider/client.py: hello 30 s + open 180 s, execute 60 s, stop 30 s + close 10 s.
    expect(LAB_CREATE_TIMEOUT_MS).toBeGreaterThan((30 + 180) * 1000);
    expect(LAB_EXECUTE_TIMEOUT_MS).toBeGreaterThan(60 * 1000);
    expect(LAB_STOP_TIMEOUT_MS).toBeGreaterThan((30 + 10) * 1000);
    setLabFetch(() => new Promise<Response>(() => {}));
    for (const [call, deadline] of [
      [() => createRun(fid, create), LAB_CREATE_TIMEOUT_MS],
      [() => executeCommand(fid, runId, command), LAB_EXECUTE_TIMEOUT_MS],
      [() => stopRun(fid, runId), LAB_STOP_TIMEOUT_MS],
      [() => listLab(fid), LAB_REQUEST_TIMEOUT_MS],
    ] as const) {
      let settled = false;
      const result = call().catch((error: unknown) => { settled = true; throw error; });
      const failure = expect(result).rejects.toMatchObject({ name: "TimeoutError" });
      await vi.advanceTimersByTimeAsync(deadline - 1);
      expect(settled).toBe(false);
      await vi.advanceTimersByTimeAsync(1);
      await failure;
      expect(vi.getTimerCount()).toBe(0);
    }
  });

  it("keeps the deadline active while the response body is stalled", async () => {
    let signal: AbortSignal | null | undefined;
    setLabFetch(async (_input, init) => {
      signal = init?.signal;
      const response = json({ devices: [], runs: [], latest_event_seq: 0 });
      vi.spyOn(response, "text").mockImplementation(() => new Promise<string>(() => {}));
      return response;
    });
    const result = listLab(fid);
    const failure = expect(result).rejects.toMatchObject({ name: "TimeoutError" });
    await vi.advanceTimersToNextTimerAsync();
    await failure;
    expect(signal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("restores the native fetch seam and fails clearly when fetch is unavailable", async () => {
    const native = vi.fn(async () => json({ devices: [], runs: [], latest_event_seq: 4 }));
    const injected = vi.fn(async () => json({ devices: [], runs: [], latest_event_seq: 2 }));
    vi.stubGlobal("fetch", native);
    setLabFetch(injected);
    await expect(listLab(fid)).resolves.toMatchObject({ latest_event_seq: 2 });
    setLabFetch(null);
    await expect(listLab(fid)).resolves.toMatchObject({ latest_event_seq: 4 });
    expect(injected).toHaveBeenCalledTimes(1);
    expect(native).toHaveBeenCalledTimes(1);
    vi.stubGlobal("fetch", undefined);
    await expect(listLab(fid)).rejects.toThrow(labT("apiUnavailable"));
    expect(vi.getTimerCount()).toBe(0);
  });
});
