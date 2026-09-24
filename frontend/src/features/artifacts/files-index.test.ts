import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { artifacts as artifactsSignal, artifactsFrameId, filesScope, projectArtifacts } from "../../stores/artifacts";
import { _openGen, currentId, project } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { setArtifactsFetch } from "./api";
import {
  browseFiles,
  filterArtifactsClient,
  filesGridArtifacts,
  filesGridPending,
  filesReadFailed,
  setFilesContentType,
  setFilesOrigin,
  visibleArtifacts,
  setFilesQuery,
} from "./files-index";
import { jsonResponse } from "./http-stub";
import { loadArtifacts } from "./load";
import { upsertArtifactFromEvent } from "../ws/handlers";
import { artifactCreatedSideEffects } from "./events";
import {
  filesCursorFilter,
  filesHasMore,
  filesIndexError,
  filesIndexItems,
  filesIndexLoading,
  filesLoadedLimit,
  filesIndexMode,
  filesIndexReq,
  filesNextCursor,
  filesQuery,
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

describe("M-03 Files index (artifact-index, no array fallback)", () => {
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
      expect(url).not.toContain("/projects/p1/artifacts?");
      expect(url.endsWith("/projects/p1/artifacts")).toBe(false);
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
    expect(filesNextCursor.value).toBe("c");
    setFilesQuery("report");
    expect(filesNextCursor.value).toBeNull();
    expect(filesCursorFilter.value).toBeNull();
    await browseFiles({ reset: true });
    expect(seen[1]).not.toContain("stale-cursor");
    expect(seen[1]).not.toContain("cursor=");
    expect(seen[1]).toContain("q=report");
  });

  it("load-more after a filter change does not reuse the old cursor even without reset", async () => {
    project.value = "p1";
    filesScope.value = "project";
    const seen: string[] = [];
    setArtifactsFetch(async (url) => {
      seen.push(url);
      return jsonResponse({ artifacts: [row({ id: "x" })], next_cursor: "keep-me", has_more: true });
    });
    await browseFiles({ reset: true });
    expect(filesNextCursor.value).toBe("keep-me");
    filesQuery.value = "plot";
    await browseFiles({ loadMore: true });
    const last = seen[seen.length - 1] || "";
    expect(last).not.toContain("cursor=");
    expect(last).toContain("q=plot");
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

  it("drops a late response after the filter changes", async () => {
    filesScope.value = "project";
    project.value = "p1";
    let resolveFirst: ((body: unknown) => void) | undefined;
    const firstBody = new Promise<unknown>((resolve) => {
      resolveFirst = resolve;
    });
    setArtifactsFetch(async (url) => {
      if (url.includes("q=keep")) {
        return jsonResponse({
          artifacts: [row({ id: "kept" })],
          next_cursor: null,
          has_more: false,
        });
      }
      const body = await firstBody;
      return jsonResponse(body);
    });
    const first = browseFiles({ reset: true });
    setFilesQuery("keep");
    const second = browseFiles({ reset: true });
    resolveFirst!({ artifacts: [row({ id: "stale" })], next_cursor: null, has_more: false });
    await first;
    await second;
    expect(filesIndexItems.value.map((a) => a.id)).toEqual(["kept"]);
  });

  it("invalid_cursor retries without the old cursor", async () => {
    project.value = "p1";
    filesScope.value = "project";
    const seen: string[] = [];
    setArtifactsFetch(async (url) => {
      seen.push(url);
      if (url.includes("cursor=")) {
        return jsonResponse({ error: "invalid cursor", code: "invalid_cursor" }, 400);
      }
      return jsonResponse({
        artifacts: [row({ id: "fresh" })],
        next_cursor: null,
        has_more: false,
      });
    });
    await browseFiles({ reset: true });
    filesNextCursor.value = "dead-cursor";
    await browseFiles({ loadMore: true });
    expect(seen.some((u) => u.includes("cursor=dead-cursor"))).toBe(true);
    expect(seen[seen.length - 1]).not.toContain("cursor=");
    expect(filesIndexItems.value.map((a) => a.id)).toEqual(["fresh"]);
    expect(filesIndexMode.value).toBe("index");
  });

  it("does not fall back to GET /projects/{pid}/artifacts when the index errors", async () => {
    project.value = "p1";
    filesScope.value = "project";
    const seen: string[] = [];
    setArtifactsFetch(async (url) => {
      seen.push(url);
      if (url.includes("artifact-index")) return jsonResponse({ error: "missing" }, 404);
      return jsonResponse([row({ id: "from-array" })]);
    });
    await browseFiles({ reset: true });
    expect(seen.every((u) => u.includes("artifact-index"))).toBe(true);
    expect(seen.some((u) => /\/projects\/p1\/artifacts(?:\?|$)/.test(u) && !u.includes("artifact-index"))).toBe(
      false,
    );
    expect(filesIndexMode.value).toBe("error");
    expect(filesIndexItems.value).toEqual([]);
    expect(filesIndexError.value).toBeTruthy();
  });

  it("frame scope filters the session array locally", async () => {
    filesScope.value = "frame";
    currentId.value = "frame-a";
    artifactsFrameId.value = currentId.value;
    artifactsSignal.value = [
      row({ id: "a", filename: "keep.csv" }),
      row({ id: "b", filename: "skip.png" }),
    ];
    filesQuery.value = "keep";
    await browseFiles({ reset: true });
    expect(filesIndexItems.value.map((a) => a.id)).toEqual(["a"]);
    expect(filesHasMore.value).toBe(false);
  });

  it("paints 125 frame artifacts as 50, 50, 25 without changing conversation visibility", async () => {
    currentId.value = "frame-a";
    artifactsFrameId.value = currentId.value;
    artifactsSignal.value = make500().slice(0, 125);
    await browseFiles({ reset: true });
    expect(filesGridArtifacts()).toHaveLength(50);
    expect(visibleArtifacts()).toHaveLength(125);
    expect(filesNextCursor.value).toBe("50");
    await browseFiles({ loadMore: true });
    expect(filesGridArtifacts()).toHaveLength(100);
    expect(filesNextCursor.value).toBe("100");
    await browseFiles({ loadMore: true });
    expect(filesGridArtifacts()).toHaveLength(125);
    expect(new Set(filesGridArtifacts().map((a) => a.id)).size).toBe(125);
    expect(filesHasMore.value).toBe(false);
    expect(filesNextCursor.value).toBeNull();
  });

  it("populates the first filtered page when artifacts initially load with the dock closed", async () => {
    currentId.value = "frame-a";
    setFilesQuery("report");
    setFilesContentType("text/csv");
    setFilesOrigin("uploaded");
    setArtifactsFetch(async () => jsonResponse(make500().slice(0, 125)));
    await loadArtifacts("frame-a");
    expect(filesIndexItems.value).toHaveLength(13);
    expect(filesGridArtifacts().every((a) => a.filename === "report.csv" && a.is_user_upload)).toBe(true);
    expect(filesHasMore.value).toBe(false);
  });

  it("refreshes the loaded frame pages from current rows without duplicate IDs", async () => {
    currentId.value = "frame-a";
    let rows = make500().slice(0, 125);
    setArtifactsFetch(async () => jsonResponse(rows));
    await loadArtifacts("frame-a");
    await browseFiles({ reset: true });
    await browseFiles({ loadMore: true });
    rows = [row({ id: "new", priority: 2 }), ...rows];
    await loadArtifacts("frame-a");
    expect(filesGridArtifacts()).toHaveLength(100);
    expect(filesGridArtifacts()[0]?.id).toBe("new");
    expect(new Set(filesGridArtifacts().map((a) => a.id)).size).toBe(100);
    await browseFiles({ loadMore: true });
    expect(filesGridArtifacts()).toHaveLength(126);
    expect(new Set(filesGridArtifacts().map((a) => a.id)).size).toBe(126);
  });

  it("retains loaded page capacity when the last page used to be partial", async () => {
    currentId.value = "frame-a";
    let rows = make500().slice(0, 125);
    setArtifactsFetch(async () => jsonResponse(rows));
    await loadArtifacts("frame-a");
    await browseFiles({ reset: true });
    await browseFiles({ loadMore: true });
    await browseFiles({ loadMore: true });
    rows = [row({ id: "new", priority: 2 }), ...rows];
    await loadArtifacts("frame-a");
    expect(filesIndexItems.value).toHaveLength(126);
    expect(filesHasMore.value).toBe(false);
  });

  it("cannot paint the previous session or append from its cursor while the new read is pending", async () => {
    currentId.value = "frame-a";
    artifactsFrameId.value = currentId.value;
    artifactsSignal.value = make500().slice(0, 125);
    await browseFiles({ reset: true });
    await browseFiles({ loadMore: true });
    currentId.value = "frame-b";
    expect(filesGridArtifacts()).toEqual([]);
    artifactsFrameId.value = "frame-b";
    artifactsSignal.value = make500().slice(0, 80).map((a) => ({ ...a, id: "b-" + a.id }));
    await browseFiles({ loadMore: true });
    expect(filesGridArtifacts()).toHaveLength(50);
    expect(filesNextCursor.value).toBe("50");
    expect(filesGridArtifacts().every((a) => a.id.startsWith("b-"))).toBe(true);
  });

  it("cannot rebrand the old array while the new session read is pending", async () => {
    currentId.value = "a";
    artifactsFrameId.value = "a";
    artifactsSignal.value = make500().slice(0, 125);
    await browseFiles({ reset: true });
    let release!: (rows: ArtifactRow[]) => void;
    const pending = new Promise<ArtifactRow[]>((resolve) => { release = resolve; });
    setArtifactsFetch(async () => jsonResponse(await pending));
    currentId.value = "b";
    const loading = loadArtifacts("b");
    setFilesQuery("report");
    await browseFiles({ reset: true });
    await browseFiles({ loadMore: true });
    expect(filesGridArtifacts()).toEqual([]);
    expect(filesHasMore.value).toBe(false);
    expect(filesNextCursor.value).toBeNull();
    release([row({ id: "b-file", filename: "report.csv" })]);
    await loading;
    expect(filesGridArtifacts().map((a) => a.id)).toEqual(["b-file"]);
  });

  it("rejects a late old visit to the same session and does not relabel its snapshot", async () => {
    currentId.value = "a";
    artifactsFrameId.value = "a";
    artifactsSignal.value = [row({ id: "old" })];
    await browseFiles({ reset: true });
    let release!: () => void;
    const pending = new Promise<void>((resolve) => { release = resolve; });
    setArtifactsFetch(async () => { await pending; return jsonResponse([row({ id: "late-old" })]); });
    const old = loadArtifacts("a");
    currentId.value = "b"; _openGen.value += 1;
    currentId.value = "a"; _openGen.value += 1;
    setFilesQuery("old");
    await browseFiles({ reset: true });
    expect(filesGridArtifacts()).toEqual([]);
    release(); await old;
    expect(filesGridArtifacts()).toEqual([]);
    expect(artifactsSignal.value).toEqual([row({ id: "old" })]);
  });

  it("recomputes in-place events, rejects the old root, and accepts a current-root child", async () => {
    currentId.value = "b";
    artifactsFrameId.value = "b";
    artifactsSignal.value = make500().slice(0, 125);
    await browseFiles({ reset: true });
    await browseFiles({ loadMore: true });
    const emit = (event: Parameters<typeof upsertArtifactFromEvent>[0]) => {
      upsertArtifactFromEvent(event);
      artifactCreatedSideEffects(event);
    };
    emit({ type: "artifact_created", root_frame_id: "a", artifact: row({ id: "foreign", priority: 10 }) });
    expect(filesGridArtifacts().some((a) => a.id === "foreign")).toBe(false);
    const child = { type: "artifact_created", root_frame_id: "b", frame_id: "child", artifact: row({ id: "child-file", priority: 2 }) };
    emit(child); emit(child);
    expect(filesGridArtifacts()).toHaveLength(100);
    expect(filesGridArtifacts()[0]?.id).toBe("child-file");
    expect(new Set(filesGridArtifacts().map((a) => a.id)).size).toBe(100);
    emit({ ...child, artifact: row({ id: "child-file", priority: -1 }) });
    expect(filesGridArtifacts()).toHaveLength(100);
    expect(filesGridArtifacts().some((a) => a.id === "child-file")).toBe(false);
  });

  it("keeps three-page capacity after shrinking and growing the snapshot", async () => {
    currentId.value = "a";
    let rows = make500().slice(0, 125);
    setArtifactsFetch(async () => jsonResponse(rows));
    await loadArtifacts("a");
    await browseFiles({ loadMore: true });
    await browseFiles({ loadMore: true });
    rows = rows.slice(0, 20);
    await loadArtifacts("a");
    expect(filesGridArtifacts()).toHaveLength(20);
    rows = make500().slice(0, 160);
    await loadArtifacts("a");
    expect(filesGridArtifacts()).toHaveLength(150);
    expect(filesNextCursor.value).toBe("150");
    expect(filesHasMore.value).toBe(true);
  });

  it("clears card and cursor eligibility after the last session is removed", async () => {
    currentId.value = "a";
    artifactsFrameId.value = "a";
    artifactsSignal.value = make500().slice(0, 125);
    await browseFiles({ reset: true });
    currentId.value = null;
    artifactsSignal.value = [];
    expect(filesGridArtifacts()).toEqual([]);
    await browseFiles({ loadMore: true });
    expect(filesHasMore.value).toBe(false);
    expect(filesNextCursor.value).toBeNull();
  });

  it("origin setter drops the cursor", () => {
    filesNextCursor.value = "c";
    filesCursorFilter.value = "old";
    setFilesOrigin("uploaded");
    expect(filesNextCursor.value).toBeNull();
    expect(filesCursorFilter.value).toBeNull();
  });

  it("bumps the request token on resetFilesIndexState", () => {
    const n = filesIndexReq.value;
    resetFilesIndexState();
    expect(filesIndexReq.value).toBeGreaterThan(n);
    expect(filesIndexItems.value).toEqual([]);
  });
});


describe("session artifact read failures (AUDIT A26)", () => {
  beforeEach(() => { resetStoreFields(); resetFilesIndexState(); setArtifactsFetch(null); });
  afterEach(() => setArtifactsFetch(null));
  const refuse = async () => jsonResponse({ error: "daemon restarting" }, 503);

  it("keeps the confirmed list through a failed refresh and reports the read", async () => {
    currentId.value = "a";
    const rows = make500().slice(0, 3);
    setArtifactsFetch(async () => jsonResponse(rows));
    await loadArtifacts("a");
    expect(filesGridArtifacts().map((x) => x.id)).toEqual(rows.map((x) => x.id).sort().reverse());
    setArtifactsFetch(refuse);
    await loadArtifacts("a");
    expect(artifactsSignal.value).toEqual(rows);
    expect(filesGridArtifacts()).toHaveLength(3);
    expect(filesReadFailed()).toBe(true);
    setArtifactsFetch(async () => jsonResponse(rows));
    await loadArtifacts("a");
    expect(filesReadFailed()).toBe(false);
  });

  it("reports a failed first read instead of an empty session, without the previous session's rows", async () => {
    currentId.value = "a";
    setArtifactsFetch(async () => jsonResponse(make500().slice(0, 3)));
    await loadArtifacts("a");
    currentId.value = "b";
    setArtifactsFetch(refuse);
    await loadArtifacts("b");
    expect(filesReadFailed()).toBe(true);
    expect(filesGridArtifacts()).toEqual([]);
    expect(artifactsSignal.value).toEqual([]);
    expect(artifactsFrameId.value).toBe("b");
  });
});


describe("Files grid pending state (AUDIT A63)", () => {
  beforeEach(() => { resetStoreFields(); resetFilesIndexState(); setArtifactsFetch(null); });
  afterEach(() => setArtifactsFetch(null));

  it("is pending while the first project index read is in flight, not empty", async () => {
    filesScope.value = "project";
    project.value = "p";
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    setArtifactsFetch(async () => { await held; return jsonResponse({ artifacts: [], next_cursor: null, has_more: false }); });
    const reading = browseFiles({ reset: true });
    expect(filesGridPending()).toBe(true);
    release();
    await reading;
    expect(filesGridPending()).toBe(false);
    expect(filesGridArtifacts()).toEqual([]);
  });

  it("is pending after a filter change until the listing is browsed", async () => {
    currentId.value = "a";
    setArtifactsFetch(async () => jsonResponse([row({ id: "1" })]));
    await loadArtifacts("a");
    expect(filesGridPending()).toBe(false);
    setFilesQuery("zzz");
    expect(filesGridPending()).toBe(true);
    await browseFiles({ reset: true });
    expect(filesGridPending()).toBe(false);
  });

  it("is pending until the open session's list arrives, and not after a failed read", async () => {
    currentId.value = "a";
    await browseFiles({ reset: true });
    expect(filesGridPending()).toBe(true);
    setArtifactsFetch(async () => jsonResponse({ error: "down" }, 503));
    await loadArtifacts("a");
    expect(filesGridPending()).toBe(false);
    expect(filesReadFailed()).toBe(true);
  });
});


describe("project refresh capacity and ownership", () => {
  beforeEach(() => { resetStoreFields(); resetFilesIndexState(); filesScope.value = "project"; project.value = "p"; });
  afterEach(() => setArtifactsFetch(null));

  it("preserves requested capacity through 125 → 20 → 160 and uses only index pages", async () => {
    let rows = make500().slice(0, 125);
    const reads: number[] = [];
    setArtifactsFetch(async (url) => {
      expect(url).toContain("/projects/p/artifact-index?");
      const params = new URL(url, "https://fixture.invalid").searchParams;
      const start = Number(params.get("cursor") || 0);
      const limit = Number(params.get("limit"));
      reads.push(start);
      const end = Math.min(start + limit, rows.length);
      return jsonResponse({ artifacts: rows.slice(start, end), next_cursor: end < rows.length ? String(end) : null, has_more: end < rows.length });
    });
    await browseFiles({ reset: true });
    await browseFiles({ loadMore: true });
    await browseFiles({ loadMore: true });
    expect(filesGridArtifacts()).toHaveLength(125);
    rows = make500().slice(0, 20);
    await browseFiles({ refresh: true });
    expect(filesGridArtifacts()).toHaveLength(20);
    expect(filesLoadedLimit.value).toBe(150);
    rows = make500().slice(0, 160); reads.length = 0;
    await browseFiles({ refresh: true });
    expect(filesGridArtifacts()).toHaveLength(150);
    expect(reads).toEqual([0, 50, 100]);
    expect(filesHasMore.value).toBe(true);
  });

  it.each(["filter", "frame"])("rejects the old second refresh page after a %s change", async (change) => {
    let release!: () => void;
    let started!: () => void;
    const waiting = new Promise<void>((resolve) => { started = resolve; });
    const gate = new Promise<void>((resolve) => { release = resolve; });
    filesLoadedLimit.value = 150;
    // Establish the fingerprint without depending on a magic encoded identity.
    setArtifactsFetch(async () => jsonResponse({ artifacts: [row({ id: "initial" })], next_cursor: "50", has_more: true }));
    await browseFiles({ reset: true }); filesLoadedLimit.value = 150;
    setArtifactsFetch(async (url) => {
      if (url.includes("q=keep")) return jsonResponse({ artifacts: [row({ id: "keep" })], next_cursor: null, has_more: false });
      if (url.includes("cursor=")) { started(); await gate; }
      return jsonResponse({ artifacts: make500().slice(url.includes("cursor=") ? 50 : 0, url.includes("cursor=") ? 100 : 50), next_cursor: "50", has_more: true });
    });
    const old = browseFiles({ refresh: true }); await waiting;
    if (change === "filter") setFilesQuery("keep");
    else { filesScope.value = "frame"; currentId.value = "f"; artifactsFrameId.value = "f"; artifactsSignal.value = [row({ id: "keep" })]; }
    await browseFiles({ reset: true }); release(); await old;
    expect(filesGridArtifacts().map((a) => a.id)).toEqual(["keep"]);
    expect(filesIndexLoading.value).toBe(false);
    expect(filesIndexError.value).toBeNull();
  });

  it.each(["repeated cursor", "zero progress"])("fails finitely on %s during refresh", async (fault) => {
    let reads = 0;
    setArtifactsFetch(async () => jsonResponse({ artifacts: [row({ id: "initial" })], next_cursor: null, has_more: false }));
    await browseFiles({ reset: true }); filesLoadedLimit.value = 150;
    setArtifactsFetch(async () => {
      reads += 1;
      return jsonResponse({ artifacts: fault === "zero progress" ? [] : [row({ id: String(reads) })], next_cursor: "same", has_more: true });
    });
    await browseFiles({ refresh: true });
    expect(reads).toBe(fault === "zero progress" ? 1 : 2);
    expect(filesIndexMode.value).toBe("error");
    expect(filesGridArtifacts()).toEqual([]);
    expect(filesHasMore.value).toBe(false);
    expect(filesIndexLoading.value).toBe(false);
  });
});
