import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({ api: vi.fn() }));

import { api } from "./api";
import { loadProjects, projectsReplaceInFlight } from "./load";
import { projects, projectsHasMore, projectsNextCursor } from "../../stores/session";

const page = (ids: string[], cursor: string | null) => ({
  projects: ids.map((id) => ({ project_id: id, name: id })),
  next_cursor: cursor,
  has_more: cursor !== null,
  total: ids.length,
});

describe("a search in flight refuses a load-more", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    projects.value = [];
    projectsHasMore.value = false;
    projectsNextCursor.value = null;
  });
  afterEach(() => vi.mocked(api).mockReset());

  it("keeps the older query's page two from landing under the newer query", async () => {
    // Page one of the unfiltered directory, with more behind it.
    vi.mocked(api).mockResolvedValueOnce(page(["a"], "c1"));
    await loadProjects({ q: "" });
    expect(projectsReplaceInFlight()).toBe(false);

    // A debounced search takes a newer generation and waits on the daemon.
    let answer: (value: unknown) => void = () => {};
    vi.mocked(api).mockReturnValueOnce(new Promise((resolve) => (answer = resolve)));
    const search = loadProjects({ q: "beta" });
    expect(projectsReplaceInFlight()).toBe(true);

    // The still-rendered Load-more button is clicked meanwhile. Before the
    // gate, this took generation N+1 with the OLD query and cursor, and the
    // search reply was then discarded as stale.
    vi.mocked(api).mockResolvedValueOnce(page(["b"], null));
    await loadProjects({ append: true });
    expect(vi.mocked(api).mock.calls).toHaveLength(2);

    answer(page(["z"], null));
    await search;
    expect(projectsReplaceInFlight()).toBe(false);
    expect((projects.value as { project_id: string }[]).map((row) => row.project_id)).toEqual(["z"]);
  });
});
