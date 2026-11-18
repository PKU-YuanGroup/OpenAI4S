/**
 * Composer autocomplete. Port of app.js:12946-13033, 13125 + the keydown
 * branch at 13403-13411.
 *
 * `@` files (one artifact-index page + this session, version-pinned),
 * `#` sessions, `/` skills. Popup is `#composer-ac`. `ac` is the live
 * controller hung on window so F-11's send() keydown can see `ac.open`.
 * `acPending()` is true while an `@` search is debouncing or in flight;
 * Enter and Tab wait for that page instead of sending a bare `@name`.
 *
 * `@` does not keep a project-wide cache and does not call
 * `GET /projects/{pid}/artifacts`. A page is one filename query, limit 20.
 * A response whose project, session, or update generation no longer matches
 * is discarded. Hidden rows (`priority < 0`) stay out. A user dismiss
 * (Escape, blur, pick) bumps the generation so a late page cannot reopen
 * the popup. An empty paint inside `showFilePopup` does not.
 */

import { t } from "../../i18n/runtime";
import { artifacts } from "../../stores/artifacts";
import { currentId, sessions } from "../../stores/session";
import { fetchArtifactIndexPage } from "../artifacts/api";
import { filesT } from "../artifacts/copy";
import { effProject } from "../customize/host";
import { $, el, grow } from "../sessions/dom";
import { renderComposerRefChips } from "../sessions/transcript";
import { loadSkillsCatalog } from "./catalog";
import { acDetectFrom, type ComposerDetect } from "./detect";
import { rememberMention } from "./mentions";
import {
  artifactToAcItem,
  mergeArtifactCandidates,
  rankComposerItems,
  sessionToAcItem,
  skillToAcItem,
  type AcItem,
  type ArtifactLike,
} from "./rank";

export type AcState = {
  open: boolean;
  items: AcItem[];
  idx: number;
  trigger: string;
  start: number;
};

export const ac: AcState = {
  open: false,
  items: [],
  idx: 0,
  trigger: "",
  start: 0,
};

/** Input settles for this long before an `@` index request. `#` and `/` do not wait. */
export const AC_DEBOUNCE_MS = 150;
/** Rows asked of artifact-index. The popup still caps at `AC_LIMIT` (8). */
export const AC_INDEX_LIMIT = 20;
/** Abort an index request that has not settled. A user abort is not a failure hint. */
export const AC_INDEX_TIMEOUT_MS = 8000;

type AcNotice = "loading" | "error" | "recent";

const NOTICE_KEY: Record<AcNotice, string> = {
  loading: "ac.files.searching",
  error: "ac.files.failed",
  recent: "ac.files.recent",
};

type AcToken = {
  pid: string;
  sessionKey: string;
  query: string;
  seq: number;
};

type AcDecision =
  | { kind: "apply"; now: ComposerDetect }
  | { kind: "drop" }
  | { kind: "local" }
  | { kind: "close" };

/**
 * Bumped by every `acUpdate` and by a user dismiss. A load that settles
 * after a newer generation started is dropped.
 */
let acSeq = 0;
let acNotice: AcNotice | null = null;
let acTimer: ReturnType<typeof setTimeout> | null = null;
let acAbort: AbortController | null = null;
let debounceResolve: (() => void) | null = null;
/** Last rows actually applied for this anchor. Keystrokes re-filter these, not a fresh session list. */
let appliedItems: AcItem[] = [];
let appliedPid = "";
let appliedSession = "";
/** Enter/Tab landed while a search was in flight. Completed when that page settles, if the token is still there. */
let acAcceptArmed = false;
/** Token start Enter/Tab armed on; the accept only lands there. */
let acArmedStart = -1;

function projectId(): string {
  return effProject() || "";
}

function sessionKey(): string {
  return currentId.value || "";
}

/** True while an `@` search is waiting out the debounce or the index request. */
export function acPending(): boolean {
  return acTimer !== null || acAbort !== null;
}

function isShownArtifact(row: ArtifactLike | null | undefined): row is ArtifactLike {
  if (!row || !row.filename) return false;
  const priority = (row as { priority?: number | null }).priority;
  return !(typeof priority === "number" && priority < 0);
}

