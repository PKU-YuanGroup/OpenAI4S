/**
 * Admission tracker. Port of app.js:9016-9143.
 *
 * localStorage keys `openai4s.admission.*` are frozen. Independent keys, never
 * a container: a write touches one reservation so concurrent tabs cannot
 * clobber each other. `outstandingAdmissions` stays on window.
 */

import { currentId } from "../../stores/session";
import { api } from "../sessions/api";
import { callLane } from "./host";

export const ADMISSION_LEGACY_KEY = (fid: string): string =>
  "openai4s.admission." + fid;
export const ADMISSION_PREFIX = (fid: string): string =>
  "openai4s.admission." + fid + ".";

/**
 * How long a just-minted admission is protected from another tab's 404.
 * Past the grace a 404 is taken at face value.
 */
export const ADMISSION_GRACE_MS = 60_000;

function storage(): Storage | null {
  try {
    const s = (globalThis as { localStorage?: Storage }).localStorage;
    return s || null;
  } catch {
    return null;
  }
}

/**
 * The scalar and the list a tab may still be holding when it reloads into
 * this build — which is precisely a client with something outstanding, so
 * dropping them would lose the comments this whole mechanism exists to
 * recover.
 */
export function migrateAdmissions(fid: string): void {
  const store = storage();
  if (!store) return;
  let raw: string | null = null;
  try {
    raw = store.getItem(ADMISSION_LEGACY_KEY(fid));
  } catch {
    return;
  }
  if (!raw) return;
  let ids: string[] = [];
  if (raw[0] === "[") {
    try {
      const parsed: unknown = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        ids = parsed.filter((x): x is string => typeof x === "string" && !!x);
      }
    } catch {
      /* unparseable list stays as nothing to migrate */
    }
  } else ids = [raw];
  for (const id of ids) rememberAdmission(fid, id);
  try {
    store.removeItem(ADMISSION_LEGACY_KEY(fid));
  } catch {
    /* quota / private mode */
  }
}

export function outstandingAdmissions(fid: string): string[] {
  migrateAdmissions(fid);
  const prefix = ADMISSION_PREFIX(fid);
  const found: Array<[string, string | null]> = [];
  const store = storage();
  if (!store) return [];
  try {
    for (let i = 0; i < store.length; i++) {
      const key = store.key(i);
      if (key && key.startsWith(prefix)) {
        found.push([key.slice(prefix.length), store.getItem(key)]);
      }
    }
  } catch {
    return [];
  }
  // Oldest first, by the stamp written at mint time. Ties break on the id:
  // `Date.now()` has millisecond resolution and several sends can share one,
  // in which case sorting on the stamp alone falls back to storage iteration
  // order -- which is not defined, so "oldest first" would be a claim the code
  // does not keep.
  found.sort(
    (a, b) =>
      (Number(a[1]) || 0) - (Number(b[1]) || 0) ||
      (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0),
  );
  return found.map((pair) => pair[0]).filter(Boolean);
}

export function admissionAge(fid: string, id: string): number | null {
  const store = storage();
  let raw: string | null = null;
  try {
    raw = store ? store.getItem(ADMISSION_PREFIX(fid) + id) : null;
  } catch {
    return null;
  }
  const minted = Number(raw);
  // A missing, unparseable or future stamp is not a lease. Trusting one would
  // make a corrupted value protect a key permanently.
  if (!raw || !Number.isFinite(minted) || minted <= 0 || minted > Date.now()) {
    return null;
  }
  return Date.now() - minted;
}

export function admissionWithinGrace(fid: string, id: string): boolean {
  const age = admissionAge(fid, id);
  return age !== null && age < ADMISSION_GRACE_MS;
}

export function rememberAdmission(fid: string, id: string): void {
  // A single independent write. No read, so nothing to lose a race with.
  try {
    storage()?.setItem(ADMISSION_PREFIX(fid) + id, String(Date.now()));
  } catch {
    /* quota / private mode */
  }
}

export function forgetAdmission(fid: string, id: string): void {
  try {
    storage()?.removeItem(ADMISSION_PREFIX(fid) + id);
  } catch {
    /* quota / private mode */
  }
}

/**
 * Whether an answer settles an admission. `sent`, `released` and `none` are
 * decided; `pending` is undecided by definition, and an unrecognised state is
 * not evidence of anything — dropping either throws away the only handle the
 * client has on those comments.
 */
export function admissionSettled(state: unknown): boolean {
  return state === "sent" || state === "released" || state === "none";
}

/**
 * One pending retry per session, so N unresolved ids schedule one sweep.
 *
 * Without the de-dupe every 404 inside the lease would arm its own timer and a
 * tab with several outstanding sends would re-ask N times per round.
 */
const admissionRetries = new Map<string, ReturnType<typeof setTimeout>>();

export function resetAdmissionRetries(): void {
  for (const timer of admissionRetries.values()) clearTimeout(timer);
  admissionRetries.clear();
}

export function scheduleAdmissionRetry(fid: string): void {
  if (admissionRetries.has(fid)) return;
  admissionRetries.set(
    fid,
    setTimeout(() => {
      admissionRetries.delete(fid);
      // Only if the session is still the one on screen; reconciling a session
      // the user has left would fight whatever is now open.
      if (currentId.value === fid) void reconcileLastAdmission(fid).catch(() => {});
    }, 3000),
  );
}

export async function reconcileLastAdmission(fid: string): Promise<unknown> {
  const outstanding = outstandingAdmissions(fid);
  if (!outstanding.length) return null;
  const records: unknown[] = [];
  for (const reservation of outstanding) {
    let record: unknown = null;
    try {
      record = await api(
        `/frames/${fid}/admissions/${encodeURIComponent(reservation)}`,
      );
    } catch (e) {
      // 404 means this session has no such admission. That is true both for a
      // stale id and for one whose POST has not left another tab yet, and the
      // second must not be deleted — see ADMISSION_GRACE_MS. Within the lease
      // the key is kept and re-asked; past it, taken at face value. Anything
      // else (offline, 5xx) always leaves it for the next attempt rather than
      // dropping the only handle on the comments.
      const err = e as { status?: number } | null;
      if (err && err.status === 404) {
        if (admissionWithinGrace(fid, reservation)) scheduleAdmissionRetry(fid);
        else forgetAdmission(fid, reservation);
      }
      continue;
    }
    records.push(record);
    const rec = record && typeof record === "object" ? (record as { state?: unknown }) : null;
    if (admissionSettled(rec && rec.state)) forgetAdmission(fid, reservation);
  }
  await callLane("loadAnnotations", fid);
  callLane("refreshAllStages");
  callLane("updateAnnotBadge");
  // The most recent decided record, for callers that want one answer; every
  // record was acted on above regardless.
  return records.length ? records[records.length - 1] : null;
}
