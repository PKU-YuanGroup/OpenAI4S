/**
 * Window-capability lookups. Use `isReady` from `compat/stub.ts` — never
 * `typeof x === "function"`. Do not import `compat/window-exports.ts`.
 */

import { isReady } from "../compat/stub";
import { t } from "../i18n/runtime";

export function hostBag(): Record<string, unknown> {
  const g = globalThis as unknown as { window?: Record<string, unknown> };
  return (g.window || (globalThis as unknown as Record<string, unknown>)) as Record<
    string,
    unknown
  >;
}

export function callWindow(name: string, ...args: unknown[]): unknown {
  const fn = hostBag()[name];
  if (!isReady(fn)) return undefined;
  return (fn as (...inner: unknown[]) => unknown)(...args);
}

export function translate(key: string, ...args: unknown[]): string {
  const fromWindow = hostBag().t;
  if (isReady(fromWindow)) return (fromWindow as typeof t)(key, ...args);
  return t(key, ...args);
}

export { isReady };
