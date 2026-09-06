/**
 * Fork / branch 409 presentation. Gateway returns HTTP 409
 * `historical source has no exact cursor checkpoint` when fork-from-cell
 * (or fork-from-message) has no exact cursor checkpoint — deliberate, not a
 * retryable fault (app.js / gateway.py CursorCheckpointUnavailable).
 *
 * The UI must surface the server sentence, never retry, never rewrite the
 * 409 into success / empty / a generic "try again".
 */

import { publicText } from "../scrub/scrub";

export const FORK_NO_CHECKPOINT_MESSAGE =
  "historical source has no exact cursor checkpoint";

const NO_CHECKPOINT_RE = /no exact cursor checkpoint/i;

export type ForkPresentation = {
  kind: "conflict" | "error";
  noCheckpoint: boolean;
  httpStatus: number | null;
  code: string;
  message: string;
  retry: false;
  masked: false;
};

function rec(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object") return null;
  return value as Record<string, unknown>;
}

/** HTTP status only. A recovery domain `status: "failed"` string is not 409. */
export function httpStatusOf(error: unknown): number | null {
  const bag = rec(error);
  if (!bag) return null;
  const raw = bag.status;
  if (typeof raw === "number" && Number.isFinite(raw) && raw >= 100 && raw <= 599) {
    return raw;
  }
  if (typeof raw === "string" && /^\d{3}$/.test(raw)) return Number(raw);
  return null;
}

export function errorCodeOf(error: unknown): string {
  const bag = rec(error);
  return bag && bag.code != null ? String(bag.code) : "";
}

export function errorMessageOf(error: unknown): string {
  const bag = rec(error);
  const raw =
    (bag && (bag.message || bag.error || bag.detail)) ||
    (error instanceof Error ? error.message : error);
  return publicText(raw, 240);
}

export function isForkNoCheckpoint(error: unknown): boolean {
  const status = httpStatusOf(error);
  const code = errorCodeOf(error);
  const message = errorMessageOf(error);
  return (
    status === 409 ||
    code === "conflict" ||
    NO_CHECKPOINT_RE.test(message)
  );
}

/**
 * Present a fork (or branch-mutation) failure. `retry` is always false:
 * a 409 from a missing cursor checkpoint must not be retried or papered over.
 */
export function presentForkError(error: unknown): ForkPresentation {
  const httpStatus = httpStatusOf(error);
  const code = errorCodeOf(error);
  const message = errorMessageOf(error);
  const noCheckpoint = NO_CHECKPOINT_RE.test(message);
  const isConflict = httpStatus === 409 || code === "conflict" || noCheckpoint;
  return {
    kind: isConflict ? "conflict" : "error",
    noCheckpoint,
    httpStatus: httpStatus ?? (isConflict && noCheckpoint ? 409 : httpStatus),
    code: code || (isConflict ? "conflict" : ""),
    message,
    retry: false,
    masked: false,
  };
}

export function shouldRetryFork(_presentation: ForkPresentation): boolean {
  return false;
}

/** Display text: the server sentence, never replaced with a generic key. */
export function forkErrorDisplay(presentation: ForkPresentation): string {
  return presentation.message;
}

export type ForkAttempt<T = unknown> =
  | { ok: true; result: T; attempts: 1 }
  | { ok: false; presentation: ForkPresentation; attempts: 1 };

/**
 * Run a fork POST exactly once. A 409 is returned as a presentation;
 * the thunk is never invoked again.
 */
export async function forkOnce<T>(post: () => Promise<T>): Promise<ForkAttempt<T>> {
  try {
    const result = await post();
    return { ok: true, result, attempts: 1 };
  } catch (error) {
    return { ok: false, presentation: presentForkError(error), attempts: 1 };
  }
}
