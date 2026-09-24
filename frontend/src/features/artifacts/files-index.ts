import { artifacts as artifactsSignal, artifactsFrameId, artifactsFrameGeneration, filesScope, projectArtifacts } from "../../stores/artifacts";
import { _openGen, currentId, project } from "../../stores/session";
import { api, asArtifactList, isApiStatus } from "./api";
import { filesT } from "./copy";
import {
  artifactsReadError,
  filesContentType,
  filesCursorFilter,
  filesHasMore,
  filesIndexError,
  filesIndexItems,
  filesIndexLoading,
  filesLoadedLimit,
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

/** Cursor identity includes scope, owner, and the complete filter. */
export function filterFingerprint(filter: FilesFilter, pid = "", scope = "project", generation = 0): string {
  return JSON.stringify({
    pid,
    scope,
    generation,
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

export function currentFilesFingerprint(): string {
  const scope = filesScope.value;
  return filterFingerprint(currentFilesFilter(), (scope === "project" ? project.value : currentId.value) || "", scope, scope === "project" ? 0 : _openGen.value);
}

export function filesListingIsCurrent(): boolean {
  return filesCursorFilter.value === currentFilesFingerprint();
}

/** Session scope: the open session's last artifact read failed. */
export function filesReadFailed(): boolean {
  const failed = artifactsReadError.value;
  return filesScope.value !== "project" && !!failed &&
    failed.frameId === currentId.value && failed.generation === _openGen.value;
}

/** Cards, count, and pagination share the same owned result set. */
export function filesGridArtifacts(): ArtifactRow[] {
  if (!filesListingIsCurrent()) return [];
  if (filesScope.value !== "project" && (!currentId.value || artifactsFrameId.value !== currentId.value || artifactsFrameGeneration.value !== _openGen.value)) return [];
  return filesIndexItems.value.filter((a) => (a.priority || 0) >= 0);
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


function dropFilesCursor(): void {
  filesNextCursor.value = null;
  filesCursorFilter.value = null;
  filesIndexItems.value = [];
  filesLoadedLimit.value = FILES_PAGE_SIZE;
  filesHasMore.value = false;
  filesIndexLoading.value = false;
  filesIndexError.value = null;
  filesIndexMode.value = "idle";
  filesIndexReq.value = (filesIndexReq.value || 0) + 1;
}

export type BrowseFilesOpts = { reset?: boolean; loadMore?: boolean; refresh?: boolean; limit?: number };

/**
 * M-03 Files listing. Project scope walks B-06 artifact-index (50/page, cap 100)
 * and never falls back to `GET /projects/{pid}/artifacts`. Filter changes drop
 * the previous cursor (client fingerprint + server 400 invalid_cursor). A
 * Project switch drops late responses via `filesIndexReq`. Frame scope filters
 * the session array locally — the index route is project-scoped.
 */
export async function browseFiles(opts: BrowseFilesOpts = {}): Promise<void> {
  if (opts.loadMore && filesIndexLoading.value && filesListingIsCurrent()) return;
  const req = ++filesIndexReq.value;
  const filter = currentFilesFilter();
  const scope = filesScope.value;
  const limit = clampLimit(opts.limit ?? FILES_PAGE_SIZE);
  const pid = project.value || "";
  const fid = currentId.value;
  const fp = currentFilesFingerprint();
  const same = filesCursorFilter.value === fp;
  const loadMore = !!opts.loadMore && !opts.reset && same;
  const refresh = !!opts.refresh && !opts.reset && same;
  const capacity = loadMore ? filesLoadedLimit.value + limit : refresh ? filesLoadedLimit.value : limit;
  const owns = () => req === filesIndexReq.value && fp === currentFilesFingerprint();
  if (!same || opts.reset) {
    filesIndexItems.value = [];
    filesNextCursor.value = null;
    filesHasMore.value = false;
    filesLoadedLimit.value = limit;
    filesCursorFilter.value = null;
    filesIndexError.value = null;
  }

  if (scope !== "project") {
    const src = fid && artifactsFrameId.value === fid && artifactsFrameGeneration.value === _openGen.value ? artifactsSignal.value as ArtifactRow[] : [];
    const filtered = filterArtifactsClient(sortPriorityThenId(src), filter);
    // Re-slice the current snapshot rather than appending a stale prefix.
    const items = filtered.slice(0, capacity);
    if (!owns()) return;
    filesIndexItems.value = items;
    filesLoadedLimit.value = capacity;
    filesHasMore.value = items.length < filtered.length;
    filesNextCursor.value = filesHasMore.value ? String(items.length) : null;
    filesCursorFilter.value = fp;
    filesIndexMode.value = "idle";
    filesIndexError.value = null;
    filesIndexLoading.value = false;
    return;
  }

  if (!pid) {
    filesIndexItems.value = [];
    projectArtifacts.value = [];
    filesHasMore.value = false;
    filesNextCursor.value = null;
    filesCursorFilter.value = fp;
    filesIndexMode.value = "idle";
    filesIndexLoading.value = false;
    return;
  }

  filesIndexLoading.value = true;
  let cursor = loadMore ? filesNextCursor.value : null;
  const rows = loadMore ? [...filesIndexItems.value] : [];
  let page: ArtifactIndexPage;
  try {
    // A refresh walks the previously loaded capacity through the same index;
    // it never substitutes the unpaged project array route.
    const seenCursors = new Set<string>();
    do {
      if (!owns()) return;
      page = await fetchArtifactIndex(pid, filter, cursor, Math.min(limit, Math.max(1, capacity - rows.length)));
      if (!owns()) return;
      const previousLength = rows.length;
      for (const row of page.artifacts) {
        const index = rows.findIndex((previous) => previous.id === row.id);
        if (index < 0) rows.push(row);
        else rows[index] = row;
      }
      if (refresh && page.has_more && rows.length === previousLength) throw new Error(filesT("files.index.unavailable"));
      cursor = page.next_cursor;
      if (cursor && seenCursors.has(cursor)) throw new Error(filesT("files.index.unavailable"));
      if (cursor) seenCursors.add(cursor);
    } while (refresh && page.has_more && cursor && rows.length < capacity);
    filesIndexItems.value = rows;
    filesLoadedLimit.value = capacity;
    filesNextCursor.value = cursor;
    filesHasMore.value = page.has_more;
    filesCursorFilter.value = fp;
    projectArtifacts.value = rows;
    filesIndexMode.value = "index";
    filesIndexError.value = null;
  } catch (e) {
    if (!owns()) return;
    if (isApiStatus(e, 400, "invalid_cursor")) {
      dropFilesCursor();
      await browseFiles({ reset: true, limit });
      return;
    }
    filesIndexItems.value = [];
    filesNextCursor.value = null;
    filesHasMore.value = false;
    filesCursorFilter.value = fp;
    projectArtifacts.value = [];
    filesIndexMode.value = "error";
    filesIndexError.value = e instanceof Error ? e.message : filesT("files.index.unavailable");
  } finally {
    if (owns()) filesIndexLoading.value = false;
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
