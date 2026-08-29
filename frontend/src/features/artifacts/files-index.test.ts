import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { artifacts as artifactsSignal, filesScope, projectArtifacts } from "../../stores/artifacts";
import { project } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { setArtifactsFetch } from "./api";
import {
  browseFiles,
  filterArtifactsClient,
  filesGridArtifacts,
} from "./files-index";
import { jsonResponse } from "./http-stub";
import {
  filesHasMore,
  filesIndexError,
  filesIndexItems,
  filesIndexMode,
  filesIndexReq,
  filesNextCursor,
  resetFilesIndexState,
} from "./state";
import type { ArtifactRow } from "./types";
import { FILES_PAGE_SIZE } from "./types";

function row(partial: Partial<ArtifactRow> & { id: string }): ArtifactRow {
  return {
    filename: `${partial.id}.csv`,
    content_type: "text/csv",
    is_user_upload: false,
    priority: 0,
    ...partial,
  };
}

function make500(): ArtifactRow[] {
  const rows: ArtifactRow[] = [];
  for (let i = 0; i < 500; i++) {
    rows.push(
      row({
        id: `a-${String(i).padStart(3, "0")}`,
        filename: i % 2 === 0 ? "report.csv" : `plot-${i}.png`,
        content_type: i % 2 === 0 ? "text/csv" : "image/png",
        is_user_upload: i % 5 === 0,
        created_at: String(1000 - i),
      }),
    );
  }
  return rows;
}

describe("M-03 Files index + client fallback", () => {
  beforeEach(() => {
    resetStoreFields();
    resetFilesIndexState();
    setArtifactsFetch(null);
  });

  afterEach(() => {
    setArtifactsFetch(null);
  });

  it("filters by filename, content type, and origin without merging same names", () => {
    const rows = [
      row({ id: "1", filename: "report.csv", content_type: "text/csv", is_user_upload: true }),
      row({ id: "2", filename: "report.csv", content_type: "text/csv", is_user_upload: false }),
      row({ id: "3", filename: "plot.png", content_type: "image/png", is_user_upload: false }),
      row({ id: "4", filename: "hidden.csv", priority: -1 }),
    ];
    const named = filterArtifactsClient(rows, { q: "report", contentType: "", origin: "" });
    expect(named.map((a) => a.id)).toEqual(["1", "2"]);
    const csv = filterArtifactsClient(rows, { q: "", contentType: "text/csv", origin: "" });
    expect(csv.map((a) => a.id)).toEqual(["1", "2"]);
    const uploaded = filterArtifactsClient(rows, { q: "", contentType: "", origin: "uploaded" });
    expect(uploaded.map((a) => a.id)).toEqual(["1"]);
    const generated = filterArtifactsClient(rows, { q: "", contentType: "", origin: "generated" });
    expect(generated.map((a) => a.id)).toEqual(["2", "3"]);
  });

  it("first index page is ≤ 50 of a 500-artifact fixture", async () => {
    const all = make500();
    project.value = "p1";
    filesScope.value = "project";
    setArtifactsFetch(async (url) => {
      expect(url).toContain("/projects/p1/artifact-index");
      expect(url).toContain("limit=50");
      expect(url).not.toContain("cursor=");
      return jsonResponse({
        artifacts: all.slice(0, FILES_PAGE_SIZE),
        next_cursor: "cur-1",
        has_more: true,
      });
    });
    await browseFiles({ reset: true });
    expect(filesIndexItems.value).toHaveLength(50);
    expect(filesHasMore.value).toBe(true);
    expect(filesNextCursor.value).toBe("cur-1");
    expect(filesIndexMode.value).toBe("index");
    expect(filesGridArtifacts()).toHaveLength(50);
  });

  it("load-more keeps the previous cursor until filters change", async () => {
    project.value = "p1";
    filesScope.value = "project";
    const seen: string[] = [];
    setArtifactsFetch(async (url) => {
      seen.push(url);
      if (url.includes("cursor=cur-1")) {
        return jsonResponse({
          artifacts: [row({ id: "page-2" })],
          next_cursor: null,
          has_more: false,
        });
      }
      return jsonResponse({
        artifacts: [row({ id: "page-1" })],
        next_cursor: "cur-1",
        has_more: true,
      });
    });
    await browseFiles({ reset: true });
    await browseFiles({ loadMore: true });
    expect(filesIndexItems.value.map((a) => a.id)).toEqual(["page-1", "page-2"]);
    expect(seen[1]).toContain("cursor=cur-1");
  });

  it("a filter change drops the previous cursor", async () => {
    project.value = "p1";
    filesScope.value = "project";
    const seen: string[] = [];
    setArtifactsFetch(async (url) => {
      seen.push(url);
      return jsonResponse({ artifacts: [row({ id: "x" })], next_cursor: "c", has_more: true });
    });
    await browseFiles({ reset: true });
    filesNextCursor.value = "stale-cursor";
    await browseFiles({ reset: true });
    expect(seen[1]).not.toContain("stale-cursor");
    expect(seen[1]).not.toContain("cursor=");
  });

  it("drops a late response after the project switches", async () => {
    filesScope.value = "project";
    project.value = "p1";
    let resolveP1: ((body: unknown) => void) | undefined;
    const p1Body = new Promise<unknown>((resolve) => {
      resolveP1 = resolve;
    });
    setArtifactsFetch(async (url) => {
      if (url.includes("/projects/p1/")) {
        const body = await p1Body;
        return jsonResponse(body);
      }
      return jsonResponse({
        artifacts: [row({ id: "from-p2" })],
        next_cursor: null,
        has_more: false,
      });
    });
    const first = browseFiles({ reset: true });
    project.value = "p2";
    const second = browseFiles({ reset: true });
    resolveP1!({ artifacts: [row({ id: "from-p1" })], next_cursor: null, has_more: false });
    await first;
    await second;
    expect(filesIndexItems.value.map((a) => a.id)).toEqual(["from-p2"]);
    expect(projectArtifacts.value as ArtifactRow[]).toEqual(filesIndexItems.value);
  });

  it("falls back to the array route with client paging when index is 404", async () => {
    project.value = "p1";
    filesScope.value = "project";
    const all = make500();
    setArtifactsFetch(async (url) => {
      if (url.includes("artifact-index")) return jsonResponse({ error: "missing" }, 404);
      expect(url).toContain("/projects/p1/artifacts");
      expect(url).not.toContain("artifact-index");
      return jsonResponse(all);
    });
    await browseFiles({ reset: true });
    expect(filesIndexMode.value).toBe("fallback");
    expect(filesIndexItems.value).toHaveLength(FILES_PAGE_SIZE);
    expect(filesHasMore.value).toBe(true);
    expect(filesIndexError.value).toBeTruthy();
    await browseFiles({ loadMore: true });
    expect(filesIndexItems.value.length).toBe(FILES_PAGE_SIZE * 2);
  });

  it("frame scope filters the session array locally", async () => {
    filesScope.value = "frame";
    artifactsSignal.value = [
      row({ id: "a", filename: "keep.csv" }),
      row({ id: "b", filename: "skip.png" }),
    ];
    const { filesQuery } = await import("./state");
    filesQuery.value = "keep";
    await browseFiles({ reset: true });
    expect(filesIndexItems.value.map((a) => a.id)).toEqual(["a"]);
    expect(filesHasMore.value).toBe(false);
  });

  it("bumps the request token on resetFilesIndexState", () => {
    const n = filesIndexReq.value;
    resetFilesIndexState();
    expect(filesIndexReq.value).toBeGreaterThan(n);
    expect(filesIndexItems.value).toEqual([]);
  });
});
