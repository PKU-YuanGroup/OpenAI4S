import { useRef, useState } from "preact/hooks";
import { useAlive } from "./use-timer-lease";

export type OptimisticHandlers<T> = {
  /** The write succeeded; `result` is what it answered. */
  done?: (next: T, result: unknown) => void;
  /** The write failed; the control is already back on the confirmed value. */
  failed?: (error: unknown, next: T) => void;
};

/**
 * A control bound to one server setting.
 *
 * `server` is what the tab read, `null` until that read lands, and the control
 * is not `ready` until then: a write made before the first read answered was
 * overwritten by that read when it landed late, so a network or memory switch
 * showed the opposite of what the server held. `set` moves the control at
 * once and writes; while the write is in flight a second change is ignored;
 * a failed write puts back the last value the server confirmed. A read that
 * brings a different value is adopted unless a write is in flight.
 */
export function useOptimistic<T>(
  server: T | null,
  write: (next: T) => Promise<unknown>,
  handlers: OptimisticHandlers<T> = {},
): {
  value: T | null;
  ready: boolean;
  busy: boolean;
  set: (next: T) => void;
  confirm: (value: T) => void;
} {
  const alive = useAlive();
  const [, rerender] = useState(0);
  const ref = useRef<{ seen: T | null; own: T | null; busy: boolean }>({
    seen: server,
    own: null,
    busy: false,
  });
  const s = ref.current;
  if (server !== s.seen) {
    s.seen = server;
    if (!s.busy) s.own = null;
  }
  const current = (): T | null => (s.own !== null ? s.own : s.seen);
  const bump = () => {
    if (alive()) rerender((n) => n + 1);
  };
  const set = (next: T) => {
    const previous = current();
    if (previous === null || s.busy || next === previous) return;
    s.busy = true;
    s.own = next;
    bump();
    write(next).then(
      (result) => {
        s.busy = false;
        bump();
        handlers.done?.(next, result);
      },
      (error: unknown) => {
        s.busy = false;
        s.own = previous;
        bump();
        handlers.failed?.(error, next);
      },
    );
  };
  /** Record a value the server confirmed some other way (a related write). */
  const confirm = (value: T) => {
    s.own = value;
    bump();
  };
  return { value: current(), ready: server !== null, busy: s.busy, set, confirm };
}

/** `useOptimistic` for an on/off switch. */
export function useOptimisticToggle(
  server: boolean | null,
  write: (next: boolean) => Promise<unknown>,
  handlers: OptimisticHandlers<boolean> = {},
): { on: boolean; ready: boolean; busy: boolean; toggle: () => void; confirm: (value: boolean) => void } {
  const state = useOptimistic(server, write, handlers);
  return {
    on: state.value === true,
    ready: state.ready,
    busy: state.busy,
    toggle: () => state.set(state.value !== true),
    confirm: state.confirm,
  };
}
