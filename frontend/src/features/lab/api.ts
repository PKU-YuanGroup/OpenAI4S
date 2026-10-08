import { API, ApiError } from "../sessions/api";
import { labT } from "./copy";
import type {
  CommandPage, CommandRequest, CommandResult, CreateRequest, CreateResult,
  Descriptor, DetailResult, EventPage, LabIndex, ObservationPage, StopResult,
} from "./types";

export type FetchFn = (input: string, init?: RequestInit) => Promise<Response>;
export const LAB_REQUEST_TIMEOUT_MS = 30_000;
let fetchImpl: FetchFn | null = null;

export function setLabFetch(fn: FetchFn | null): void {
  fetchImpl = fn;
}

async function request<T>(path: string, body?: unknown): Promise<T> {
  const fetcher = fetchImpl ?? (globalThis as { fetch?: FetchFn }).fetch;
  if (!fetcher) throw new Error(labT("apiUnavailable"));
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      const error = new Error(labT("apiTimeout"));
      error.name = "TimeoutError";
      reject(error);
    }, LAB_REQUEST_TIMEOUT_MS);
  });
  const send = async (): Promise<T> => {
    const response = await fetcher(API + path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "content-type": "application/json" },
      signal: controller.signal,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const text = await response.text();
    let data: unknown = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = text;
    }
    if (!response.ok) throw new ApiError(data, response.status);
    return data as T;
  };
  try {
    // Also bound body reads and injected transports that do not honor abort.
    return await Promise.race([send(), deadline]);
  } finally {
    clearTimeout(timer);
  }
}

function base(fid: string): string {
  return `/frames/${encodeURIComponent(fid)}/lab`;
}

function runPath(fid: string, runId: string): string {
  return `${base(fid)}/runs/${encodeURIComponent(runId)}`;
}

export function listLab(fid: string): Promise<LabIndex> {
  return request(base(fid));
}

export function describeDevice(fid: string, deviceId: string, profile: string): Promise<{ descriptor: Descriptor }> {
  const query = new URLSearchParams({ profile });
  return request(`${base(fid)}/devices/${encodeURIComponent(deviceId)}?${query}`);
}

export function createRun(fid: string, input: CreateRequest): Promise<CreateResult> {
  const { device_id, profile, seed, budgets, idempotency_key } = input;
  return request(`${base(fid)}/runs`, { device_id, profile, seed, budgets, idempotency_key });
}

export function getRun(fid: string, runId: string): Promise<DetailResult> {
  return request(runPath(fid, runId));
}

export function listCommands(fid: string, runId: string, afterSeq = 0, limit = 50): Promise<CommandPage> {
  const query = new URLSearchParams({ after_seq: String(afterSeq), limit: String(limit) });
  return request(`${runPath(fid, runId)}/commands?${query}`);
}

export function listObservations(
  fid: string, runId: string, afterSequence = -1, limit = 20, full = true,
): Promise<ObservationPage> {
  const query = new URLSearchParams({ after_sequence: String(afterSequence), limit: String(limit), full: String(full) });
  return request(`${runPath(fid, runId)}/observations?${query}`);
}

export function executeCommand(fid: string, runId: string, input: CommandRequest): Promise<CommandResult> {
  const { operation, source, target, parameters, expected_revision, idempotency_key } = input;
  return request(`${runPath(fid, runId)}/commands`, {
    operation, source, target, parameters, expected_revision, idempotency_key,
  });
}

export function reconcileCommand(fid: string, runId: string, commandId: string): Promise<CommandResult> {
  return request(`${runPath(fid, runId)}/commands/${encodeURIComponent(commandId)}/reconcile`, {});
}

export function stopRun(fid: string, runId: string, reason?: string): Promise<StopResult> {
  return request(`${runPath(fid, runId)}/stop`, { reason });
}

export function listEvents(fid: string, afterSeq = 0, limit = 200): Promise<EventPage> {
  const query = new URLSearchParams({ after_seq: String(afterSeq), limit: String(limit) });
  return request(`${base(fid)}/events?${query}`);
}
