/**
 * Per-mount timer lease. Customize polls (jobs, job output, Volcengine key,
 * clipboard-copy restore) must die with the component that started them.
 *
 * The old app.js parked job polls on `S._jobPoll` and Volcengine polls on the
 * panel DOM node (`root._volcKeyPollTimer`). Switching tabs rebuilt innerHTML
 * but did not always clear those handles. A lease is created on mount and
 * disposed on unmount: every timeout/interval it owns is cleared, and a tick
 * that fires after dispose is a no-op.
 */

export type TimerLease = {
  readonly id: number;
  readonly timeouts: Set<ReturnType<typeof setTimeout>>;
  readonly intervals: Set<ReturnType<typeof setInterval>>;
};

let nextLeaseId = 1;
const live = new Map<number, TimerLease>();

export function createTimerLease(): TimerLease {
  const lease: TimerLease = {
    id: nextLeaseId++,
    timeouts: new Set(),
    intervals: new Set(),
  };
  live.set(lease.id, lease);
  return lease;
}

export function isLeaseLive(lease: TimerLease): boolean {
  return live.get(lease.id) === lease;
}

export function scheduleTimeout(
  lease: TimerLease,
  fn: () => void,
  ms: number,
): ReturnType<typeof setTimeout> | 0 {
  if (!isLeaseLive(lease)) return 0;
  const handle = setTimeout(() => {
    lease.timeouts.delete(handle);
    if (!isLeaseLive(lease)) return;
    fn();
  }, ms);
  lease.timeouts.add(handle);
  return handle;
}

export function scheduleInterval(
  lease: TimerLease,
  fn: () => void,
  ms: number,
): ReturnType<typeof setInterval> | 0 {
  if (!isLeaseLive(lease)) return 0;
  const handle = setInterval(() => {
    if (!isLeaseLive(lease)) {
      clearInterval(handle);
      lease.intervals.delete(handle);
      return;
    }
    fn();
  }, ms);
  lease.intervals.add(handle);
  return handle;
}

export function clearLeaseTimeout(
  lease: TimerLease,
  handle: ReturnType<typeof setTimeout> | 0,
): void {
  if (!handle) return;
  clearTimeout(handle);
  lease.timeouts.delete(handle);
}

export function disposeTimerLease(lease: TimerLease): void {
  for (const handle of lease.timeouts) clearTimeout(handle);
  for (const handle of lease.intervals) clearInterval(handle);
  lease.timeouts.clear();
  lease.intervals.clear();
  live.delete(lease.id);
}

export function liveLeaseCount(): number {
  return live.size;
}

export function pendingTimerCount(): number {
  let n = 0;
  for (const lease of live.values()) n += lease.timeouts.size + lease.intervals.size;
  return n;
}

/** Test helper: drop every lease. Production unmount is per-component. */
export function resetTimerLeases(): void {
  for (const lease of [...live.values()]) disposeTimerLease(lease);
}
