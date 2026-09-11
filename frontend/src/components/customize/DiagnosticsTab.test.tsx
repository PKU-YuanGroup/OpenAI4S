import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const hookState: unknown[] = [];
let hookCursor = 0;
const effects: Array<() => void> = [];
const cleanups: Array<() => void> = [];

vi.mock("preact/hooks", () => ({
  useState: (initial: unknown) => {
    const index = hookCursor++;
    if (hookState.length === index) hookState.push(initial);
    return [hookState[index], (value: unknown) => {
      hookState[index] = typeof value === "function" ? (value as (prev: unknown) => unknown)(hookState[index]) : value;
    }];
  },
  useRef: (initial: unknown) => {
    const index = hookCursor++;
    if (hookState.length === index) hookState.push({ current: initial });
    return hookState[index];
  },
  useEffect: (fn: () => void | (() => void), deps: unknown[]) => {
    const index = hookCursor++;
    const old = hookState[index] as unknown[] | undefined;
    if (old && deps.every((value, i) => value === old[i])) return;
    hookState[index] = deps;
    effects.push(() => { const cleanup = fn(); if (cleanup) cleanups.push(cleanup); });
  },
}));

const mocks = vi.hoisted(() => ({
  checks: vi.fn(), passive: vi.fn(), fetch: vi.fn(), custTab: vi.fn(),
  alive: (): boolean => true, lang: "en",
}));
vi.mock("../../features/customize/actions", () => ({ custTab: (...args: unknown[]) => mocks.custTab(...args) }));
vi.mock("../../i18n", () => ({ get LANG() { return mocks.lang; }, t: (key: string) => key }));
vi.mock("./use-timer-lease", () => ({ useAlive: () => mocks.alive }));

import { DiagnosticsTab } from "./DiagnosticsTab";
import { api, runDiagnosticsChecks } from "../../features/customize/api";
import { CUST_LOAD_TIMEOUT_MS } from "../../features/customize/load";
import {
  customizeGeneration, diagnosticsAttempt, diagnosticsConfigRevision,
  diagnosticsFailure, diagnosticsRequest, diagnosticsResult,
} from "../../features/customize/state";

