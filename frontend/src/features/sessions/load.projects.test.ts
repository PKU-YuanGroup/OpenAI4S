import { describe, expect, it } from "vitest";
import {
  PROJECT_PAGE_SIZE,
  PROJECT_Q_MAX,
  canLoadMoreProjects,
  mergeProjectPage,
  normalizeProjectQuery,
  projectDashView,
  projectListQuery,
} from "./load";

describe("project directory paging", () => {
  it("keeps PROJECT_PAGE_SIZE at 100 and never puts offset on the wire", () => {
    expect(PROJECT_PAGE_SIZE).toBe(100);
    const path = projectListQuery({ q: "  Alpha  ", cursor: "abc", limit: 100 });
    expect(path.startsWith("/projects?")).toBe(true);
    expect(path).not.toContain("offset");
    expect(path).toContain("limit=100");
    expect(path).toContain("cursor=abc");
    expect(path).toContain("q=Alpha");
  });

  it("trims q and caps it at 128 Unicode code points", () => {
    expect(normalizeProjectQuery("  café  ")).toBe("café");
    const long = "é".repeat(PROJECT_Q_MAX + 5);
    expect(Array.from(normalizeProjectQuery(long))).toHaveLength(PROJECT_Q_MAX);
    expect(projectListQuery({ q: "   " })).toBe("/projects?limit=100");
  });

  it("replaces, appends, and dedupes by project_id", () => {
    const first = mergeProjectPage([], [{ project_id: "a" }, { project_id: "a" }, { id: "b" }], "replace");
    expect(first.map((row) => row.project_id || row.id)).toEqual(["a", "b"]);
    const second = mergeProjectPage(first, [{ project_id: "b" }, { project_id: "c" }], "append");
    expect(second.map((row) => row.project_id || row.id)).toEqual(["a", "b", "c"]);
  });

  it("refuses load-more without a cursor, while loading, or when has_more is false", () => {
    expect(canLoadMoreProjects({ loadingMore: false, hasMore: true, cursor: "c1" })).toBe(true);
    expect(canLoadMoreProjects({ loadingMore: true, hasMore: true, cursor: "c1" })).toBe(false);
    expect(canLoadMoreProjects({ loadingMore: false, hasMore: false, cursor: "c1" })).toBe(false);
    expect(canLoadMoreProjects({ loadingMore: false, hasMore: true, cursor: null })).toBe(false);
  });

  it("maps empty, no-match, error, retry, and load-more view states", () => {
    expect(projectDashView({ error: true, count: 0, query: "", hasMore: false, loadingMore: false })).toEqual({
      kind: "error",
    });
    expect(projectDashView({ error: false, count: 0, query: "", hasMore: false, loadingMore: false })).toEqual({
      kind: "empty",
    });
    expect(projectDashView({ error: false, count: 0, query: "lab", hasMore: false, loadingMore: false })).toEqual({
      kind: "no-match",
    });
    expect(
      projectDashView({ error: false, count: 3, query: "", hasMore: true, loadingMore: true }),
    ).toEqual({ kind: "list", showMore: true, loadingMore: true });
  });

  it("keeps the loaded rows when a load-more fails, so the button is the retry", () => {
    // loadProjects only clears the store on a failed *replace*; a failed
    // append leaves the pages already fetched, and the card must not
    // discard them behind a whole-list error box.
    expect(
      projectDashView({ error: true, count: 100, query: "", hasMore: true, loadingMore: false }),
    ).toEqual({ kind: "list", showMore: true, loadingMore: false });
  });
});
