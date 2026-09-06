/**
 * Call a later-lane window export only when it is a real implementation.
 *
 * F-05 placeholders are functions, so `typeof x === "function"` is the wrong
 * guard (F-09 boot crash). Capability checks use `isReady` from stub.ts —
 * that module has no install side effect, unlike window-exports.ts.
 */

import { isReady } from "../../compat/stub";

export function hostFn(name: string): ((...args: never[]) => unknown) | undefined {
  const bag = globalThis as unknown as Record<string, unknown>;
  const fn = bag[name];
  if (!isReady(fn)) return undefined;
  return fn as (...args: never[]) => unknown;
}

export function callLane(name: string, ...args: unknown[]): unknown {
  const fn = hostFn(name);
  if (!fn) return undefined;
  return fn(...(args as never[]));
}

export function hostWindow(): Record<string, unknown> | undefined {
  return (globalThis as unknown as { window?: Record<string, unknown> }).window;
}
