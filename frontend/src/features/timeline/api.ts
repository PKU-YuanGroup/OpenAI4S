/**
 * Same-origin /api/v1 fetch used by workbench loads and panel mutations.
 * Port of app.js:93-119 and optionalApi 3299-3301.
 */

import { isReady } from "../../compat/stub";
import { API } from "../ws/connect";

export class ApiError extends Error {
  code: string;
  status: number | string;
  requestId: string;
  body: unknown;
  constructor(
    body: { error?: string; detail?: string; code?: string; status?: number; request_id?: string } | null,
    httpStatus: number,
  ) {
    super((body && (body.error || body.detail)) || "HTTP " + httpStatus);
    this.name = "ApiError";
    this.code = (body && body.code) || "";
    this.status = (body && body.status) || httpStatus;
    this.requestId = (body && body.request_id) || "";
    this.body = body;
  }
}

export function apiErrorText(e: unknown): string {
  const err = e as { message?: string; requestId?: string } | null;
  const msg = err && err.message ? String(err.message) : String(e);
  return err && err.requestId ? `${msg} [${err.requestId}]` : msg;
}

export async function api(p: string, o: RequestInit = {}): Promise<unknown> {
  if (typeof p !== "string" || p[0] !== "/" || p[1] === "/")
    throw new Error("invalid api path");
  const r = await fetch(API + p, {
    headers: { "content-type": "application/json" },
    ...o,
  });
  const text = await r.text();
  let j: unknown = null;
  try {
    j = text ? JSON.parse(text) : null;
  } catch {
    j = text;
  }
  if (!r.ok)
    throw new ApiError(
      j && typeof j === "object" ? (j as ApiError["body"] as never) : null,
      r.status,
    );
  return j;
}

export async function optionalApi(paths: string[]): Promise<unknown> {
  for (const path of paths) {
    try {
      return await api(path);
    } catch {
      /* try the next advertised path */
    }
  }
  return null;
}

export function hint(message: string, isError?: boolean): void {
  const fn = (globalThis as { hint?: unknown }).hint;
  if (isReady(fn)) (fn as (m: string, e?: boolean) => void)(message, isError);
}

export function laneCall(name: string, ...args: unknown[]): unknown {
  const fn = (globalThis as Record<string, unknown>)[name];
  if (!isReady(fn)) return undefined;
  return (fn as (...a: unknown[]) => unknown)(...args);
}
