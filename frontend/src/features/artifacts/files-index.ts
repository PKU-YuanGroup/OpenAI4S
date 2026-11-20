import { artifacts as artifactsSignal, artifactsFrameId, artifactsFrameGeneration, filesScope, projectArtifacts } from "../../stores/artifacts";
import { _openGen, currentId, project } from "../../stores/session";
import { clampIndexLimit, fetchArtifactIndexPage, isApiStatus } from "./api";
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
  filesKind,
  filesNextCursor,
  filesOrigin,
  filesQuery,
  filesShowHidden,
  filesSort,
} from "./state";
import type { ArtifactIndexPage, ArtifactRow, FilesKind, FilesOrigin, FilesSort } from "./types";
import { FILES_PAGE_SIZE } from "./types";

export type FilesFilter = {
  q: string;
  contentType: string;
  origin: FilesOrigin;
  /** ART-12 human category; "" is every kind. */
  kind?: FilesKind;
  /** ART-12: rows hidden from the list are included. */
  hidden?: boolean;
  /** ART-12 session-list order. */
  sort?: FilesSort;
};

export function currentFilesFilter(): FilesFilter {
  return {
    q: filesQuery.value.trim(),
    contentType: filesContentType.value.trim(),
    origin: filesOrigin.value,
    kind: filesKind.value,
    hidden: filesShowHidden.value,
    sort: filesSort.value,
  };
}

/**
 * ART-12 human categories, in precedence order: a file is the first kind
 * whose extension or content-type prefix matches, and "other" is the rest.
 * The server's `ARTIFACT_KINDS` (openai4s/storage/artifacts.py) is the same
 * table, so both scopes agree.
 */
const KIND_RULES: ReadonlyArray<
  readonly [Exclude<FilesKind, "" | "other">, readonly string[], readonly string[]]
> = [
  [
    "table",
    ["csv", "tsv", "tab", "xlsx", "xls", "parquet", "feather", "arrow"],
    [
      "text/csv",
      "text/tab-separated-values",
      "application/vnd.openxmlformats-officedocument.spreadsheetml",
      "application/vnd.ms-excel",
    ],
  ],
  ["image", ["png", "jpg", "jpeg", "gif", "webp", "svg", "tif", "tiff", "bmp"], ["image/"]],
  [
    "report",
    ["md", "markdown", "rst", "pdf", "html", "htm", "docx", "doc", "tex", "ipynb", "txt"],
    ["text/markdown", "application/pdf", "text/html", "application/x-ipynb+json"],
  ],
  [
    "code",
    ["py", "r", "sh", "bash", "js", "ts", "sql", "jl", "m", "c", "cpp", "h", "java", "rs", "go", "yaml", "yml", "toml"],
    ["text/x-python", "text/x-r", "application/x-sh"],
  ],
];

/** The human category of one file (ART-12). */
export function artifactKind(a: Pick<ArtifactRow, "filename" | "content_type">): Exclude<FilesKind, ""> {
  const name = String(a.filename || "").toLowerCase();
  const type = String(a.content_type || "").toLowerCase();
  for (const [kind, extensions, prefixes] of KIND_RULES) {
    if (extensions.some((ext) => name.endsWith("." + ext))) return kind;
    if (prefixes.some((prefix) => type.startsWith(prefix))) return kind;
  }
  return "other";
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
    kind: filter.kind || "",
    hidden: !!filter.hidden,
    sort: filter.sort || "newest",
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
    if ((a.priority || 0) < 0 && !filter.hidden) continue;
    const name = String(a.filename || "");
    if (q && !name.toLowerCase().includes(q)) continue;
    if (ct && !String(a.content_type || "").toLowerCase().includes(ct)) continue;
    if (filter.origin && originOf(a) !== filter.origin) continue;
    if (filter.kind && artifactKind(a) !== filter.kind) continue;
    out.push(a);
  }
  return out;
}

