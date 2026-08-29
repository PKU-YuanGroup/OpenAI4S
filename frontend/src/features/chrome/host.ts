/**
 * Window-capability lookups for later lanes.
 *
 * Use `isReady` from `compat/stub.ts` — never `typeof x === "function"`.
 * F-05 fills missing contract names with throwing placeholders that are
 * themselves functions, so a typeof check passes and the call then throws
 * (F-09's boot crash). Do not import `compat/window-exports.ts` from here:
 * that module installs the S Proxy on import.
 */

import { isReady } from "../../compat/stub";

export type AutocompleteState = { open: boolean };

export type ChromeHost = {
  openConversation?: (fid: string, pid?: string | null) => Promise<unknown> | unknown;
  openViewer?: (view: unknown) => void;
  openArtifact?: (view: unknown) => void;
  openCust?: (tab?: string) => void;
  setActiveTab?: (tab: string) => void;
  dockTab?: (tab: string) => void;
  newSession?: () => void;
  openProjectModal?: () => void;
  showDashboard?: () => void;
  loadSessions?: () => Promise<unknown> | unknown;
  loadArtifacts?: (id: string) => void;
  closeProjectModal?: () => void;
  setSidebar?: (collapsed: boolean) => void;
  grow?: () => void;
  hint?: (message: string, err?: boolean, spin?: boolean) => void;
  ac?: AutocompleteState;
};

export function chromeHost(): ChromeHost {
  const g = globalThis as unknown as { window?: ChromeHost };
  return (g.window || (globalThis as unknown as ChromeHost)) as ChromeHost;
}

/** Look up a window capability. Gate calls with `isReady`. */
export function hostFn<K extends keyof ChromeHost>(name: K): ChromeHost[K] {
  return chromeHost()[name];
}

/**
 * Call a window capability only when `isReady` says it is real.
 * F-05 stubs are functions; `typeof === "function"` is not a gate.
 */
export function invokeHost(fn: unknown, ...args: unknown[]): unknown {
  if (!isReady(fn)) return undefined;
  return (fn as (...inner: unknown[]) => unknown)(...args);
}

export { isReady };
