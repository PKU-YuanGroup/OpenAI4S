import { describe, expect, it } from "vitest";
import {
  MESSAGE_PAGE_SIZE,
  MESSAGE_WALK_MAX_PAGES,
  SESSION_MAX_PAGES,
  SESSION_PAGE_SIZE,
  absorbSessionPage,
  annotateRunningCounts,
  canLoadMoreSessions,
  dateBucketId,
  emptySessionWalk,
  filterRootFrames,
  prependOlderMessages,
  recentDashboardSessions,
  runningDashboardFrames,
  sessionWalkBudget,
  sessionsInProject,
  shouldWalkEarlier,
  sortMessagesBySeq,
  sortSessionsByUpdatedAt,
  ungroupedSessions,
} from "./paging";

describe("message paging", () => {
  it("keeps MESSAGE_PAGE_SIZE 300 and the 200-page walk bound", () => {
    expect(MESSAGE_PAGE_SIZE).toBe(300);
    expect(MESSAGE_WALK_MAX_PAGES).toBe(200);
  });

  it("sorts a newest-first page back into reading order by seq", () => {
    const rows = [{ seq: 9 }, { seq: 3 }, { seq: 5 }, { seq: undefined }];
    expect(sortMessagesBySeq(rows).map((r) => r.seq || 0)).toEqual([0, 3, 5, 9]);
  });

  it("concatenates an older page in front of the held (newer) rows", () => {
    const newest = [{ seq: 301 }, { seq: 302 }];
    const older = [{ seq: 1 }, { seq: 2 }];
    expect(prependOlderMessages(older, newest).map((m) => m.seq)).toEqual([1, 2, 301, 302]);
  });

  it("stops the whole-conversation walk at MESSAGE_WALK_MAX_PAGES", () => {
    expect(shouldWalkEarlier(true, 10, MESSAGE_WALK_MAX_PAGES - 1)).toBe(true);
    expect(shouldWalkEarlier(true, 10, MESSAGE_WALK_MAX_PAGES)).toBe(false);
    expect(shouldWalkEarlier(true, null, 1)).toBe(false);
    expect(shouldWalkEarlier(false, 10, 1)).toBe(false);
  });
});

describe("session sort and sidebar grouping", () => {
  it("sorts sessions newest-first without mutating the source array", () => {
    const rows = [
      { id: "a", updated_at: "2026-01-01T00:00:00Z" },
      { id: "b", updated_at: "2026-06-01T00:00:00Z" },
      { id: "c", updated_at: "2026-03-01T00:00:00Z" },
    ];
    expect(sortSessionsByUpdatedAt(rows).map((r) => r.id)).toEqual(["b", "c", "a"]);
    expect(rows.map((r) => r.id)).toEqual(["a", "b", "c"]);
  });

  it("filters the sidebar to the open project", () => {
    const rows = [
      { id: "1", project_id: "p1" },
      { id: "2", project_id: "p2" },
    ];
    expect(sessionsInProject(rows, "p2").map((r) => r.id)).toEqual(["2"]);
    expect(sessionsInProject(rows, null)).toEqual(rows);
  });

  it("treats sessions whose folder is gone as ungrouped", () => {
    const rows = [
      { id: "1", folder_id: "f1" },
      { id: "2", folder_id: "missing" },
      { id: "3" },
    ];
    expect(
      ungroupedSessions(rows, [{ folder_id: "f1" }]).map((r) => r.id),
    ).toEqual(["2", "3"]);
  });
});