function sessionCandidates(): ArtifactLike[] {
  return ((artifacts.value || []) as ArtifactLike[]).filter(isShownArtifact);
}

/** Artifact identity per row: a newer version of the same file is still the highlighted row. */
const itemKey = new WeakMap<AcItem, string>();

function toItems(rows: ArtifactLike[]): AcItem[] {
  return rows.map((a) => {
    const item = artifactToAcItem(a, currentId.value, t("ac.fromOtherSession"));
    itemKey.set(item, String(a.artifact_id || a.id || a.filename));
    return item;
  });
}

function clearDebounce(): void {
  if (acTimer !== null) {
    clearTimeout(acTimer);
    acTimer = null;
  }
  if (debounceResolve) {
    const resolve = debounceResolve;
    debounceResolve = null;
    resolve();
  }
}

function abortInFlight(): void {
  const ctrl = acAbort;
  acAbort = null;
  if (ctrl) ctrl.abort();
}

function cancelFileSearch(): void {
  clearDebounce();
  abortInFlight();
}

function forgetApplied(): void {
  appliedItems = [];
  appliedPid = "";
  appliedSession = "";
}

/**
 * A newer keystroke owns the popup: leave it alone.
 * The project or session moved under this same update: do not render the
 * page that was fetched for the old one.
 * The caret left the token this update searched: close.
 */
function decide(token: AcToken): AcDecision {
  if (token.seq !== acSeq) return { kind: "drop" };
  if (token.pid !== projectId() || token.sessionKey !== sessionKey()) return { kind: "local" };
  const now = acDetect();
  if (!now || now.trigger !== "@" || now.query !== token.query) return { kind: "close" };
  return { kind: "apply", now };
}

function sameAnchor(d: { trigger: string; start: number }): boolean {
  return ac.trigger === d.trigger && ac.start === d.start;
}

function canRefine(d: ComposerDetect): boolean {
  return (
    d.trigger === "@" &&
    sameAnchor(d) &&
    appliedPid === projectId() &&
    appliedSession === sessionKey() &&
    appliedItems.length > 0
  );
}

function nextIndex(items: AcItem[], keep: boolean): number {
  if (!keep || items.length === 0) return 0;
  const prev = ac.items[ac.idx];
  if (!prev) return 0;
  const key = itemKey.get(prev) ?? prev.insert;
  return Math.max(0, items.findIndex((item) => (itemKey.get(item) ?? item.insert) === key));
}

function sameItemList(a: AcItem[], b: AcItem[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i]!;
    const y = b[i]!;
    if (x.insert !== y.insert || x.label !== y.label || x.sub !== y.sub) return false;
    if ((x.mention?.version_id || "") !== (y.mention?.version_id || "")) return false;
  }
  return true;
}

function isHintNode(node: Element): boolean {
  return String(node.className).split(/\s+/).includes("ac-hint");
}

function settle(token: AcToken, rows: ArtifactLike[], notice: AcNotice | null): void {
  const decision = decide(token);
  if (decision.kind === "drop") return;
  if (decision.kind === "apply") {
    showFilePopup(rows, decision.now, notice);
    finishArmedAccept();
    return;
  }
  if (decision.kind === "local") {
    const now = acDetect();
    if (!now || now.trigger !== "@") {
      acAcceptArmed = false;
      hidePopup();
    } else {
      showFilePopup(sessionCandidates(), now, null);
      finishArmedAccept();
    }
    return;
  }
  acAcceptArmed = false;
  hidePopup();
}

export function acDetect(): ComposerDetect | null {
  const c = $("#composer") as HTMLTextAreaElement | null;
  if (!c) return null;
  const pos = c.selectionStart;
  const before = (c.value || "").slice(0, pos);
  return acDetectFrom(before, pos);
}

/**
 * One index page for `query` (empty → most recent page, no `q`), merged with
 * this session's visible files. No shared cache: callers that outlive their
 * `{pid, sessionKey, query, seq}` token drop the array.
 */
