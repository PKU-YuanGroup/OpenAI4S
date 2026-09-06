/**
 * Session / message paging and sort helpers.
 * Port of openai4s/server/webui/app.js:6914-7074 and dashboard filters 6697-6718.
 *
 * Newest-first fetch, then sort back into reading order (seq ascending).
 * Session walk is a keyset cursor, not an offset.
 */

export const MESSAGE_PAGE_SIZE = 300;
export const MESSAGE_WALK_MAX_PAGES = 200;
export const SESSION_PAGE_SIZE = 100;
export const SESSION_MAX_PAGES = 50;

export type SeqRow = { seq?: number };
export type SessionLike = {
  id?: string;
  parent_frame_id?: unknown;
  project_id?: string;
  folder_id?: string;
  name?: string;
  task_summary?: string;
  updated_at?: string;
  message_count?: number;
  running?: boolean;
  kernel_alive?: boolean;
};
export type FolderLike = { folder_id?: string; name?: string };
export type FramePage = {
  frames?: SessionLike[];
  has_more?: boolean;
  next_cursor?: string | null;
};
export type SessionWalkState = {
  rows: SessionLike[];
  seen: Set<string>;
  walked: number;
  hasMore: boolean;
};

export function sortMessagesBySeq<T extends SeqRow>(rows: T[]): T[] {
  rows.sort((a, b) => (a.seq || 0) - (b.seq || 0));
  return rows;
}

/** Older page belongs in front of the already-held (newer) rows. */
export function prependOlderMessages<T>(older: T[], newer: T[]): T[] {
  return older.concat(newer);
}

export function shouldWalkEarlier(earlier: boolean, cursor: unknown, pages: number): boolean {
  return earlier && cursor != null && pages < MESSAGE_WALK_MAX_PAGES;
}

export function sortSessionsByUpdatedAt<T extends { updated_at?: string }>(rows: T[]): T[] {
  return rows.slice().sort((a, b) => {
    return new Date(b.updated_at || "").getTime() - new Date(a.updated_at || "").getTime();
  });
}

export function sessionsInProject<T extends { project_id?: string }>(
  rows: T[],
  projectId: string | null | undefined,
): T[] {
  if (!projectId) return rows;
  return rows.filter((f) => f.project_id === projectId);
}

export function ungroupedSessions<T extends { folder_id?: string }>(
  rows: T[],
  folders: FolderLike[],
): T[] {
  return rows.filter((f) => !f.folder_id || !folders.some((x) => x.folder_id === f.folder_id));
}

export function sessionWalkBudget(sessionPages: number): number {
  return Math.min(SESSION_MAX_PAGES, Math.max(1, sessionPages || 1));
}

export function canLoadMoreSessions(opts: {
  loadingMore: boolean;
  hasMore: boolean;
  sessionPages: number;
}): boolean {
  if (opts.loadingMore || !opts.hasMore) return false;
  if ((opts.sessionPages || 1) >= SESSION_MAX_PAGES) return false;
  return true;
}

export function emptySessionWalk(): SessionWalkState {
  return { rows: [], seen: new Set<string>(), walked: 0, hasMore: false };
}

/**
 * Absorb one `/frames` page into the walk. Root frames only, id-deduped.
 * Returns the next cursor, or stop when the route is exhausted.
 */
export function absorbSessionPage(state: SessionWalkState, page: FramePage | null | undefined): {
  cursor: string | null;
  stop: boolean;
} {
  state.walked += 1;
  const frames = (page && page.frames) || [];
  for (const x of frames) {
    if (!x.parent_frame_id && x.id && !state.seen.has(x.id)) {
      state.seen.add(x.id);
      state.rows.push(x);
    }
  }
  const hasMore = !!(page && page.has_more);
  const cursor = (page && page.next_cursor) || null;
  state.hasMore = hasMore;
  if (!hasMore || !cursor) {
    state.hasMore = false;
    return { cursor: null, stop: true };
  }
  return { cursor, stop: false };
}

export function filterRootFrames<T extends { parent_frame_id?: unknown }>(frames: T[]): T[] {
  return frames.filter((f) => !f.parent_frame_id);
}

export function annotateRunningCounts<
  P extends { project_id?: string; id?: string; running_count?: number },
  F extends { project_id?: string; running?: boolean },
>(projects: P[], frames: F[]): P[] {
  const rc: Record<string, number> = {};
  frames.forEach((f) => {
    if (f.running && f.project_id) rc[f.project_id] = (rc[f.project_id] || 0) + 1;
  });
  projects.forEach((p) => {
    p.running_count = rc[p.project_id || p.id || ""] || 0;
  });
  return projects;
}

export function recentDashboardSessions<T extends SessionLike>(frames: T[]): T[] {
  return frames
    .filter((f) => (f.message_count || 0) > 0 || f.name || f.task_summary)
    .sort((a, b) => new Date(b.updated_at || "").getTime() - new Date(a.updated_at || "").getTime())
    .slice(0, 10);
}

export function runningDashboardFrames<T extends SessionLike>(frames: T[]): T[] {
  return (frames || [])
    .filter((f) => f.running)
    .sort((a, b) => new Date(b.updated_at || "").getTime() - new Date(a.updated_at || "").getTime());
}

export type DateBucket = "today" | "yesterday" | "thisWeek" | "older";

export function dateBucketId(iso: string | undefined, nowMs: number): DateBucket {
  const ts = new Date(iso || "").getTime();
  if (isNaN(ts)) return "older";
  const d = (nowMs - ts) / 86400000;
  if (d < 1) return "today";
  if (d < 2) return "yesterday";
  if (d < 7) return "thisWeek";
  return "older";
}

export const DATE_BUCKET_KEYS: Record<DateBucket, string> = {
  today: "date.bucket.today",
  yesterday: "date.bucket.yesterday",
  thisWeek: "date.bucket.thisWeek",
  older: "date.bucket.older",
};