describe("session page walk", () => {
  it("keeps SESSION_PAGE_SIZE 100 and SESSION_MAX_PAGES 50", () => {
    expect(SESSION_PAGE_SIZE).toBe(100);
    expect(SESSION_MAX_PAGES).toBe(50);
  });

  it("drops child frames, dedupes by id, and stops when the cursor ends", () => {
    const state = emptySessionWalk();
    const first = absorbSessionPage(state, {
      frames: [
        { id: "a" },
        { id: "child", parent_frame_id: "a" },
        { id: "a" },
        { id: "b" },
      ],
      has_more: true,
      next_cursor: "c1",
    });
    expect(first.stop).toBe(false);
    expect(first.cursor).toBe("c1");
    expect(state.rows.map((r) => r.id)).toEqual(["a", "b"]);
    expect(state.walked).toBe(1);
    expect(state.hasMore).toBe(true);

    const second = absorbSessionPage(state, {
      frames: [{ id: "c" }],
      has_more: false,
      next_cursor: null,
    });
    expect(second.stop).toBe(true);
    expect(state.hasMore).toBe(false);
    expect(state.rows.map((r) => r.id)).toEqual(["a", "b", "c"]);
    expect(state.walked).toBe(2);
  });

  it("caps the walk budget at SESSION_MAX_PAGES", () => {
    expect(sessionWalkBudget(1)).toBe(1);
    expect(sessionWalkBudget(0)).toBe(1);
    expect(sessionWalkBudget(80)).toBe(SESSION_MAX_PAGES);
  });

  it("refuses load-more at the page cap even when has_more is true", () => {
    expect(
      canLoadMoreSessions({ loadingMore: false, hasMore: true, sessionPages: 49 }),
    ).toBe(true);
    expect(
      canLoadMoreSessions({ loadingMore: false, hasMore: true, sessionPages: 50 }),
    ).toBe(false);
    expect(
      canLoadMoreSessions({ loadingMore: true, hasMore: true, sessionPages: 2 }),
    ).toBe(false);
    expect(
      canLoadMoreSessions({ loadingMore: false, hasMore: false, sessionPages: 2 }),
    ).toBe(false);
  });
});

describe("dashboard frame filters", () => {
  it("keeps only root frames for the home list", () => {
    expect(
      filterRootFrames([{ id: "a" }, { id: "b", parent_frame_id: "a" }]).map((f) => f.id),
    ).toEqual(["a"]);
  });

  it("annotates each project with a live running-session count", () => {
    const projects = [
      { project_id: "p1", running_count: 0 },
      { id: "p2", running_count: 0 },
    ];
    annotateRunningCounts(projects, [
      { project_id: "p1", running: true },
      { project_id: "p1", running: true },
      { project_id: "p1", running: false },
      { project_id: "p2", running: true },
    ]);
    expect(projects[0]?.running_count).toBe(2);
    expect(projects[1]?.running_count).toBe(1);
  });

  it("recent list is non-empty sessions, newest first, capped at 10", () => {
    const frames = [];
    for (let i = 0; i < 12; i++) {
      frames.push({
        id: String(i),
        message_count: 1,
        updated_at: `2026-01-${String(i + 1).padStart(2, "0")}T00:00:00Z`,
      });
    }
    frames.push({ id: "empty", message_count: 0, updated_at: "2026-12-01T00:00:00Z" });
    const recent = recentDashboardSessions(frames);
    expect(recent).toHaveLength(10);
    expect(recent[0]?.id).toBe("11");
    expect(recent.some((f) => f.id === "empty")).toBe(false);
  });

  it("running hero is running frames newest first", () => {
    const running = runningDashboardFrames([
      { id: "a", running: true, updated_at: "2026-01-01T00:00:00Z" },
      { id: "b", running: false, updated_at: "2026-06-01T00:00:00Z" },
      { id: "c", running: true, updated_at: "2026-03-01T00:00:00Z" },
    ]);
    expect(running.map((f) => f.id)).toEqual(["c", "a"]);
  });

  it("date buckets match app.js:7023", () => {
    const now = Date.parse("2026-06-10T12:00:00Z");
    expect(dateBucketId("2026-06-10T11:00:00Z", now)).toBe("today");
    expect(dateBucketId("2026-06-09T11:00:00Z", now)).toBe("yesterday");
    expect(dateBucketId("2026-06-05T11:00:00Z", now)).toBe("thisWeek");
    expect(dateBucketId("2026-01-01T00:00:00Z", now)).toBe("older");
    expect(dateBucketId("not-a-date", now)).toBe("older");
  });
});