export async function acProjectFiles(query = "", signal?: AbortSignal): Promise<ArtifactLike[]> {
  const pid = projectId();
  const sessionList = sessionCandidates();
  if (!pid) return sessionList;
  const page = await fetchArtifactIndexPage(pid, {
    q: query,
    limit: AC_INDEX_LIMIT,
    signal,
  });
  return mergeArtifactCandidates(page.artifacts.filter(isShownArtifact), sessionList);
}

export function ensureComposerAc(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  let box = document.getElementById("composer-ac");
  if (box) return box;
  box = el("div", "composer-ac hidden");
  box.id = "composer-ac";
  const refs = document.getElementById("composer-refs");
  const hint = document.getElementById("composer-hint");
  const composer = document.getElementById("composer");
  const parent =
    (refs && refs.parentNode) ||
    (hint && hint.parentNode) ||
    (composer && composer.parentNode) ||
    document.body;
  if (refs && refs.parentNode === parent) parent.insertBefore(box, refs.nextSibling);
  else if (hint && hint.parentNode === parent) parent.insertBefore(box, hint);
  else parent.appendChild(box);
  return box;
}

/** Hide the popup without invalidating an in-flight search. */
function hidePopup(): void {
  ac.open = false;
  ac.items = [];
  ac.idx = 0;
  acNotice = null;
  const b = $("#composer-ac");
  if (!b) return;
  b.classList.add("hidden");
  b.innerHTML = "";
}

export function acClose(): void {
  acSeq += 1;
  acAcceptArmed = false;
  cancelFileSearch();
  forgetApplied();
  ac.trigger = "";
  ac.start = 0;
  hidePopup();
}

function rememberApplied(items: AcItem[]): void {
  appliedItems = items;
  appliedPid = projectId();
  appliedSession = sessionKey();
}

/**
 * Paint `@` rows. `replaceApplied` stores them as the set later keystrokes
 * re-filter. An empty non-loading paint hides without bumping `acSeq`.
 * Recent-page and failure hints with no rows are not left on screen.
 */
function publishFileItems(
  items: AcItem[],
  d: ComposerDetect,
  notice: AcNotice | null,
  replaceApplied: boolean,
): void {
  if (!items.length && notice !== "loading") {
    if (replaceApplied) forgetApplied();
    hidePopup();
    return;
  }
  const keep = sameAnchor(d);
  const idx = nextIndex(items, keep);
  const rowsSame = keep && ac.idx === idx && sameItemList(ac.items, items);
  acNotice = notice;
  ac.items = items;
  ac.idx = items.length ? idx : 0;
  ac.trigger = d.trigger;
  ac.start = d.start;
  ac.open = items.length > 0;
  if (replaceApplied) rememberApplied(items);
  if (rowsSame) {
    syncHint();
    return;
  }
  acRender();
}

function showFilePopup(rows: ArtifactLike[], d: ComposerDetect, notice: AcNotice | null): void {
  publishFileItems(rankComposerItems(toItems(rows), d.query), d, notice, true);
}

function refineApplied(d: ComposerDetect, notice: AcNotice | null): void {
  publishFileItems(rankComposerItems(appliedItems, d.query), d, notice, false);
}

function syncHint(): void {
  const box = ensureComposerAc();
  if (!box) return;
  const kids = Array.from(box.children);
  const hint = kids.find((node) => isHintNode(node)) as HTMLElement | undefined;
  if (!acNotice) {
    hint?.remove();
  } else {
    const text = filesT(NOTICE_KEY[acNotice]);
    if (hint) hint.textContent = text;
    else box.appendChild(el("div", "ac-hint", text));
  }
  box.classList.remove("hidden");
}

export function acRender(): void {
  const box = ensureComposerAc();
  if (!box) return;
  box.innerHTML = "";
  const list = el("div", "ac-list");
  ac.items.forEach((it, i) => {
    const row = el("div", "ac-item" + (i === ac.idx ? " on" : ""));
    row.appendChild(el("span", "ac-lbl", ac.trigger + (it.label || "")));
    if (it.sub) row.appendChild(el("span", "ac-sub", it.sub));
    row.onmousedown = (e) => {
      e.preventDefault();
      acPick(i);
    };
    list.appendChild(row);
  });
  box.appendChild(list);
  if (acNotice) box.appendChild(el("div", "ac-hint", filesT(NOTICE_KEY[acNotice])));
  box.classList.remove("hidden");
  // The list is rebuilt each render; keep the highlighted row in view.
  const on = list.children[ac.idx] as HTMLElement | undefined;
  if (on && typeof on.scrollIntoView === "function") on.scrollIntoView({ block: "nearest" });
}

