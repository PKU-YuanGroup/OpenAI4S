/**
 * Reserved-placeholder marking, kept free of side effects.
 *
 * This lives apart from `window-exports.ts` because that module installs
 * itself onto `window` on import. A lane that only wants to ask "is this
 * capability real yet?" must not be made to run that installation as a
 * side effect of asking -- doing so replaced a test's own `window.S` with
 * the Proxy and silently dropped the 3Dmol viewer it had mounted there.
 */

const STUB_MARK = "__openai4sContractStub";

/** A callable placeholder that fails loudly if a lane actually calls it. */
export function contractStub(name: string): (..._args: unknown[]) => never {
  const fn = (..._args: unknown[]): never => {
    throw new Error(
      `F-05 stub: window.${name} is reserved for a later lane; assign the real implementation in the lane-additions region of window-exports.ts`,
    );
  };
  Object.defineProperty(fn, "name", { value: name });
  Object.defineProperty(fn, STUB_MARK, { value: true });
  return fn;
}

/**
 * Is this a reserved placeholder rather than a working implementation?
 *
 * A stub is a function, so `typeof x === "function"` says yes and the call
 * then throws. F-09's theme toggle guarded exactly that way, passed, called
 * it, and took the whole boot down before `bootWs()` ever ran.
 */
export function isContractStub(value: unknown): boolean {
  if (typeof value !== "function") return false;
  return (value as unknown as Record<string, unknown>)[STUB_MARK] === true;
}

/** Present, callable, and not a placeholder. Guard optional capabilities with this. */
export function isReady<T>(value: T | undefined | null): value is T {
  return typeof value === "function" && !isContractStub(value);
}
