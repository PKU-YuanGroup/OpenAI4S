/**
 * Admission tracker. Port of app.js:9016-9143.
 *
 * localStorage keys `openai4s.admission.*` are frozen. Independent keys, never
 * a container: a write touches one reservation so concurrent tabs cannot
 * clobber each other. `outstandingAdmissions` stays on window.
 */

import { LANG } from "../../i18n/runtime";
import { currentId } from "../../stores/session";
import { $, el } from "../messages/dom";
import { api } from "../sessions/api";
import { hint } from "../sessions/chrome";
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
  // A reopened session asks about its plain-message receipts too (NV-01).
  // Separate keys and a separate route; never awaited by the pin path, so a
  // slow receipt read cannot hold the comments back.
  if (outstandingMessageReceipts(fid).length) {
    void reconcileMessageReceipts(fid).catch(() => {});
  }
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

// ---- NV-01: plain-message submission receipts ------------------------------
//
// A send without pinned comments carries a `client_request_id` the client
// mints and stores BEFORE dispatch, so a page whose answer was lost (a
// dropped connection, a 5xx, a reload) can ask the server what became of it
// instead of guessing: `GET /frames/{fid}/message-admissions/{id}` is
// read-only and never submits. A new key prefix, `openai4s.message-receipt.`,
// because the `openai4s.admission.*` keys are frozen and mean pins.

export const MESSAGE_RECEIPT_PREFIX = (fid: string): string =>
  "openai4s.message-receipt." + fid + ".";

/** Automatic re-asks of an unconfirmed receipt before only "Check again" is left. */
export const MESSAGE_RECEIPT_RETRIES = 3;
export const MESSAGE_RECEIPT_RETRY_MS = 3000;

/** Feature-local copy: the generated F-07 dictionaries do not carry these. */
const RECEIPT_COPY: Record<"en" | "zh", Record<"pending" | "check" | "refused" | "refusedEarlier", string>> = {
  en: {
    pending: "Submission pending confirmation",
    check: "Check again",
    refused: "Not accepted: {0}",
    refusedEarlier: "A message sent before this page reloaded was not accepted: {0}",
  },
  zh: {
    pending: "提交待确认",
    check: "重新查询",
    refused: "未被接受：{0}",
    refusedEarlier: "页面刷新前发送的一条消息未被接受：{0}",
  },
};

export function receiptCopy(key: keyof (typeof RECEIPT_COPY)["en"], arg = ""): string {
  return RECEIPT_COPY[LANG === "zh" ? "zh" : "en"][key].split("{0}").join(arg);
}

/**
 * 128 bits from the platform CSPRNG as 32 lowercase hex characters -- the one
 * shape the server accepts. Throws rather than fall back: a weak id is a key
 * another send can collide with, and a collision answers one message with
 * another's receipt.
 */