export function acPick(i: number): void {
  const it = ac.items[i];
  if (!it) return;
  const c = $("#composer") as HTMLTextAreaElement | null;
  if (!c) return;
  const val = c.value;
  const pos = c.selectionStart;
  const token = ac.trigger + it.insert + " ";
  c.value = val.slice(0, ac.start) + token + val.slice(pos);
  // The text says `@name`; the version the row named is kept beside it.
  if (it.mention) rememberMention(it.mention);
  const np = ac.start + token.length;
  c.setSelectionRange(np, np);
  acClose();
  grow();
  renderComposerRefChips();
  c.focus();
}

function finishArmedAccept(): void {
  if (!acAcceptArmed) return;
  acAcceptArmed = false;
  if (!ac.open || ac.items.length === 0) return;
  const now = acDetect();
  if (!now || now.trigger !== "@" || now.start !== acArmedStart) return;
  acPick(ac.idx);
}

function armAccept(): void {
  const d = acDetect();
  acAcceptArmed = !!d && d.trigger === "@";
  acArmedStart = d ? d.start : -1;
}

/** The send button while an `@` search is pending: wait like Enter does. */
export function acHoldSend(): boolean {
  if (!acPending()) return false;
  armAccept();
  return true;
}

/** `AbortSignal.any` is Chrome 116 / Firefox 124 / Safari 17.4; older engines get a child controller. */
function requestSignal(user: AbortSignal, ms: number): { signal: AbortSignal; done: () => void } {
  const any = (AbortSignal as { any?: (signals: AbortSignal[]) => AbortSignal }).any;
  if (typeof any === "function" && typeof AbortSignal.timeout === "function") {
    return { signal: any.call(AbortSignal, [user, AbortSignal.timeout(ms)]), done: () => {} };
  }
  const child = new AbortController();
  const onUser = (): void => child.abort(user.reason);
  if (user.aborted) onUser();
  else user.addEventListener("abort", onUser, { once: true });
  const timer = setTimeout(() => child.abort(new DOMException("timed out", "TimeoutError")), ms);
  return {
    signal: child.signal,
    done: () => {
      clearTimeout(timer);
      user.removeEventListener("abort", onUser);
    },
  };
}

async function runFileSearch(seq: number): Promise<void> {
  if (seq !== acSeq) return;
  const d = acDetect();
  if (!d || d.trigger !== "@") {
    acClose();
    return;
  }
  const token: AcToken = {
    pid: projectId(),
    sessionKey: sessionKey(),
    query: d.query,
    seq,
  };
  if (!token.pid) {
    settle(token, sessionCandidates(), null);
    return;
  }
  if (canRefine(d)) refineApplied(d, "loading");
  else showFilePopup(sessionCandidates(), d, "loading");
  const ctrl = new AbortController();
  const req = requestSignal(ctrl.signal, AC_INDEX_TIMEOUT_MS);
  acAbort = ctrl;
  try {
    const merged = await acProjectFiles(token.query, req.signal);
    settle(token, merged, token.query ? null : "recent");
  } catch {
    if (ctrl.signal.aborted) return;
    settle(token, sessionCandidates(), "error");
  } finally {
    req.done();
    if (acAbort === ctrl) acAbort = null;
  }
}

function scheduleFileSearch(seq: number): Promise<void> {
  const d = acDetect();
  const refine = !!d && d.trigger === "@" && canRefine(d);
  clearDebounce();
  abortInFlight();
  if (!d || d.trigger !== "@" || seq !== acSeq) return Promise.resolve();
  if (refine) refineApplied(d, null);
  else showFilePopup(sessionCandidates(), d, null);
  if (!projectId()) return Promise.resolve();
  return new Promise((resolve) => {
    debounceResolve = resolve;
    acTimer = setTimeout(() => {
      acTimer = null;
      debounceResolve = null;
      void runFileSearch(seq).then(resolve, resolve);
    }, AC_DEBOUNCE_MS);
  });
}