const nameCollator =
  typeof Intl !== "undefined" && typeof Intl.Collator === "function"
    ? new Intl.Collator(undefined, { numeric: true, sensitivity: "base" })
    : null;

function createdMs(a: ArtifactRow): number {
  const value = Date.parse(String(a.created_at || ""));
  return Number.isFinite(value) ? value : 0;
}

/**
 * ART-12: starred first, then the chosen order. The list used to fall back to
 * the artifact id -- random hex -- so it was neither by name nor by time.
 */
export function sortArtifactsForFiles(rows: ArtifactRow[], sort: FilesSort = "newest"): ArtifactRow[] {
  return rows.slice().sort((x, y) => {
    const dp = (y.priority || 0) - (x.priority || 0);
    if (dp) return dp;
    if (sort === "name") {
      const a = String(x.filename || "");
      const b = String(y.filename || "");
      const byName = nameCollator ? nameCollator.compare(a, b) : a < b ? -1 : a > b ? 1 : 0;
      if (byName) return byName;
    } else {
      const dt = createdMs(y) - createdMs(x);
      if (dt) return sort === "oldest" ? -dt : dt;
    }
    return String(y.id).localeCompare(String(x.id));
  });
}

/**
 * ART-4: a scratch script or a log the agent wrote. Kept and openable, but
 * folded away from the results and left out of their count. A person's own
 * input is never one, whatever folder its copy sits in.
 */
export function isProcessFile(a: ArtifactRow): boolean {
  return a.role === "process" && !a.is_user_upload;
}

/** app.js:8492-8495. The conversation's inputs and results: no process files. */
export function visibleArtifacts(): ArtifactRow[] {
  const src = (artifactsSignal.value as ArtifactRow[]) || [];
  return src
    .filter((a) => (a.priority || 0) >= 0 && a.role !== "internal" && !isProcessFile(a))
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

/**
 * The grid has no answer yet for what it would show: an index read in
 * flight, a filter change not yet browsed, or a session whose artifact list
 * has not arrived. No cards then means "not known yet", not "no files".
 */
export function filesGridPending(): boolean {
  if (filesIndexLoading.value || !filesListingIsCurrent()) return true;
  if (filesScope.value === "project" || !currentId.value || filesReadFailed()) return false;
  return artifactsFrameId.value !== currentId.value || artifactsFrameGeneration.value !== _openGen.value;
}

/** Cards, count, and pagination share the same owned result set. */
export function filesGridArtifacts(): ArtifactRow[] {
  if (!filesListingIsCurrent()) return [];
  if (filesScope.value !== "project" && (!currentId.value || artifactsFrameId.value !== currentId.value || artifactsFrameGeneration.value !== _openGen.value)) return [];
  const showHidden = filesShowHidden.value;
  return filesIndexItems.value.filter(
    (a) => a.role !== "internal" && (showHidden || (a.priority || 0) >= 0),
  );
}

function fetchArtifactIndex(
  pid: string,
  filter: FilesFilter,
  cursor: string | null,
  limit: number,
): Promise<ArtifactIndexPage> {
  return fetchArtifactIndexPage(pid, {
    q: filter.q,
    contentType: filter.contentType,
    origin: filter.origin,
    kind: filter.kind || "",
    hidden: !!filter.hidden,
    cursor,
    limit,
  });
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
  const limit = clampIndexLimit(opts.limit ?? FILES_PAGE_SIZE);
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
    const filtered = filterArtifactsClient(sortArtifactsForFiles(src, filter.sort), filter);
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

const KINDS: readonly FilesKind[] = ["", "table", "image", "report", "code", "other"];

export function setFilesKind(value: string): void {
  filesKind.value = (KINDS as readonly string[]).includes(value) ? (value as FilesKind) : "";
  dropFilesCursor();
}

export function setFilesSort(value: string): void {
  filesSort.value = value === "oldest" || value === "name" ? value : "newest";
  dropFilesCursor();
}

export function setFilesShowHidden(value: boolean): void {
  filesShowHidden.value = !!value;
  dropFilesCursor();
}
