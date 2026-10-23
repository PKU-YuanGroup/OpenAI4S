/**
 * C2: the Timeline's own read of the session's remote compute tasks.
 *
 * The workbench fans nine projections out through `optionalApi`, which turns
 * any failure into `undefined`. For a task list that is the wrong fact: the
 * panel then rendered the previous success (or "no tasks") as if it were the
 * current answer. This controller keeps a real state machine instead —
 * session + filter + monotonic request token — so:
 *
 *   - a failure is an error state, and the previous page survives only as an
 *     explicitly stale snapshot;
 *   - another session's page is never presented under this session;
 *   - a late answer for a filter/session the user has left is dropped;
 *   - paging appends a page from the previous response's cursor.
 *
 * The GET itself is read-only: filtering and paging only re-read (no POST, no
 * remote action, no harvest).
 */
import { api, apiErrorText } from "./api";
import { mergeComputeTaskPages, sanitizeComputeTasks } from "./sanitize";
import type { ComputeRecordsState, ComputeTaskFilter } from "./types";

export function computeRecordsPath(
  fid: string,
  filter: ComputeTaskFilter,
  cursor: string | null,
): string {
  const query = new URLSearchParams();
  if (filter === "live") query.set("state", "live");
  if (cursor) query.set("cursor", cursor);
  const suffix = query.toString();
  return `/frames/${encodeURIComponent(fid)}/compute/tasks${suffix ? "?" + suffix : ""}`;
}

export type ComputeRecordsRead = (
  fid: string,
  filter: ComputeTaskFilter,
  cursor: string | null,
) => Promise<unknown>;

export type ComputeRecordsDeps = {
  /** Injected in tests; defaults to the documented GET. */
  read?: ComputeRecordsRead;
  now?: () => number;
  /** Who the view currently belongs to; a stale answer for anyone else is dropped. */
  current?: () => string | null;
  onChange?: (state: ComputeRecordsState | null) => void;
};

export type ComputeRecordsController = {
  readonly state: ComputeRecordsState | null;
  /** First page of `filter`; `append` loads the held page's next cursor. */
  read(
    fid: string,
    options?: { filter?: ComputeTaskFilter; append?: boolean },
  ): Promise<void>;
  reset(): void;
};

export function createComputeRecordsController(
  deps: ComputeRecordsDeps = {},
): ComputeRecordsController {
  const readPage: ComputeRecordsRead =
    deps.read || ((fid, filter, cursor) => api(computeRecordsPath(fid, filter, cursor)));
  const now = deps.now || (() => Date.now());
  const current = deps.current;
  let state: ComputeRecordsState | null = null;
  let token = 0;

  const publish = (next: ComputeRecordsState | null): void => {
    state = next;
    deps.onChange?.(next);
  };
  const owns = (next: ComputeRecordsState, request: number): boolean => {
    if (state !== next || state.request !== request) return false;
    const owner = current ? current() : next.fid;
    return owner === next.fid;
  };

  async function read(
    fid: string,
    options: { filter?: ComputeTaskFilter; append?: boolean } = {},
  ): Promise<void> {
    if (!fid) return;
    const previous = state && state.fid === fid ? state : null;
    const filter = options.filter || previous?.filter || "all";
    const append =
      options.append === true &&
      previous != null &&
      previous.filter === filter &&
      previous.page != null &&
      !previous.loadingMore;
    // An append with nothing to append is not an error: there is no cursor or
    // a page is already in flight. Only the user's first-page read always runs.
    if (options.append === true && !append) return;
    const cursor = append ? previous!.page!.next_cursor : null;
    if (append && !cursor) return;

    const request = ++token;
    const next: ComputeRecordsState = append
      ? { ...previous!, phase: "ready", loadingMore: true, error: "", request }
      : {
          fid,
          filter,
          phase: "loading",
          // Same session and filter keeps the held page visible while the new
          // read runs; another filter has no comparable rows to show.
          page: previous && previous.filter === filter ? previous.page : null,
          stale: false,
          error: "",
          updatedAt: previous && previous.filter === filter ? previous.updatedAt : 0,
          loadingMore: false,
          request,
        };
    publish(next);

    let raw: unknown;
    try {
      raw = await readPage(fid, filter, cursor);
    } catch (error) {
      if (!owns(next, request)) return;
      publish({
        ...next,
        phase: "error",
        stale: next.page != null,
        loadingMore: false,
        error: apiErrorText(error),
      });
      return;
    }
    if (!owns(next, request)) return;
    const page = sanitizeComputeTasks(raw);
    publish({
      ...next,
      phase: "ready",
      page: append ? mergeComputeTaskPages(next.page, page) : page,
      stale: false,
      loadingMore: false,
      error: "",
      updatedAt: now(),
    });
  }

  function reset(): void {
    token += 1;
    publish(null);
  }

  return {
    get state() {
      return state;
    },
    read,
    reset,
  };
}
