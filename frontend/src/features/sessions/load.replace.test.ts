import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({ api: vi.fn() }));

import { api } from "./api";
import { loadProjects } from "./load";
import { resetStoreFields } from "../../stores/signal-field";
import { projectSearch, projects, projectsQuery } from "../../stores/session";

const page = (ids: string[], cursor: string | null) => ({
  projects: ids.map((id) => ({ project_id: id, name: id })),
  next_cursor: cursor,
  has_more: cursor !== null,
  total: ids.length,
});
const ids = (rows: unknown[]) => (rows as { project_id: string }[]).map((row) => row.project_id);

describe("a search in flight refuses a load-more", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    resetStoreFields();
  });
  afterEach(() => vi.mocked(api).mockReset());

  it("keeps the older query's page two from landing under the newer query", async () => {
    // Page one of the unfiltered directory, with more behind it.
    vi.mocked(api).mockResolvedValueOnce(page(["a"], "c1"));
    await loadProjects({ q: "" });

    // The box takes a query; its debounced search waits on the daemon.
    projectsQuery.value = "beta";
    let answer: (value: unknown) => void = () => {};
    vi.mocked(api).mockReturnValueOnce(new Promise((resolve) => (answer = resolve)));
    const search = loadProjects({ q: "beta" });

    // The still-rendered Load-more button is clicked meanwhile. Before the
    // gate, this took generation N+1 with the OLD query and cursor, and the
    // search reply was then discarded as stale.
    vi.mocked(api).mockResolvedValueOnce(page(["b"], null));
    await loadProjects({ append: true });
    expect(vi.mocked(api).mock.calls).toHaveLength(2);

    answer(page(["z"], null));
    await search;
    expect(ids(projectSearch.value)).toEqual(["z"]);
    // The search has pages of its own; the directory is what it was.
    expect(ids(projects.value)).toEqual(["a"]);
  });
});
