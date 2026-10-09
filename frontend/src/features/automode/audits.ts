/**
 * The read-only Audit view: `GET /frames/{id}/auto-audits`, newest first.
 *
 * docs/auto-mode.md, "Workbench status surface" §6. A kind filter
 * (`subject_kind`), server cursor paging (`before` = the previous page's
 * `next_before`, stopping when `has_more` is false), and only the allowlisted
 * fields `sanitize.ts` copies. It never shows a prompt, hidden rationale, a
 * permission-request body or a reusable authorization, and it never writes:
 * no review, repair, resume, approval or cancellation starts from here.
 *
 * A canonical audit/terminal event while the view is open re-reads the first
 * page; the event itself is never inserted as a row.
 */

import { _openGen, currentId } from "../../stores/session";
import { _modalMode } from "../../stores/ui";
import { closeModalEl, openModalEl } from "../chrome/modal";
import { ApiError, api } from "../sessions/api";
import { $, el } from "../sessions/dom";
import { autoModeT } from "./copy";
import { auditDetailRows, auditFacts, auditHeadline, auditKindLabel } from "./present";
import { sanitizeAuditPage } from "./sanitize";
import { AUDIT_SUBJECT_KINDS, type AuditRow, type AuditSubjectKind } from "./types";

/** Rows per page; the route accepts 1-500. */
export const AUDIT_PAGE_SIZE = 20;
const MODE = "auto-audits:";
const REFRESH_DEBOUNCE_MS = 60;

type Failure = "unavailable" | "not_found";

type AuditViewState = {
  frameId: string;
  openGen: number;
  filter: AuditSubjectKind | null;
  rows: AuditRow[];
  nextBefore: string | null;
  hasMore: boolean;
  phase: "loading" | "ready" | "failed";
  failure: Failure | null;
  /** A page request failed (a rejected cursor or limit); the rows above it stay. */
  pageFailed: boolean;
  /** Bumps on every first-page read, so a superseded read or page cannot land. */
  listVersion: number;
  pageInFlight: boolean;
  filterBar: HTMLElement;
  list: HTMLElement;
  foot: HTMLElement;
};

let view: AuditViewState | null = null;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;

function modal(): HTMLElement | null {
  return $("#modal");
}

/** Whether the Audit view for `frameId` (or any frame) is the open modal. */
export function autoModeAuditsOpen(frameId?: string): boolean {
  if (!view || _modalMode.value !== MODE + view.frameId) return false;
  const box = modal();
  if (!box || box.classList.contains("hidden")) return false;
  return frameId === undefined || frameId === view.frameId;
}

function current(v: AuditViewState, version: number): boolean {
  return (
    view === v &&
    v.listVersion === version &&
    _modalMode.value === MODE + v.frameId &&
    currentId.value === v.frameId &&
    _openGen.value === v.openGen
  );
}

function auditPath(v: AuditViewState, before: string | null): string {
  const params = new URLSearchParams();
  params.set("limit", String(AUDIT_PAGE_SIZE));
  if (v.filter) params.set("subject_kind", v.filter);
  if (before !== null) params.set("before", before);
  return `/frames/${encodeURIComponent(v.frameId)}/auto-audits?${params.toString()}`;
}

function errorCode(error: unknown): { status: number; code: string } {
  return error instanceof ApiError ? { status: error.status, code: error.code } : { status: 0, code: "" };
}

function failureOf(error: unknown): Failure {
  const { status, code } = errorCode(error);
  return status === 404 && code === "frame_not_found" ? "not_found" : "unavailable";
}

async function loadFirstPage(v: AuditViewState, { clear }: { clear: boolean }): Promise<void> {
  v.listVersion += 1;
  const version = v.listVersion;
  v.pageInFlight = false;
  if (clear) {
    v.rows = [];
    v.nextBefore = null;
    v.hasMore = false;
    v.phase = "loading";
    v.failure = null;
  }
  v.pageFailed = false;
  paint(v);
  let raw: unknown = null;
  let error: unknown = null;
  try {
    raw = await api(auditPath(v, null));
  } catch (caught) {
    error = caught;
  }
  if (!current(v, version)) return;
  // A new first page supersedes a next page still in flight: its answer is
  // dropped for naming another cursor, so it must not keep the button busy.
  v.pageInFlight = false;
  if (error) {
    const { status, code } = errorCode(error);
    if (status === 400 && code === "invalid_subject_kind" && v.filter !== null) {
      // Reset to both kinds once; the rejected value is never sent again.
      v.filter = null;
      await loadFirstPage(v, { clear: true });
      return;
    }
    v.phase = "failed";
    v.failure = failureOf(error);
    v.rows = [];
    v.nextBefore = null;
    v.hasMore = false;
    paint(v);
    return;
  }
  const page = sanitizeAuditPage(raw);
  if (!page || page.root_frame_id !== v.frameId) {
    v.phase = "failed";
    v.failure = "unavailable";
    v.rows = [];
    v.nextBefore = null;
    v.hasMore = false;
    paint(v);
    return;
  }
  const seen = new Set<string>();
  v.rows = page.audits.filter((row) => !seen.has(row.audit_id) && !!seen.add(row.audit_id));
  v.nextBefore = page.has_more ? page.next_before : null;
  v.hasMore = page.has_more;
  v.phase = "ready";
  v.failure = null;
  paint(v);
}

