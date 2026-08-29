import { artifacts as artifactsSignal, filesScope, projectArtifacts } from "../../stores/artifacts";
import { project } from "../../stores/session";
import { api, asArtifactList, isApiStatus } from "./api";
import { filesT } from "./copy";
import {
  filesContentType,
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

export function filterFingerprint(filter: FilesFilter): string {
  return JSON.stringify({
    q: filter.q,
    contentType: filter.contentType,
    origin: filter.origin,
  });
}

function originOf(a: ArtifactRow): "uploaded" | "generated" {
  return a.is_user_upload ? "uploaded" : "generated";
}

/**
 * Client-side filter matching B-06 semantics: filename substring,
 * content_type substring, origin from `is_user_upload`. Hidden
 * (`priority < 0`) rows stay out. Same-name rows are not merged.
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

/**
 * TODO(F-17/M-03): drop this `/projects/{pid}/artifacts` array fallback once
 * the Files dock talks only to B-06's artifact-index route.
 */
async function fetchProjectArray(pid: string): Promise<ArtifactRow[]> {
  const body = await api(`/projects/${encodeURIComponent(pid)}/artifacts`);
  return asArtifactList(body);
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

async function applyFallbackPage(
  pid: string,
  req: number,
  filter: FilesFilter,
  loadMore: boolean,
): Promise<void> {
  const all = await fetchProjectArray(pid);
  if (req !== filesIndexReq.value || project.value !== pid) return;
  const filtered = filterArtifactsClient(all, filter);
  const offset = loadMore ? filesIndexItems.value.length : 0;
  const page = pageSlice(filtered, offset, FILES_PAGE_SIZE);
  filesIndexItems.value = loadMore ? [...filesIndexItems.value, ...page.artifacts] : page.artifacts;
  filesNextCursor.value = page.next_cursor;
  filesHasMore.value = page.has_more;
  projectArtifacts.value = filesIndexItems.value;
  filesIndexMode.value = "fallback";
  filesIndexError.value = filesT("files.index.fallback");
}

export type BrowseFilesOpts = { reset?: boolean; loadMore?: boolean; limit?: number };

/**
 * M-03 Files listing. Project scope walks artifact-index (50/page, cap 100).
 * Filter changes drop the previous cursor. A Project switch drops late
 * responses via `filesIndexReq`. Frame scope filters the session array locally.
 */
export async function browseFiles(opts: BrowseFilesOpts = {}): Promise<void> {
  const req = (filesIndexReq.value || 0) + 1;
  filesIndexReq.value = req;
  const filter = currentFilesFilter();
  const scope = filesScope.value;
  const limit = opts.limit ?? FILES_PAGE_SIZE;

  if (opts.reset || !opts.loadMore) {
    if (!opts.loadMore) filesNextCursor.value = null;
  }

  if (scope !== "project") {
    const src = (artifactsSignal.value as ArtifactRow[]) || [];
    const filtered = filterArtifactsClient(sortPriorityThenId(src), filter);
    const offset = opts.loadMore ? filesIndexItems.value.length : 0;
    const page = pageSlice(filtered, offset, limit);
    if (req !== filesIndexReq.value) return;
    filesIndexItems.value = opts.loadMore
      ? [...filesIndexItems.value, ...page.artifacts]
      : page.artifacts;
    filesNextCursor.value = page.next_cursor;
    filesHasMore.value = page.has_more;
    filesIndexMode.value = "idle";
    filesIndexError.value = null;
    filesIndexLoading.value = false;
    return;
  }

  const pid = project.value;
  if (!pid) {
    if (req !== filesIndexReq.value) return;
    filesIndexItems.value = [];
    projectArtifacts.value = [];
    filesHasMore.value = false;
    filesNextCursor.value = null;
    filesIndexMode.value = "idle";
    filesIndexLoading.value = false;
    return;
  }

  filesIndexLoading.value = true;
  const cursor = opts.loadMore ? filesNextCursor.value : null;
  try {
    const page = await fetchArtifactIndex(pid, filter, cursor, limit);
    if (req !== filesIndexReq.value || project.value !== pid) return;
    filesIndexItems.value = opts.loadMore
      ? [...filesIndexItems.value, ...page.artifacts]
      : page.artifacts;
    filesNextCursor.value = page.next_cursor;
    filesHasMore.value = page.has_more;
    projectArtifacts.value = filesIndexItems.value;
    filesIndexMode.value = "index";
    filesIndexError.value = null;
  } catch (e) {
    if (req !== filesIndexReq.value || project.value !== pid) return;
    if (isApiStatus(e, 400, "invalid_cursor")) {
      filesNextCursor.value = null;
      filesIndexItems.value = [];
      await browseFiles({ reset: true, limit });
      return;
    }
    if (isApiStatus(e, 404) || isApiStatus(e, 501)) {
      try {
        await applyFallbackPage(pid, req, filter, !!opts.loadMore);
      } catch {
        if (req !== filesIndexReq.value) return;
        filesIndexError.value = filesT("files.version.notFound");
      }
    } else {
      filesIndexError.value = e instanceof Error ? e.message : String(e);
    }
  } finally {
    if (req === filesIndexReq.value) filesIndexLoading.value = false;
  }
}

export function setFilesQuery(value: string): void {
  filesQuery.value = value;
  filesNextCursor.value = null;
  filesIndexItems.value = [];
}

export function setFilesContentType(value: string): void {
  filesContentType.value = value;
  filesNextCursor.value = null;
  filesIndexItems.value = [];
}

export function setFilesOrigin(value: FilesOrigin): void {
  filesOrigin.value = value === "uploaded" || value === "generated" ? value : "";
  filesNextCursor.value = null;
  filesIndexItems.value = [];
}