async function finishOther(seq: number, d: ComposerDetect): Promise<void> {
  let items: AcItem[] = [];
  if (d.trigger === "#") {
    const rows = (sessions.value || []) as Array<{
      name?: string;
      task_summary?: string;
    }>;
    items = rows.map(sessionToAcItem);
  } else if (d.trigger === "/") {
    // A failed read offers nothing this time; the next `/` asks again.
    const sk = await loadSkillsCatalog().catch(() => []);
    items = sk.map(skillToAcItem);
  }
  // The skill list loads asynchronously. Keystrokes in the meantime started
  // newer updates, and the caret may have moved without one.
  if (seq !== acSeq) return;
  const now = acDetect();
  if (!now || now.trigger !== d.trigger) {
    acClose();
    return;
  }
  items = rankComposerItems(items, now.query);
  acNotice = null;
  if (!items.length) {
    acClose();
    return;
  }
  const keep = sameAnchor(now);
  ac.open = true;
  ac.idx = nextIndex(items, keep);
  ac.items = items;
  ac.trigger = now.trigger;
  ac.start = now.start;
  acRender();
}

export function acUpdate(): Promise<void> {
  acAcceptArmed = false;
  const seq = ++acSeq;
  const d = acDetect();
  if (!d) {
    acClose();
    return Promise.resolve();
  }
  if (d.trigger !== "@") {
    cancelFileSearch();
    acNotice = null;
    return finishOther(seq, d);
  }
  return scheduleFileSearch(seq);
}

function onComposerKeydown(e: KeyboardEvent): void {
  if (e.isComposing || e.keyCode === 229) return;
  const pending = acPending();
  if (e.key === "Escape" && (ac.open || acNotice || pending)) {
    e.preventDefault();
    e.stopImmediatePropagation();
    acClose();
    return;
  }
  // Shift+Enter is a newline, never a send: with no rows showing it stays one.
  if (e.key === "Enter" && e.shiftKey && !ac.open) return;
  if (e.key === "Enter" || e.key === "Tab") {
    if (ac.open || pending) {
      e.preventDefault();
      e.stopImmediatePropagation();
      if (pending) {
        armAccept();
        return;
      }
      if (ac.items.length > 0) acPick(ac.idx);
      return;
    }
    // Send clears the composer without an input event. A notice-only popup
    // would otherwise stay up over the empty box.
    if (e.key === "Enter" && !e.shiftKey && acNotice) acClose();
    return;
  }
  if (!ac.open || ac.items.length === 0) return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    e.stopImmediatePropagation();
    ac.idx = (ac.idx + 1) % ac.items.length;
    acRender();
    return;
  }
  if (e.key === "ArrowUp") {
    e.preventDefault();
    e.stopImmediatePropagation();
    ac.idx = (ac.idx - 1 + ac.items.length) % ac.items.length;
    acRender();
  }
}

const boundComposers = new WeakSet<HTMLTextAreaElement>();
let autocompleteWatching = false;

function attachComposer(c: HTMLTextAreaElement): void {
  if (boundComposers.has(c)) return;
  boundComposers.add(c);
  ensureComposerAc();
  c.addEventListener("input", () => {
    void acUpdate();
  });
  c.addEventListener("keydown", onComposerKeydown, true);
  c.addEventListener("blur", () => {
    cancelFileSearch();
    acAcceptArmed = false;
    const seq = ++acSeq;
    setTimeout(() => {
      if (seq === acSeq) hidePopup();
    }, 120);
  });
}

export function bindComposerAutocomplete(): void {
  if (typeof document === "undefined") return;
  const tryBind = (): boolean => {
    const c = document.getElementById("composer") as HTMLTextAreaElement | null;
    if (!c) return false;
    attachComposer(c);
    return true;
  };
  if (tryBind()) return;
  if (autocompleteWatching || typeof MutationObserver !== "function") return;
  autocompleteWatching = true;
  const ob = new MutationObserver(() => {
    if (!tryBind()) return;
    autocompleteWatching = false;
    ob.disconnect();
  });
  ob.observe(document.documentElement || document.body, {
    childList: true,
    subtree: true,
  });
}