async function loadNextPage(v: AuditViewState): Promise<void> {
  if (!v.hasMore || v.nextBefore === null || v.pageInFlight || v.phase !== "ready") return;
  const version = v.listVersion;
  const before = v.nextBefore;
  v.pageInFlight = true;
  v.pageFailed = false;
  paint(v);
  let raw: unknown = null;
  let error: unknown = null;
  try {
    raw = await api(auditPath(v, before));
  } catch (caught) {
    error = caught;
  }
  if (!current(v, version) || v.nextBefore !== before) return;
  v.pageInFlight = false;
  const page = error ? null : sanitizeAuditPage(raw);
  if (!page || page.root_frame_id !== v.frameId) {
    if (error && failureOf(error) === "not_found") {
      v.phase = "failed";
      v.failure = "not_found";
      v.rows = [];
    }
    // A rejected cursor or limit is unavailable for that request only; the
    // client never walks a cursor of its own making.
    v.pageFailed = true;
    v.hasMore = false;
    v.nextBefore = null;
    paint(v);
    return;
  }
  const seen = new Set(v.rows.map((row) => row.audit_id));
  for (const row of page.audits) {
    if (seen.has(row.audit_id)) continue;
    seen.add(row.audit_id);
    v.rows.push(row);
  }
  v.nextBefore = page.has_more ? page.next_before : null;
  v.hasMore = page.has_more;
  paint(v);
}

function factList(rows: Array<[string, string]>, className: string): HTMLElement {
  const list = el("dl", className);
  for (const [key, value] of rows) {
    list.appendChild(el("dt", null, key));
    list.appendChild(el("dd", null, value));
  }
  return list;
}

function findingNode(finding: AuditRow["findings"][number]): HTMLElement {
  const node = el("div", "am-finding");
  node.dataset.findingId = finding.finding_id;
  const head = el("div", "am-finding-head");
  for (const value of [finding.severity, finding.category, finding.status]) {
    if (value) head.appendChild(el("span", "am-chip", value));
  }
  node.appendChild(head);
  if (finding.claim) node.appendChild(el("div", "am-finding-claim", finding.claim));
  const refs: Array<[string, string]> = [];
  const push = (key: "evidence_refs" | "version_ids" | "artifact_ids" | "cell_ids") => {
    if (finding[key].length) refs.push([autoModeT("autoMode.audit.f." + key), finding[key].join(", ")]);
  };
  push("evidence_refs");
  push("version_ids");
  push("artifact_ids");
  push("cell_ids");
  if (refs.length) node.appendChild(factList(refs, "am-kv"));
  const identity: Array<[string, string]> = [[autoModeT("autoMode.audit.f.finding_id"), finding.finding_id]];
  if (finding.fingerprint) identity.push([autoModeT("autoMode.audit.f.fingerprint"), finding.fingerprint]);
  const more = el("details", "am-detail");
  more.appendChild(el("summary", null, autoModeT("autoMode.details")));
  more.appendChild(factList(identity, "am-kv"));
  node.appendChild(more);
  return node;
}

function auditNode(row: AuditRow): HTMLElement {
  const node = el("article", "am-audit");
  node.dataset.auditId = row.audit_id;
  node.dataset.kind = row.subject_kind;
  const head = el("div", "am-audit-head");
  head.appendChild(el("span", "am-chip am-audit-kind", auditKindLabel(row.subject_kind)));
  head.appendChild(el("span", "am-audit-summary", auditHeadline(row)));
  node.appendChild(head);
  node.appendChild(factList(auditFacts(row), "am-audit-facts"));
  if (row.findings.length) {
    const findings = el("div", "am-findings");
    findings.appendChild(el("div", "am-findings-title", autoModeT("autoMode.audit.findings")));
    for (const finding of row.findings) findings.appendChild(findingNode(finding));
    node.appendChild(findings);
  }
  const more = el("details", "am-detail");
  more.appendChild(el("summary", null, autoModeT("autoMode.details")));
  more.appendChild(factList(auditDetailRows(row), "am-kv"));
  if (row.rationale_summary) {
    const summary = el("div", "am-rationale");
    summary.appendChild(el("span", "am-h", autoModeT("autoMode.audit.summary")));
    summary.appendChild(el("span", "am-v", row.rationale_summary));
    more.appendChild(summary);
  }
  node.appendChild(more);
  return node;
}

