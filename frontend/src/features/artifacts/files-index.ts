import { artifacts as artifactsSignal, filesScope, projectArtifacts } from "../../stores/artifacts";
import { project } from "../../stores/session";
import { api, asArtifactList, isApiStatus } from "./api";
import { filesT } from "./copy";
import {
  filesContentType,
  filesCursorFilter,
  filesHasMore,
  filesIndexError,
  filesIndexItems,
  filesIndexLoading,
  filesIndexMode,
  filesIndexReq,
  filesNextCursor,
  filesOrigin,
  filesQuery,
} from "./state";
import type { ArtifactIndexPage, ArtifactRow, FilesOrigin } from "./types";
import { FILES_MAX_PAGE_SIZE, FILES_PAGE_SIZE } from "./types";

export type FilesFilter = {
  q: string;
  contentType: string;
  origin: FilesOrigin;
};

export function currentFilesFilter(): FilesFilter {
  return {
    q: filesQuery.value.trim(),
    contentType: filesContentType.value.trim(),
    origin: filesOrigin.value,
  };
}

/** Cursor identity: project + q + content_type + origin. Filter change ⇒ drop. */
export function filterFingerprint(filter: FilesFilter, pid = ""): string {
  return JSON.stringify({
    pid,
    q: filter.q,
    contentType: filter.contentType,
    origin: filter.origin,
  });
}

function originOf(a: ArtifactRow): "uploaded" | "generated" {
  return a.is_user_upload ? "uploaded" : "generated";
}

/**
 * Frame-scope filter matching B-06 semantics: filename substring,
 * content_type substring, origin from `is_user_upload`. Hidden
 * (`priority < 0`) rows stay out. Same-name rows are not merged.
 * Project scope never uses this — it walks artifact-index.
 */
export function filterArtifactsClient(rows: ArtifactRow[], filter: FilesFilter): ArtifactRow[] {
  const q = filter.q.toLowerCase();
  const ct = filter.contentType.toLowerCase();
  const out: ArtifactRow[] = [];
  for (const a of rows) {
    if ((a.priority || 0) < 0) continue;
    const name = String(a.filename || "");
    if (q && !name.toLowerCase().includes(q)) continue;
    if (ct && !String(a.content_type || "").toLowerCase().includes(ct)) continue;
    if (filter.origin && originOf(a) !== filter.origin) continue;
    out.push(a);
  }
  return out;
}

function sortPriorityThenId(rows: ArtifactRow[]): ArtifactRow[] {
  return rows.slice().sort((x, y) => {
    const dp = (y.priority || 0) - (x.priority || 0);
    if (dp) return dp;
    return String(y.id).localeCompare(String(x.id));
  });
}

/** app.js:8492-8495 */
export function visibleArtifacts(): ArtifactRow[] {
  const src = (artifactsSignal.value as ArtifactRow[]) || [];
  return src
    .filter((a) => (a.priority || 0) >= 0)
    .slice()
    .sort((x, y) => (y.priority || 0) - (x.priority || 0));
}

/**
 * app.js:8499-8502, plus M-03: project scope paints the paged index
 * (server order). Frame scope keeps the original priority sort.
 */
export function filesGridArtifacts(): ArtifactRow[] {
  if (filesScope.value === "project") {
    return (filesIndexItems.value || []).filter((a) => (a.priority || 0) >= 0);
  }
  const src = (artifactsSignal.value as ArtifactRow[]) || [];
  return sortPriorityThenId(src.filter((a) => (a.priority || 0) >= 0));
}

function clampLimit(limit: number): number {
  if (!Number.isFinite(limit) || limit < 1) return FILES_PAGE_SIZE;
  return Math.min(Math.max(1, Math.floor(limit)), FILES_MAX_PAGE_SIZE);
}

async function fetchArtifactIndex(
  pid: string,
  filter: FilesFilter,
  cursor: string | null,
  limit: number,
): Promise<ArtifactIndexPage> {
  const params = new URLSearchParams();
  if (filter.q) params.set("q", filter.q);
  if (filter.contentType) params.set("content_type", filter.contentType);
  if (filter.origin) params.set("origin", filter.origin);
  if (cursor) params.set("cursor", cursor);
  params.set("limit", String(clampLimit(limit)));
  const qs = params.toString();
  const body = await api(`/projects/${encodeURIComponent(pid)}/artifact-index?${qs}`);
  if (!body || typeof body !== "object") {
    return { artifacts: [], next_cursor: null, has_more: false };
  }
  const rec = body as Record<string, unknown>;
  return {
    artifacts: asArtifactList(rec.artifacts),
    next_cursor: rec.next_cursor == null ? null : String(rec.next_cursor),
    has_more: !!rec.has_more,
  };
}