type Node = { type?: unknown; props?: Record<string, unknown> & { children?: unknown; onClick?: () => void; disabled?: boolean } };
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const valid = (name = "model", status = "fail", detail = "missing key") => ({
  status, request_id: "req-checks", checks: [{ name, status, detail, remedy: "Configure a model credential", facts: { provider: "example" } }],
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function render(): Node { hookCursor = 0; return DiagnosticsTab() as Node; }
function flushEffects() { effects.splice(0).forEach((effect) => effect()); }
function walk(node: unknown, visit: (current: Node) => void): void {
  if (Array.isArray(node)) { node.forEach((child) => walk(child, visit)); return; }
  if (!node || typeof node !== "object") return;
  const current = node as Node;
  visit(current);
  walk(current.props?.children, visit);
}
function nodes(tree: unknown, predicate: (node: Node) => boolean): Node[] {
  const found: Node[] = [];
  walk(tree, (node) => { if (predicate(node)) found.push(node); });
  return found;
}
function tagged(tree: unknown, key: string, value?: string): Node[] {
  return nodes(tree, (node) => value === undefined ? key in (node.props || {}) : node.props?.[key] === value);
}
function click(tree: unknown, key: string, value?: string) {
  const node = tagged(tree, key, value)[0];
  expect(node).toBeTruthy();
  node?.props?.onClick?.();
}
function content(node: unknown): string {
  if (Array.isArray(node)) return node.map(content).join("");
  if (typeof node === "string" || typeof node === "number") return String(node);
  return node && typeof node === "object" ? content((node as Node).props?.children) : "";
}
async function settled() { await vi.waitFor(() => expect(diagnosticsRequest.value).toBeNull()); }
async function check() { const tree = render(); click(tree, "data-diagnostics-run"); await settled(); return render(); }
function remount() {
  cleanups.splice(0).forEach((cleanup) => cleanup());
  hookState.length = 0;
  effects.length = 0;
  customizeGeneration.value += 1;
  mocks.alive = () => true;
}

beforeEach(() => {
  hookState.length = 0; hookCursor = 0; effects.length = 0; cleanups.length = 0;
  mocks.checks.mockReset().mockResolvedValue(response(valid()));
  mocks.passive.mockReset().mockImplementation(() => Promise.resolve(response({
    security: { kernel_sandbox: "auto", egress: "off" }, environment: {}, request_id: "req-passive",
  })));
  mocks.custTab.mockReset(); mocks.lang = "en"; mocks.alive = () => true;
  customizeGeneration.value = 0; diagnosticsAttempt.value = 0; diagnosticsConfigRevision.value = 0;
  diagnosticsResult.value = null; diagnosticsFailure.value = null; diagnosticsRequest.value = null;
  mocks.fetch.mockReset().mockImplementation((url: string) => {
    if (url.endsWith("/diagnostics/status")) return mocks.passive();
    if (url.endsWith("/diagnostics/checks")) return mocks.checks();
    return Promise.resolve(response({}));
  });
  vi.stubGlobal("fetch", mocks.fetch);
});
afterEach(() => { cleanups.splice(0).forEach((cleanup) => cleanup()); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });

describe("DiagnosticsTab", () => {
  it("opens with one passive GET and no full checks or bundle request", async () => {
    render(); flushEffects();
    await vi.waitFor(() => expect(mocks.passive).toHaveBeenCalledTimes(1));
    render(); flushEffects();
    expect(mocks.fetch.mock.calls.map(([url, init]) => [url, init.method || "GET"]))
      .toEqual([["/api/v1/diagnostics/status", "GET"]]);
  });

  it("renders each status, detail and remedy with the verified per-check setting", async () => {
    mocks.checks.mockResolvedValue(response({ status: "fail", checks: [
      { name: "model", status: "fail", detail: "missing key", remedy: "Configure a model credential" },
      { name: "connectors", status: "warn", detail: "network disabled", remedy: "Enable networking" },
      { name: "remote", status: "warn", detail: "no host", remedy: "Register a host" },
      { name: "runtime", status: "fail", detail: "R unavailable" },
      { name: "disk", status: "ok", detail: "space available" },
    ] }));
    const tree = await check();
    expect(tagged(tree, "data-diagnostic-check")).toHaveLength(5);
    expect(content(tree)).toContain("Configure a model credential");
    for (const button of tagged(tree, "data-diagnostic-setting")) button.props?.onClick?.();
    expect(mocks.custTab.mock.calls.map(([tab]) => tab)).toEqual(["models", "network", "compute", "compute"]);
    expect(mocks.fetch).toHaveBeenCalledTimes(1);
    expect(mocks.fetch.mock.calls[0]?.[1]).toMatchObject({ method: "POST", body: "{}" });
  });

  it("keeps unknown checks and HTML as text, without arbitrary navigation", async () => {
    const html = '<img src=x onerror="alert(1)">';
    mocks.checks.mockResolvedValue(response({ status: "warn", checks: [{
      name: "constructor", status: "warn", detail: html, remedy: "https://untrusted.example/repair", url: "javascript:alert(1)", facts: { html },
    }] }));
    const tree = await check();
    expect(content(tree)).toContain(html);
    expect(content(tree)).toContain("https://untrusted.example/repair");
    expect(tagged(tree, "data-diagnostic-setting")).toHaveLength(0);
    expect(nodes(tree, (node) => "dangerouslySetInnerHTML" in (node.props || {}) || node.type === "img" || node.type === "a")).toHaveLength(0);
  });

  it("limits collapsed facts to 20 keys and 500 characters without losing 0 or false", async () => {
    const facts = Object.fromEntries(Array.from({ length: 25 }, (_, i) => [`key${i}`, i === 0 ? 0 : i === 1 ? false : "x".repeat(800)]));
    mocks.checks.mockResolvedValue(response({ status: "ok", checks: [{ name: "disk", status: "ok", detail: "ready", facts }] }));
    const tree = await check();
    const details = tagged(tree, "data-diagnostic-facts")[0];
    expect(details?.type).toBe("details"); expect(details?.props?.open).toBeUndefined();
    const values = nodes(details, (node) => node.type === "dd").map(content);
    expect(values).toHaveLength(20); expect(values.slice(0, 2)).toEqual(["0", "false"]);
    expect(values.slice(2).every((value) => value.length === 500)).toBe(true);
    expect(content(details)).not.toContain("key20");
  });

  it("blocks repeated clicks synchronously and across tab remounts", async () => {
    const wait = deferred<Response>(); mocks.checks.mockReturnValue(wait.promise);
    const tree = render(); click(tree, "data-diagnostics-run"); click(tree, "data-diagnostics-run");
    expect(mocks.checks).toHaveBeenCalledTimes(1);
    remount(); const next = render(); click(next, "data-diagnostics-run");
    expect(tagged(next, "data-diagnostics-run")[0]?.props?.disabled).toBe(true);
    expect(mocks.checks).toHaveBeenCalledTimes(1);
    wait.resolve(response(valid())); await settled();
    expect(diagnosticsResult.value).toBeNull();
  });

  it("preserves result and its receipt time across tabs, and marks a failed retry as previous", async () => {
    vi.spyOn(Date, "now").mockReturnValue(1234567890000);
    await check(); remount();
    expect(content(render())).toContain("missing key");
    const original = diagnosticsResult.value;
    mocks.checks.mockResolvedValue(response({ error: "operator only", code: "forbidden", request_id: "req-denied" }, 403));
    const tree = await check();
    expect(content(tree)).toContain("operator only");
    expect(tagged(tree, "data-diagnostics-results", "previous")).toHaveLength(1);
    expect(content(tree)).toContain("Previous results");
    expect(diagnosticsResult.value).toBe(original);
    expect(nodes(tree, (node) => node.type === "time")[0]?.props?.dateTime).toBe("2009-02-13T23:31:30.000Z");
    expect(diagnosticsFailure.value?.requestId).toBe("req-denied");
  });

  it("does not let a late passive GET clear a failed explicit check", async () => {
    const passive = deferred<Response>(); mocks.passive.mockReturnValue(passive.promise);
    render(); flushEffects();
    mocks.checks.mockRejectedValue(new DOMException("late", "TimeoutError"));
    await check(); passive.resolve(response({ security: {}, environment: {}, request_id: "late-passive" }));
    await vi.waitFor(() => expect(content(render())).toContain("timed out"));
    expect(diagnosticsResult.value).toBeNull();
  });

  it.each(["success", "failure"])("ignores a check %s after unmount even with an unchanged generation", async (outcome) => {
    const lease = { alive: true }; mocks.alive = () => lease.alive;
    const wait = deferred<Response>(); mocks.checks.mockReturnValue(wait.promise);
    click(render(), "data-diagnostics-run"); lease.alive = false;
    if (outcome === "success") wait.resolve(response(valid())); else wait.reject(new Error("stale failure"));
    await settled();
    expect(diagnosticsResult.value).toBeNull(); expect(diagnosticsFailure.value).toBeNull();
  });

  it("rejects a result overtaken by a configuration save", async () => {
    const wait = deferred<Response>(); mocks.checks.mockReturnValue(wait.promise);
    click(render(), "data-diagnostics-run");
    await api("/network/status", { method: "PUT", body: "{}" });
    wait.resolve(response(valid())); await settled();
    expect(diagnosticsResult.value).toBeNull();
    expect(content(render())).toContain("Configuration changed");
  });

  it("marks cached results as needing recheck after a successful save without running repairs", async () => {
    await check();
    await api("/model-profiles/example", { method: "PATCH", body: "{}" });
    remount(); const tree = render();
    expect(tagged(tree, "data-diagnostics-stale")).toHaveLength(1);
    expect(content(tree)).toContain("Configuration changed");
    expect(mocks.checks).toHaveBeenCalledTimes(1);
    mocks.checks.mockResolvedValue(response(valid("model", "ok", "configured")));
    const fresh = await check();
    expect(tagged(fresh, "data-diagnostics-results", "current")).toHaveLength(1);
    expect(tagged(fresh, "data-diagnostics-stale")).toHaveLength(0);
  });

  it("shows Chinese labels and uses keyboard-native controls", async () => {
    mocks.lang = "zh"; const tree = await check();
    expect(content(tree)).toContain("本次获取时间"); expect(content(tree)).toContain("下一步");
    expect(content(tree)).toContain("失败");
    expect(tagged(tree, "data-diagnostics-run")[0]?.type).toBe("button");
    expect(tagged(tree, "data-diagnostic-setting")[0]?.type).toBe("button");
  });

  it("keeps support bundle download explicit and separate from the check request", async () => {
    const anchor = { href: "", download: "", click: vi.fn(), remove: vi.fn() };
    vi.stubGlobal("document", { createElement: () => anchor, body: { appendChild: vi.fn() } });
    vi.stubGlobal("window", { setTimeout: vi.fn() });
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:diagnostics");
    const tree = render(); expect(mocks.fetch).not.toHaveBeenCalled();
    click(tree, "data-diagnostics-bundle"); await settled();
    expect(mocks.fetch.mock.calls.map(([url]) => url)).toEqual(["/api/v1/diagnostics/bundle"]);
    expect(anchor.click).toHaveBeenCalledTimes(1);
    expect(diagnosticsAttempt.value).toBe(0);
  });
});

describe("diagnostics response and configuration contracts", () => {
  it.each([
    {}, { status: "ok", checks: null }, { status: "ok", checks: [{}] },
    { status: "ok", checks: [null] }, { status: "ok", checks: [{ name: "model", status: "success", detail: "ready" }] },
    { status: "ok", checks: [{ name: "model", status: "ok", detail: {} }] },
    { status: "ok", checks: [{ name: "model", status: "fail", detail: "broken" }] },
    { status: "ok", checks: [{ name: "model", status: "ok", detail: "ready", facts: [] }] },
  ])("rejects malformed or contradictory successful reports: %j", async (body) => {
    mocks.checks.mockResolvedValue(response(body));
    await expect(runDiagnosticsChecks()).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("keeps old valid responses without optional remedy/facts compatible", async () => {
    mocks.checks.mockResolvedValue(response({ status: "ok", checks: [{ name: "model", status: "ok", detail: "ready" }] }));
    expect(await runDiagnosticsChecks()).toEqual({ status: "ok", request_id: "", checks: [{ name: "model", status: "ok", detail: "ready" }] });
  });

  it.each([
    ["/model-profiles", "POST"], ["/model-profiles/example", "PATCH"], ["/model-profiles/example/activate", "POST"],
    ["/network/status", "PUT"], ["/search/config", "POST"], ["/connectors", "POST"],
    ["/connectors/example/enabled", "PUT"], ["/connectors/example", "DELETE"],
    ["/compute/remote", "POST"], ["/compute/remote/example", "DELETE"], ["/permissions", "POST"],
    ["/doubao-search/config", "POST"], ["/datapro/config", "POST"], ["/volcengine/configure", "POST"],
  ])("invalidates a successful config save %s %s", async (path, method) => {
    await api(path, { method }); expect(diagnosticsConfigRevision.value).toBe(1);
  });

  it.each([
    ["/model-profiles", "GET"], ["/model-profiles/example/probe", "POST"], ["/connectors/example/probe", "POST"],
    ["/compute/jobs", "POST"], ["/compute/jobs/example/cancel", "POST"], ["/volcengine/refresh", "POST"],
    ["/doubao-search/search", "POST"], ["/datapro/search", "POST"], ["/kernel/install", "POST"],
  ])("does not invalidate for reads/probes/jobs: %s %s", async (path, method) => {
    await api(path, { method }); expect(diagnosticsConfigRevision.value).toBe(0);
  });

  it("does not invalidate a failed config save", async () => {
    mocks.fetch.mockResolvedValue(response({ error: "denied" }, 403));
    await expect(api("/network/status", { method: "PUT" })).rejects.toMatchObject({ status: 403 });
    expect(diagnosticsConfigRevision.value).toBe(0);
  });
});


describe("diagnostics check deadline", () => {
  it("times out a genuinely pending fetch, preserves prior results and permits a fresh retry", async () => {
    await check();
    const original = diagnosticsResult.value;
    const pending = deferred<Response>();
    // Deliberately ignores abort: the client's wait must remain bounded even
    // when a transport cannot acknowledge cancellation immediately.
    mocks.checks.mockReturnValue(pending.promise);
    vi.useFakeTimers();
    click(render(), "data-diagnostics-run");
    const owner = diagnosticsRequest.value;
    await vi.advanceTimersByTimeAsync(CUST_LOAD_TIMEOUT_MS - 1);
    expect(diagnosticsRequest.value).toBe(owner);
    await vi.advanceTimersByTimeAsync(1);
    expect(diagnosticsRequest.value).toBeNull();
    expect(diagnosticsFailure.value?.code).toBe("timeout");
    expect(diagnosticsResult.value).toBe(original);
    expect(content(render())).toContain("timed out");
    expect(tagged(render(), "data-diagnostics-results", "previous")).toHaveLength(1);
    const signal = mocks.fetch.mock.calls[1]?.[1]?.signal as AbortSignal;
    expect(signal?.aborted).toBe(true);
    expect(mocks.checks).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
    mocks.checks.mockResolvedValue(response(valid("model", "ok", "fresh")));
    click(render(), "data-diagnostics-run");
    await vi.advanceTimersByTimeAsync(0);
    const fresh = diagnosticsResult.value;
    expect(fresh?.report.checks[0]?.detail).toBe("fresh");
    pending.resolve(response(valid("model", "fail", "late old result")));
    await vi.advanceTimersByTimeAsync(0);
    expect(diagnosticsResult.value).toBe(fresh);
    expect(diagnosticsFailure.value).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("bounds response-body reading after headers have already arrived", async () => {
    vi.useFakeTimers();
    const body = deferred<string>();
    const headers = response(valid());
    vi.spyOn(headers, "text").mockReturnValue(body.promise);
    mocks.checks.mockResolvedValue(headers);
    let error: unknown;
    const pending = runDiagnosticsChecks().catch((reason: unknown) => { error = reason; });
    await vi.advanceTimersByTimeAsync(CUST_LOAD_TIMEOUT_MS);
    expect(error).toMatchObject({ name: "TimeoutError" });
    await pending;
    expect((mocks.fetch.mock.calls[0]?.[1]?.signal as AbortSignal)?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    body.resolve(JSON.stringify(valid()));
    await vi.advanceTimersByTimeAsync(0);
  });

  it.each(["success", "HTTP failure", "network failure"])("cleans its timeout after %s", async (outcome) => {
    vi.useFakeTimers();
    if (outcome === "HTTP failure") mocks.checks.mockResolvedValue(response({ error: "forbidden" }, 403));
    if (outcome === "network failure") mocks.checks.mockRejectedValue(new TypeError("offline"));
    await runDiagnosticsChecks().catch(() => undefined);
    const signal = mocks.fetch.mock.calls[0]?.[1]?.signal as AbortSignal;
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal.aborted).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(CUST_LOAD_TIMEOUT_MS);
    expect(signal.aborted).toBe(false);
    expect(mocks.checks).toHaveBeenCalledTimes(1);
  });

  it("retires a timed-out request after unmount without publishing an old failure", async () => {
    vi.useFakeTimers();
    mocks.checks.mockReturnValue(new Promise<Response>(() => {}));
    const lease = { alive: true }; mocks.alive = () => lease.alive;
    click(render(), "data-diagnostics-run");
    lease.alive = false;
    await vi.advanceTimersByTimeAsync(CUST_LOAD_TIMEOUT_MS);
    expect(diagnosticsRequest.value).toBeNull();
    expect(diagnosticsFailure.value).toBeNull();
    expect(diagnosticsResult.value).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });
});