function paintFilter(v: AuditViewState): void {
  v.filterBar.innerHTML = "";
  const choices: Array<AuditSubjectKind | null> = [null, ...AUDIT_SUBJECT_KINDS];
  for (const choice of choices) {
    const chosen = v.filter === choice;
    const option = el("button", "am-filter" + (chosen ? " active" : ""), auditKindLabel(choice));
    option.type = "button";
    option.dataset.kind = choice || "all";
    option.setAttribute("aria-pressed", chosen ? "true" : "false");
    option.onclick = () => {
      if (view !== v || v.filter === choice) return;
      v.filter = choice;
      void loadFirstPage(v, { clear: true });
    };
    v.filterBar.appendChild(option);
  }
}

function retryButton(v: AuditViewState): HTMLElement {
  const retry = el("button", "outline-btn small am-retry", autoModeT("autoMode.retry"));
  retry.type = "button";
  retry.onclick = () => {
    if (view === v) void loadFirstPage(v, { clear: true });
  };
  return retry;
}

function paint(v: AuditViewState): void {
  if (view !== v) return;
  paintFilter(v);
  v.list.innerHTML = "";
  v.foot.innerHTML = "";
  v.list.dataset.phase = v.phase;
  v.list.setAttribute("aria-busy", v.phase === "loading" || v.pageInFlight ? "true" : "false");
  if (v.phase === "loading") {
    v.list.appendChild(el("div", "dock-empty am-audit-state", autoModeT("autoMode.loading")));
    return;
  }
  if (v.phase === "failed") {
    v.list.appendChild(
      el(
        "div",
        "dock-empty am-audit-state",
        autoModeT(v.failure === "not_found" ? "autoMode.notFound" : "autoMode.audit.unavailable"),
      ),
    );
    v.foot.appendChild(retryButton(v));
    return;
  }
  if (!v.rows.length) v.list.appendChild(el("div", "dock-empty am-audit-state", autoModeT("autoMode.audit.empty")));
  for (const row of v.rows) v.list.appendChild(auditNode(row));
  if (v.pageFailed) {
    v.foot.appendChild(el("div", "am-warn am-page-error", autoModeT("autoMode.audit.unavailable")));
    v.foot.appendChild(retryButton(v));
  } else if (v.hasMore) {
    const more = el("button", "outline-btn small am-more", autoModeT(v.pageInFlight ? "autoMode.loading" : "autoMode.audit.more"));
    more.type = "button";
    more.disabled = v.pageInFlight;
    more.onclick = () => void loadNextPage(v);
    v.foot.appendChild(more);
  }
}

/** Open the Audit view for the open conversation. */
export async function openAutoModeAudits(): Promise<void> {
  const frameId = currentId.value;
  if (!frameId) return;
  _modalMode.value = MODE + frameId;
  const title = $("#modal-title");
  if (title) title.textContent = autoModeT("autoMode.audit.title");
  const download = $("#modal-download");
  if (download) download.style.display = "none";
  const body = $("#modal-body");
  if (!body) return;
  body.innerHTML = "";
  const wrap = el("div", "am-audits");
  const filterBar = el("div", "am-filters");
  filterBar.setAttribute("role", "group");
  filterBar.setAttribute("aria-label", autoModeT("autoMode.audit.filter"));
  const list = el("div", "am-audit-list");
  list.setAttribute("aria-live", "polite");
  const foot = el("div", "am-audit-foot");
  wrap.appendChild(filterBar);
  wrap.appendChild(list);
  wrap.appendChild(foot);
  body.appendChild(wrap);
  const v: AuditViewState = {
    frameId,
    openGen: _openGen.value,
    filter: null,
    rows: [],
    nextBefore: null,
    hasMore: false,
    phase: "loading",
    failure: null,
    pageFailed: false,
    listVersion: 0,
    pageInFlight: false,
    filterBar,
    list,
    foot,
  };
  view = v;
  openModalEl(modal());
  await loadFirstPage(v, { clear: true });
}

/**
 * A canonical audit or terminal event arrived. Re-read the first page if the
 * view is open on the event's conversation and its filter includes `kind`
 * (omitted for a terminal or a reconnect: every kind may have moved).
 */
export function scheduleAutoModeAuditsRefresh(kind?: unknown): void {
  const v = view;
  if (!v || !autoModeAuditsOpen(v.frameId)) return;
  if (kind !== undefined && v.filter !== null && kind !== v.filter) return;
  if (refreshTimer !== null) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    if (view === v && autoModeAuditsOpen(v.frameId)) void loadFirstPage(v, { clear: false });
  }, REFRESH_DEBOUNCE_MS);
}

/**
 * The open conversation changed or was reopened. A branch activation or
 * revert keeps the frame but changes what its audits are: re-read under the
 * new opening. Another conversation is not what this view describes: close it.
 */
export function autoModeAuditsContextChanged(): void {
  const v = view;
  if (!v || !autoModeAuditsOpen(v.frameId)) return;
  if (currentId.value !== v.frameId) {
    view = null;
    closeModalEl(modal());
    return;
  }
  v.openGen = _openGen.value;
  void loadFirstPage(v, { clear: true });
}

/** Test seam. */
export function resetAutoModeAudits(): void {
  if (refreshTimer !== null) clearTimeout(refreshTimer);
  refreshTimer = null;
  view = null;
}