export function mintClientRequestId(): string {
  const cryptoObj = (globalThis as { crypto?: Crypto }).crypto;
  if (!cryptoObj || typeof cryptoObj.getRandomValues !== "function") {
    throw new Error("a submission receipt requires crypto.getRandomValues");
  }
  const bytes = new Uint8Array(16);
  cryptoObj.getRandomValues(bytes);
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** One independent key per send, stamped with its mint time: `{frame, id, time}`. */
export function rememberMessageReceipt(fid: string, id: string): void {
  try {
    storage()?.setItem(MESSAGE_RECEIPT_PREFIX(fid) + id, String(Date.now()));
  } catch {
    /* quota / private mode */
  }
}

export function forgetMessageReceipt(fid: string, id: string): void {
  try {
    storage()?.removeItem(MESSAGE_RECEIPT_PREFIX(fid) + id);
  } catch {
    /* quota / private mode */
  }
}

export function outstandingMessageReceipts(fid: string): string[] {
  const prefix = MESSAGE_RECEIPT_PREFIX(fid);
  const store = storage();
  if (!store) return [];
  const found: Array<[string, number]> = [];
  try {
    for (let i = 0; i < store.length; i++) {
      const key = store.key(i);
      if (key && key.startsWith(prefix)) {
        found.push([key.slice(prefix.length), Number(store.getItem(key)) || 0]);
      }
    }
  } catch {
    return [];
  }
  found.sort((a, b) => a[1] - b[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  return found.map((pair) => pair[0]).filter(Boolean);
}

/** Inside the same lease as a pin: another tab's POST may not have arrived yet. */
export function messageReceiptWithinGrace(fid: string, id: string): boolean {
  let raw: string | null = null;
  try {
    raw = storage()?.getItem(MESSAGE_RECEIPT_PREFIX(fid) + id) ?? null;
  } catch {
    return false;
  }
  const minted = Number(raw);
  if (!raw || !Number.isFinite(minted) || minted <= 0 || minted > Date.now()) return false;
  return Date.now() - minted < ADMISSION_GRACE_MS;
}

export type ReceiptRecord = {
  admission_state?: unknown;
  job_id?: unknown;
  request_id?: unknown;
  execution_id?: unknown;
  branch_id?: unknown;
  model_binding?: unknown;
  refusal?: { code?: unknown; status?: unknown } | null;
};

export type ReceiptOutcome =
  /** Queue-accepted. `active` only when the session's own execution snapshot names it. */
  | { kind: "accepted"; record: ReceiptRecord; active: boolean; queuePosition: number | null }
  | { kind: "refused"; record: ReceiptRecord }
  | {
      kind: "unconfirmed";
      reason: "pending" | "indeterminate" | "not_found" | "unavailable" | "offline";
      record?: ReceiptRecord;
    };

/** Where an execution stands in a session snapshot: 0 running, n queued, null absent. */
export function executionPosition(snapshot: unknown, executionId: unknown): number | null {
  if (!executionId || !snapshot || typeof snapshot !== "object") return null;
  const snap = snapshot as { owner?: { execution_id?: unknown } | null; queue?: unknown };
  if (snap.owner && snap.owner.execution_id === executionId) return 0;
  const queue = Array.isArray(snap.queue) ? (snap.queue as Array<Record<string, unknown>>) : [];
  const item = queue.find((row) => row && row.execution_id === executionId);
  if (!item) return null;
  const position = Number(item.queue_position);
  return Number.isFinite(position) && position > 0 ? position : 1;
}

/**
 * Ask once. Read-only: a receipt is never a reason to send again. An accepted
 * receipt is checked against the execution snapshot, because "accepted" says
 * the queue took it, not that it is still running.
 */
export async function queryMessageReceipt(fid: string, id: string): Promise<ReceiptOutcome> {
  let record: ReceiptRecord | null = null;
  try {
    record = (await api(
      `/frames/${encodeURIComponent(fid)}/message-admissions/${encodeURIComponent(id)}`,
    )) as ReceiptRecord | null;
  } catch (e) {
    const status = (e as { status?: unknown } | null)?.status;
    if (status === 404) return { kind: "unconfirmed", reason: "not_found" };
    if (typeof status === "number") return { kind: "unconfirmed", reason: "unavailable" };
    return { kind: "unconfirmed", reason: "offline" };
  }
  const state = record && record.admission_state;
  if (state === "accepted" && record) {
    let position: number | null = null;
    try {
      position = executionPosition(
        await api(`/frames/${encodeURIComponent(fid)}/execution-queue`),
        record.execution_id,
      );
    } catch {
      // Unreadable is not "still running": history is the safe place to look.
      position = null;
    }
    return { kind: "accepted", record, active: position !== null, queuePosition: position };
  }
  if (state === "refused" && record) return { kind: "refused", record };
  return {
    kind: "unconfirmed",
    reason: state === "pending" ? "pending" : "indeterminate",
    record: record || undefined,
  };
}

export function refusalCode(record: ReceiptRecord | undefined): string {
  const refusal = record && record.refusal;
  return String((refusal && refusal.code) || "refused");
}

type ReceiptWatcher = {
  fid: string;
  id: string;
  attempts: number;
  apply: (outcome: ReceiptOutcome, watcher: ReceiptWatcher) => void;
};

/** One watcher per receipt id, until it is decided either way. */
const receiptWatchers = new Map<string, ReceiptWatcher>();
/** One pending sweep per session, the same de-dupe as `scheduleAdmissionRetry`. */
const receiptRetries = new Map<string, ReturnType<typeof setTimeout>>();

export function resetMessageReceipts(): void {
  for (const timer of receiptRetries.values()) clearTimeout(timer);
  receiptRetries.clear();
  receiptWatchers.clear();
}

export function watchMessageReceipt(
  fid: string,
  id: string,
  apply: (outcome: ReceiptOutcome, watcher: ReceiptWatcher) => void,
): void {
  receiptWatchers.set(id, { fid, id, attempts: 0, apply });
}

export function watchingMessageReceipt(id: string): boolean {
  return receiptWatchers.has(id);
}

/** Ask now and hand the answer to the receipt's watcher. */
export async function checkMessageReceipt(id: string): Promise<ReceiptOutcome | null> {
  const watcher = receiptWatchers.get(id);
  if (!watcher) return null;
  const outcome = await queryMessageReceipt(watcher.fid, id);
  // Superseded while the read was out (a newer watcher for the same id).
  if (receiptWatchers.get(id) !== watcher) return outcome;
  if (outcome.kind !== "unconfirmed") receiptWatchers.delete(id);
  watcher.apply(outcome, watcher);
  return outcome;
}

/** Re-ask the session's unconfirmed receipts once, a bounded number of times. */
export function scheduleMessageReceiptRetry(fid: string): void {
  if (receiptRetries.has(fid)) return;
  receiptRetries.set(
    fid,
    setTimeout(() => {
      receiptRetries.delete(fid);
      // Only while the session is the one on screen, as for pins.
      if (currentId.value !== fid) return;
      for (const watcher of [...receiptWatchers.values()]) {
        if (watcher.fid !== fid || watcher.attempts >= MESSAGE_RECEIPT_RETRIES) continue;
        watcher.attempts += 1;
        void checkMessageReceipt(watcher.id).catch(() => {});
      }
    }, MESSAGE_RECEIPT_RETRY_MS),
  );
}

/**
 * "Submission pending confirmation" with a "Check again" button, on the
 * message's own bubble while it is on screen, else as a row of its own in the
 * transcript (after a reload or a reopen the optimistic bubble is gone).
 */
export function showReceiptNotice(id: string, bubble?: HTMLElement | null): void {
  let host: HTMLElement | null = bubble && bubble.isConnected !== false ? bubble : null;
  if (!host) {
    const messages = $("#messages");
    if (!messages) return;
    host = messages.querySelector(`.msg.user[data-client-request-id="${id}"]`) as HTMLElement | null;
    if (!host) {
      host = el("div", "msg user receipt-unconfirmed");
      host.dataset.clientRequestId = id;
      messages.appendChild(host);
    }
  }
  host.dataset.clientRequestId = id;
  host.dataset.sendState = "unconfirmed";
  host.style.flexWrap = "wrap";
  if (host.querySelector && host.querySelector(".msg-receipt")) return;
  const row = el("div", "msg-receipt");
  row.style.flex = "0 0 100%";
  row.style.display = "flex";
  row.style.justifyContent = "flex-end";
  row.style.alignItems = "center";
  row.style.gap = "8px";
  row.style.marginTop = "4px";
  row.style.fontSize = "12px";
  row.style.color = "var(--text-400)";
  row.setAttribute("role", "status");
  row.appendChild(el("span", "msg-receipt-t", receiptCopy("pending")));
  const again = el("button", "outline-btn small msg-receipt-check", receiptCopy("check")) as HTMLButtonElement;
  again.type = "button";
  again.onclick = () => {
    const watcher = receiptWatchers.get(id);
    if (watcher) watcher.attempts = 0;
    void checkMessageReceipt(id).catch(() => {});
  };
  row.appendChild(again);
  host.appendChild(row);
}

/** Drop the notice: the receipt was decided. A standalone row goes with it. */
export function clearReceiptNotice(id: string, bubble?: HTMLElement | null): void {
  const hosts: HTMLElement[] = [];
  if (bubble) hosts.push(bubble);
  const messages = $("#messages");
  const row = messages && messages.querySelector
    ? (messages.querySelector(`.msg.user[data-client-request-id="${id}"]`) as HTMLElement | null)
    : null;
  if (row && !hosts.includes(row)) hosts.push(row);
  for (const host of hosts) {
    if (host.classList && host.classList.contains("receipt-unconfirmed")) {
      host.remove();
      continue;
    }
    const notice = host.querySelector ? host.querySelector(".msg-receipt") : null;
    if (notice) notice.remove();
    if (host.dataset && host.dataset.sendState === "unconfirmed") delete host.dataset.sendState;
  }
}

/**
 * What a reopened page does with the receipts it still holds. Accepted: the
 * opened conversation already follows a live execution, and a finished one is
 * read from history. Refused: said once. Unconfirmed: inside the grace another
 * tab's POST may still be on its way, so it is re-asked quietly; past it the
 * notice goes up with "Check again".
 */
export async function reconcileMessageReceipts(fid: string): Promise<void> {
  for (const id of outstandingMessageReceipts(fid)) {
    // A send in this tab is already watching its own receipt.
    if (receiptWatchers.has(id)) continue;
    watchMessageReceipt(fid, id, (outcome, watcher) => {
      if (outcome.kind === "accepted") {
        forgetMessageReceipt(fid, id);
        clearReceiptNotice(id);
        if (!outcome.active && currentId.value === fid) callLane("recoverConversation", fid);
        return;
      }
      if (outcome.kind === "refused") {
        forgetMessageReceipt(fid, id);
        clearReceiptNotice(id);
        if (currentId.value === fid) hint(receiptCopy("refusedEarlier", refusalCode(outcome.record)), true);
        return;
      }
      const quiet = messageReceiptWithinGrace(fid, id);
      // Quiet re-asks are bounded by the grace itself, not by the count.
      if (quiet) watcher.attempts = 0;
      else if (currentId.value === fid) showReceiptNotice(id);
      // Past the grace a 404 is the server saying it never claimed this key;
      // the notice is shown once and the key is not asked about again.
      if (outcome.reason === "not_found" && !quiet) forgetMessageReceipt(fid, id);
      if (watcher.attempts < MESSAGE_RECEIPT_RETRIES) scheduleMessageReceiptRetry(fid);
    });
    await checkMessageReceipt(id);
    if (currentId.value !== fid) return;
  }
}