function pageSlice(rows: ArtifactRow[], offset: number, limit: number): ArtifactIndexPage {
  const cap = clampLimit(limit);
  const slice = rows.slice(offset, offset + cap);
  const hasMore = offset + slice.length < rows.length;
  return {
    artifacts: slice,
    next_cursor: hasMore ? String(offset + slice.length) : null,
    has_more: hasMore,
  };
}

function dropFilesCursor(): void {
  filesNextCursor.value = null;
  filesCursorFilter.value = null;
  filesIndexItems.value = [];
  filesIndexReq.value = (filesIndexReq.value || 0) + 1;
}

export type BrowseFilesOpts = { reset?: boolean; loadMore?: boolean; limit?: number };

/**
 * M-03 Files listing. Project scope walks B-06 artifact-index (50/page, cap 100)
 * and never falls back to `GET /projects/{pid}/artifacts`. Filter changes drop
 * the previous cursor (client fingerprint + server 400 invalid_cursor). A
 * Project switch drops late responses via `filesIndexReq`. Frame scope filters
 * the session array locally — the index route is project-scoped.
 */
export async function browseFiles(opts: BrowseFilesOpts = {}): Promise<void> {
  const req = (filesIndexReq.value || 0) + 1;
  filesIndexReq.value = req;
  const filter = currentFilesFilter();
  const scope = filesScope.value;
  const limit = opts.limit ?? FILES_PAGE_SIZE;
  const pid = project.value || "";
  const fp = filterFingerprint(filter, scope === "project" ? pid : "");

  let loadMore = !!opts.loadMore && !opts.reset;
  if (!loadMore || filesCursorFilter.value !== fp) {
    loadMore = false;
    filesNextCursor.value = null;
  }

  if (scope !== "project") {
    const src = (artifactsSignal.value as ArtifactRow[]) || [];
    const filtered = filterArtifactsClient(sortPriorityThenId(src), filter);
    const offset = loadMore ? filesIndexItems.value.length : 0;
    const page = pageSlice(filtered, offset, limit);
    if (req !== filesIndexReq.value) return;
    filesIndexItems.value = loadMore
      ? [...filesIndexItems.value, ...page.artifacts]
      : page.artifacts;
    filesNextCursor.value = page.next_cursor;
    filesHasMore.value = page.has_more;
    filesCursorFilter.value = fp;
    filesIndexMode.value = "idle";
    filesIndexError.value = null;
    filesIndexLoading.value = false;
    return;
  }

  if (!pid) {
    if (req !== filesIndexReq.value) return;
    filesIndexItems.value = [];
    projectArtifacts.value = [];
    filesHasMore.value = false;
    filesNextCursor.value = null;
    filesCursorFilter.value = null;
    filesIndexMode.value = "idle";
    filesIndexLoading.value = false;
    return;
  }

  filesIndexLoading.value = true;
  const cursor = loadMore ? filesNextCursor.value : null;
  try {
    const page = await fetchArtifactIndex(pid, filter, cursor, limit);
    if (req !== filesIndexReq.value || project.value !== pid) return;
    filesIndexItems.value = loadMore
      ? [...filesIndexItems.value, ...page.artifacts]
      : page.artifacts;
    filesNextCursor.value = page.next_cursor;
    filesHasMore.value = page.has_more;
    filesCursorFilter.value = fp;
    projectArtifacts.value = filesIndexItems.value;
    filesIndexMode.value = "index";
    filesIndexError.value = null;
  } catch (e) {
    if (req !== filesIndexReq.value || project.value !== pid) return;
    if (isApiStatus(e, 400, "invalid_cursor")) {
      filesNextCursor.value = null;
      filesCursorFilter.value = null;
      filesIndexItems.value = [];
      await browseFiles({ reset: true, limit });
      return;
    }
    filesIndexItems.value = [];
    filesNextCursor.value = null;
    filesCursorFilter.value = null;
    filesHasMore.value = false;
    projectArtifacts.value = [];
    filesIndexMode.value = "error";
    filesIndexError.value = e instanceof Error ? e.message : filesT("files.index.unavailable");
  } finally {
    if (req === filesIndexReq.value) filesIndexLoading.value = false;
  }
}

export function setFilesQuery(value: string): void {
  filesQuery.value = value;
  dropFilesCursor();
}

export function setFilesContentType(value: string): void {
  filesContentType.value = value;
  dropFilesCursor();
}

export function setFilesOrigin(value: FilesOrigin): void {
  filesOrigin.value = value === "uploaded" || value === "generated" ? value : "";
  dropFilesCursor();
}
