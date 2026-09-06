/**
 * Window-capability lookups. Use `isReady` from `compat/stub.ts` — never
 * `typeof x === "function"`. Do not import `compat/window-exports.ts`.
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

export function setCancelHidden(hidden: boolean): void {
  if (typeof document === "undefined") return;
  const btn = document.getElementById("cancel-btn");
  if (btn) btn.classList.toggle("hidden", hidden);
}
