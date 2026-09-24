import { useEffect, useRef, useState } from "preact/hooks";
import { t } from "../../i18n";
import { hint } from "../../features/customize/host";
import { markCustomizeFailed, markCustomizeLoaded } from "../../features/customize/load";
import { customizeRefresh } from "../../features/customize/state";
import type { CustTab } from "../../features/customize/tabs";
import { useAlive } from "./use-timer-lease";

/**
 * A tab's data: read on mount, and read again in place whenever
 * `refreshCustTab(tab)` asks (after a write) or a value in `deps` changes.
 * Only the newest read may apply its answer, and only while the tab is
 * mounted: `read` gets `current()` to check after each await. The first read
 * settles the pane's load status and reports a failure through `failed`; a
 * later read that fails keeps what is shown and says so.
 */
export function useTabRead(
  tab: CustTab,
  read: (current: () => boolean) => Promise<void>,
  failed: (message: string) => void,
  deps: readonly unknown[] = [],
): void {
  const alive = useAlive();
  const seq = useRef(0);
  const loaded = useRef(false);
  const nonce = customizeRefresh.value[tab] || 0;
  useEffect(() => {
    const mine = ++seq.current;
    const current = () => alive() && seq.current === mine;
    const first = !loaded.current;
    void (async () => {
      try {
        await read(current);
        if (!current()) return;
        loaded.current = true;
        markCustomizeLoaded();
      } catch (e) {
        if (!current()) return;
        const message = t("versions.load.err", (e as Error).message);
        if (first) {
          failed(message);
          markCustomizeFailed(message);
        } else {
          hint(message, true);
        }
      }
    })();
  }, [alive, nonce, ...deps]);
}

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
