import { useEffect, useRef } from "preact/hooks";
import {
  createTimerLease,
  disposeTimerLease,
  type TimerLease,
} from "../../features/customize/timers";

/** One lease per component instance; dispose on unmount (zero leftover timers). */
export function useTimerLease(): TimerLease {
  const ref = useRef<TimerLease | null>(null);
  if (ref.current === null) ref.current = createTimerLease();
  useEffect(() => {
    const lease = ref.current;
    return () => {
      if (lease) disposeTimerLease(lease);
    };
  }, []);
  return ref.current;
}

export function useAlive(): () => boolean {
  const flag = useRef(true);
  const check = useRef(() => flag.current);
  useEffect(() => {
    flag.current = true;
    return () => {
      flag.current = false;
    };
  }, []);
  return check.current;
}
