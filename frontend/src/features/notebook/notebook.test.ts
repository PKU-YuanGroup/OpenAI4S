import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const highlightCalls = vi.hoisted(() => vi.fn());

vi.mock("../md/highlight", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../md/highlight")>();
  return {
    ...actual,
    mdHighlight: (code: string, lang?: string) => {
      highlightCalls(code, lang);
      return actual.mdHighlight(code, lang);
    },
  };
});

import {
  _kc,
  _liveCell,
  _nbDirty,
  _nbReading,
  _nbSched,
  cells,
  liveCells,
} from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { running } from "../../stores/stream";
import { activeTab, dock } from "../../stores/ui";
import { LIVE_OUTPUT_CHAR_CAP, LIVE_OUTPUT_TRUNCATION } from "../stream/cap";
import { registerBuiltinHandlers, setArtifactCreatedSideEffects } from "../ws/handlers";
import { onEvent, resetWsHandlers } from "../ws/registry";
import {
  appendTextNodeDelta,
  cellOutput,
  mergeNotebookCells,
  nbCellChunk,
  nbCellDraft,
  nbCellFinished,
  nbCellKey,
  nbCellStart,
  nbFindCell,
  projectNotebookCells,
  resetCellOutputs,
  setNotebookApi,
} from "./cells";
import {
  highlightCellSource,
  highlightTraceback,
  mountLiveNotebookFigure,
  notebookExportHref,
  NOTEBOOK_EXPORTS,
  resetHighlightMemo,
  resetNotebookCellCaches,
} from "./chrome";
import { installNotebook } from "./install";
import { invalidateKernelCache, kernelEpoch, nbSwitchEnv, notebookOnTurnDone } from "./kernel";
import {
  isNearBottom,
  measureNotebookFollow,
  nbRender,
  onNotebookScroll,
  setNotebookRenderImpl,
} from "./scroll";

function flushRaf(queue: Array<(t: number) => void>): void {
  const q = queue.splice(0);
  for (const cb of q) cb(0);
}

describe("F-14 Notebook", () => {
  const rafs: Array<(t: number) => void> = [];
  let paints = 0;

  beforeEach(() => {
    resetStoreFields();
    resetWsHandlers();
    resetCellOutputs();
    resetHighlightMemo();
    highlightCalls.mockClear();
    setNotebookApi(null);
    setArtifactCreatedSideEffects(null);
    rafs.length = 0;
    paints = 0;
    vi.stubGlobal("requestAnimationFrame", (cb: (t: number) => void) => {
      rafs.push(cb);
      return rafs.length;
    });
    setNotebookRenderImpl(() => {
      paints++;
    });
    dock.value = { open: true, tab: "notebook" };
    activeTab.value = "notebook";
    currentId.value = "frame-1";
    installNotebook({});
    registerBuiltinHandlers();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setNotebookRenderImpl(null);
    setNotebookApi(null);
    setArtifactCreatedSideEffects(null);
    resetWsHandlers();
  });

  describe("mergeNotebookCells", () => {
    it("drops prior-frame output and highlight identities but reuses current-frame entries", () => {
      const oldOutput = cellOutput("shared-cell-id");
      oldOutput.stdout.value = "old frame output";
      const oldHtml = highlightCellSource("shared-cell-id", "print(1)", "python");

      expect(cellOutput("shared-cell-id")).toBe(oldOutput);
      expect(highlightCellSource("shared-cell-id", "print(1)", "python")).toBe(oldHtml);
      expect(highlightCalls).toHaveBeenCalledTimes(1);

      resetNotebookCellCaches("frame-1", "frame-1");
      expect(cellOutput("shared-cell-id")).toBe(oldOutput);
      expect(highlightCellSource("shared-cell-id", "print(1)", "python")).toBe(oldHtml);
      expect(highlightCalls).toHaveBeenCalledTimes(1);

      resetNotebookCellCaches("frame-1", "frame-2");

      const currentOutput = cellOutput("shared-cell-id");
      expect(currentOutput).not.toBe(oldOutput);
      expect(currentOutput.stdout.value).toBe("");
      const currentHtml = highlightCellSource("shared-cell-id", "print(1)", "python");
      expect(currentHtml).toBe(oldHtml);
      expect(highlightCalls).toHaveBeenCalledTimes(2);
      expect(cellOutput("shared-cell-id")).toBe(currentOutput);
      expect(highlightCellSource("shared-cell-id", "print(1)", "python")).toBe(currentHtml);
      expect(highlightCalls).toHaveBeenCalledTimes(2);
    });

    it("keys by producing_cell_id and lets the server record win", () => {
      const local = [
        { producing_cell_id: "c1", cell_index: 1, source: "local", stdout: "old" },
        { producing_cell_id: "c2", cell_index: 2, source: "keep" },
      ];
      const server = [{ producing_cell_id: "c1", cell_index: 1, source: "server", stdout: "new" }];
      const merged = mergeNotebookCells(server, local);
      expect(merged).toHaveLength(2);
      expect(merged[0] && merged[0].source).toBe("server");
      expect(merged[0] && merged[0].stdout).toBe("new");
      expect(merged[1] && merged[1].producing_cell_id).toBe("c2");
    });

    it("sorts by cell_index then by key", () => {
      const merged = mergeNotebookCells(
        [
          { producing_cell_id: "b", cell_index: 2 },
          { producing_cell_id: "a", cell_index: 1 },
        ],
        [{ producing_cell_id: "c", cell_index: 1 }],
      );
      expect(merged.map(nbCellKey)).toEqual(["a", "c", "b"]);
    });

    it("falls back to legacy:kernel:index when no cell id", () => {
      const cell = { kernel_id: "r", cell_index: 3, source: "x" };
      expect(nbCellKey(cell)).toBe("legacy:r:3");
    });
  });

  describe("_seenChunks replay dedup (app.js:9851-9853)", () => {
    function start(id: string): void {
      nbCellStart({
        type: "notebook_cell_start",
        producing_cell_id: id,
        cell_id: id,
        cell_index: 1,
        kernel_id: "python",
        language: "python",
      });
      flushRaf(rafs);
    }

    it("ignores a replayed chunk_id on the same stream", () => {
      start("c1");
      nbCellChunk({
        producing_cell_id: "c1",
        stream: "stdout",
        chunk_id: 1,
        chunk: "hello",
      });
      nbCellChunk({
        producing_cell_id: "c1",
        stream: "stdout",
        chunk_id: 1,
        chunk: "hello",
      });
      const cell = nbFindCell("c1");
      expect(cell && cell.stdout).toBe("hello");
      expect(cellOutput("c1").stdout.value).toBe("hello");
    });

    it("treats the same chunk_id on stderr as a different key", () => {
      start("c1");
      nbCellChunk({ producing_cell_id: "c1", stream: "stdout", chunk_id: 1, chunk: "out" });
      nbCellChunk({ producing_cell_id: "c1", stream: "stderr", chunk_id: 1, chunk: "err" });
      const cell = nbFindCell("c1");
      expect(cell && cell.stdout).toBe("out");
      expect(cell && cell.stderr).toBe("err");
    });

    it("accepts chunk_id 0 (nullish check, not truthy)", () => {
      start("c1");
      nbCellChunk({ producing_cell_id: "c1", stream: "stdout", chunk_id: 0, chunk: "zero" });
      nbCellChunk({ producing_cell_id: "c1", stream: "stdout", chunk_id: 0, chunk: "again" });
      expect(nbFindCell("c1") && nbFindCell("c1")!.stdout).toBe("zero");
    });

    it("falls back to sequence when chunk_id is absent", () => {
      start("c1");
      nbCellChunk({ producing_cell_id: "c1", stream: "stdout", sequence: 4, chunk: "a" });
      nbCellChunk({ producing_cell_id: "c1", stream: "stdout", sequence: 4, chunk: "a" });
      expect(nbFindCell("c1") && nbFindCell("c1")!.stdout).toBe("a");
    });

    it("updates only the matching cell's output signal", () => {
      start("c1");
      start("c2");
      nbCellChunk({ producing_cell_id: "c1", chunk_id: 1, chunk: "one" });
      expect(cellOutput("c1").stdout.value).toBe("one");
      expect(cellOutput("c2").stdout.value).toBe("");
      expect(nbFindCell("c2") && nbFindCell("c2")!.stdout).toBe("");
    });

    it("does not schedule a pane rebuild on chunk", () => {
      start("c1");
      paints = 0;
      nbCellChunk({ producing_cell_id: "c1", chunk_id: 1, chunk: "x" });
      flushRaf(rafs);
      expect(paints).toBe(0);
    });

    it("caps live output at 1MB", () => {
      start("c1");
      nbCellChunk({
        producing_cell_id: "c1",
        chunk_id: 1,
        chunk: "x".repeat(LIVE_OUTPUT_CHAR_CAP + 50),
      });
      const out = nbFindCell("c1")!.stdout || "";
      expect(out.endsWith(LIVE_OUTPUT_TRUNCATION)).toBe(true);
      expect(out.length).toBe(LIVE_OUTPUT_CHAR_CAP + LIVE_OUTPUT_TRUNCATION.length);
    });
  });

  describe("start / finished / draft", () => {
    it("does not inherit stdout from a finished cell on replay start", () => {
      cells.value = [
        {
          producing_cell_id: "c1",
          cell_id: "c1",
          stdout: "old-complete",
          status: "ok",
          live: false,
        },
      ];
      nbCellStart({
        producing_cell_id: "c1",
        cell_id: "c1",
        cell_index: 1,
      });
      const cell = nbFindCell("c1");
      expect(cell && cell.stdout).toBe("");
      expect(cell && cell.live).toBe(true);
      expect(cell && cell._seenChunks).toBeUndefined();
      expect(asSavedHas("c1")).toBe(false);
    });

    it("inherits stdout and _seenChunks from an already-running live cell", () => {
      nbCellStart({ producing_cell_id: "c1", cell_id: "c1" });
      nbCellChunk({ producing_cell_id: "c1", chunk_id: 1, chunk: "keep" });
      const seen = nbFindCell("c1")!._seenChunks;
      nbCellStart({ producing_cell_id: "c1", cell_id: "c1" });
      const cell = nbFindCell("c1");
      expect(cell && cell.stdout).toBe("keep");
      expect(cell && cell._seenChunks).toBe(seen);
    });

    it("moves a finished cell out of liveCells into cells", () => {
      nbCellStart({ producing_cell_id: "c1", cell_id: "c1", cell_index: 2 });
      nbCellFinished({
        producing_cell_id: "c1",
        cell_id: "c1",
        stdout: "done",
        status: "ok",
      });
      expect(asLiveHas("c1")).toBe(false);
      expect(asSavedHas("c1")).toBe(true);
      expect(nbFindCell("c1") && nbFindCell("c1")!.live).toBe(false);
      expect(nbFindCell("c1") && nbFindCell("c1")!.stdout).toBe("done");
    });

    it("ignores a stale draft revision", () => {
      nbCellDraft({ draft_id: "d1", revision: 2, source: "v2" });
      nbCellDraft({ draft_id: "d1", revision: 1, source: "v1" });
      expect(nbFindCell("d1") && nbFindCell("d1")!.source).toBe("v2");
    });

    it("discards a draft", () => {
      nbCellDraft({ draft_id: "d1", revision: 1, source: "x" });
      nbCellDraft({ draft_id: "d1", revision: 2, status: "discarded" });
      expect(nbFindCell("d1")).toBeNull();
      expect(_liveCell.value).toBeNull();
    });
  });

  describe("_kc invalidate timings", () => {
    function seedCache(): void {
      const kc = _kc.value;
      kc.id = "frame-1";
      kc.st = { alive: true, state: "running" };
      kc.stAt = 99;
      kc.stBusy = true;
      kc.envs = [{ name: "python" }];
      kc.cur = "python";
      kc.envAt = 77;
      kc.envBusy = true;
    }

    function expectInvalidated(): void {
      const kc = _kc.value;
      expect(kc.id).toBeNull();
      expect(kc.st).toBeNull();
      expect(kc.stAt).toBe(0);
      expect(kc.envs).toBeNull();
      expect(kc.cur).toBeNull();
      expect(kc.envAt).toBe(0);
      expect(kc.stBusy).toBe(true);
      expect(kc.envBusy).toBe(true);
    }

    it("clears id/st/envs and leaves busy flags (app.js:9955)", () => {
      seedCache();
      const epoch = kernelEpoch.value;
      invalidateKernelCache();
      expectInvalidated();
      expect(kernelEpoch.value).toBe(epoch + 1);
    });

    it("invalidates on kernel_status for the open session", () => {
      seedCache();
      onEvent({ type: "kernel_status", frame_id: "frame-1", status: "restarted", generation: 2 });
      expectInvalidated();
    });

    it("does not invalidate kernel_status for another session", () => {
      seedCache();
      onEvent({ type: "kernel_status", frame_id: "other", status: "stopped" });
      expect(_kc.value.id).toBe("frame-1");
      expect(_kc.value.st).toEqual({ alive: true, state: "running" });
    });

    it("invalidates on notebookOnTurnDone (F-11 turnDone hook, app.js:5854)", () => {
      seedCache();
      notebookOnTurnDone();
      expectInvalidated();
    });

    it("invalidates on nbSwitchEnv (app.js:10060)", async () => {
      seedCache();
      setNotebookApi(async () => ({ ok: true }));
      await nbSwitchEnv("science");
      expectInvalidated();
    });
  });

  describe("scroll follow + reading delay (app.js:10339-10350, 9900-9908)", () => {
    it("treats <120px from the bottom as following", () => {
      expect(isNearBottom({ scrollHeight: 1000, scrollTop: 890, clientHeight: 100 })).toBe(true);
      expect(isNearBottom({ scrollHeight: 1000, scrollTop: 700, clientHeight: 100 })).toBe(false);
      expect(measureNotebookFollow(null)).toBe(true);
    });

    it("marks dirty and skips paint while running and reading", () => {
      running.value = true;
      _nbReading.value = true;
      paints = 0;
      nbRender();
      expect(_nbDirty.value).toBe(true);
      expect(_nbSched.value).toBe(false);
      flushRaf(rafs);
      expect(paints).toBe(0);
    });

    it("flushes the deferred render when the user returns to the bottom", () => {
      running.value = true;
      _nbReading.value = true;
      _nbDirty.value = true;
      const body = { scrollHeight: 500, scrollTop: 400, clientHeight: 100 };
      onNotebookScroll(body);
      expect(_nbReading.value).toBe(false);
      expect(_nbDirty.value).toBe(false);
      expect(_nbSched.value).toBe(true);
      flushRaf(rafs);
      expect(paints).toBe(1);
    });

    it("sets _nbReading when scrolled up", () => {
      const body = { scrollHeight: 500, scrollTop: 0, clientHeight: 100 };
      onNotebookScroll(body);
      expect(_nbReading.value).toBe(true);
    });
  });

  describe("window contract + traceback", () => {
    it("assigns highlightTraceback and notebookExportLink on the install target", () => {
      const target: Record<string, unknown> = {};
      installNotebook(target);
      expect(target.highlightTraceback).toBe(highlightTraceback);
      expect(typeof target.notebookExportLink).toBe("function");
    });

    it("escapes XSS samples in highlightTraceback", () => {
      const html = highlightTraceback(
        'File "<img src=x onerror=\\"window.__xssProbe()\\">", line 1\n' +
          "ValueError: <script>window.__xssProbe()</script>",
      );
      expect(html).not.toMatch(/<script/i);
      expect(html).not.toMatch(/<img/i);
      expect(html).toContain("&lt;");
      expect(html).toContain("tb-loc");
      expect(html).toContain("tb-final");
    });

    it("builds the sources.zip export on the execution-sources route", () => {
      const sources = NOTEBOOK_EXPORTS.find((o) => o.suffix === "sources.zip");
      expect(sources).toBeTruthy();
      expect(notebookExportHref("f-exec-root", sources!)).toBe(
        "/api/v1/frames/f-exec-root/execution-sources/export",
      );
    });
  });

  describe("live figure mount + highlight cache", () => {
    it("pushes an image onto the producing live cell while running", () => {
      running.value = true;
      nbCellStart({ producing_cell_id: "c1", cell_id: "c1" });
      mountLiveNotebookFigure({
        type: "artifact_created",
        artifact: {
          filename: "plot.png",
          content_type: "image/png",
          producing_cell_id: "c1",
        },
      });
      expect(nbFindCell("c1") && nbFindCell("c1")!.figures).toEqual(["plot.png"]);
      expect(cellOutput("c1").figures.value).toEqual(["plot.png"]);
    });

    it("returns the same highlight HTML when source is unchanged", () => {
      const a = highlightCellSource("c1", "print(1)", "python");
      const b = highlightCellSource("c1", "print(1)", "python");
      expect(a).toBe(b);
      const c = highlightCellSource("c1", "print(2)", "python");
      expect(c).not.toBe(a);
    });
  });

  describe("appendTextNodeDelta", () => {
    it("appends only the new suffix", () => {
      const node = {
        data: "hel",
        appendData(s: string) {
          this.data += s;
        },
      };
      const seen = appendTextNodeDelta(node, 3, "hello");
      expect(node.data).toBe("hello");
      expect(seen).toBe(5);
    });

    it("replaces when the next text is shorter (truncation)", () => {
      const node = {
        data: "hello world",
        appendData(s: string) {
          this.data += s;
        },
      };
      appendTextNodeDelta(node, 11, "hi");
      expect(node.data).toBe("hi");
    });
  });

  describe("projectNotebookCells", () => {
    it("groups agent retries after a failed cell", () => {
      const grouped = projectNotebookCells([
        {
          producing_cell_id: "a",
          origin: "agent",
          status: "error",
          kernel_id: "python",
          language: "python",
        },
        {
          producing_cell_id: "b",
          origin: "agent",
          status: "ok",
          kernel_id: "python",
          language: "python",
        },
      ]);
      expect(grouped).toHaveLength(1);
      expect(grouped[0] && grouped[0].producing_cell_id).toBe("b");
      expect(grouped[0] && grouped[0].attempt_count).toBe(2);
      expect(grouped[0] && grouped[0]._revisions && grouped[0]!._revisions!.length).toBe(1);
    });
  });

  describe("WS wiring", () => {
    it("routes notebook_cell_* only when mine(fid)", () => {
      currentId.value = "frame-1";
      onEvent({
        type: "notebook_cell_start",
        root_frame_id: "frame-1",
        producing_cell_id: "c9",
        cell_id: "c9",
      });
      expect(nbFindCell("c9")).toBeTruthy();
      onEvent({
        type: "notebook_cell_start",
        root_frame_id: "other",
        producing_cell_id: "c8",
        cell_id: "c8",
      });
      expect(nbFindCell("c8")).toBeNull();
    });
  });
});

function asLiveHas(id: string): boolean {
  return (liveCells.value as { producing_cell_id?: string }[]).some(
    (c) => c.producing_cell_id === id,
  );
}

function asSavedHas(id: string): boolean {
  return (cells.value as { producing_cell_id?: string }[]).some((c) => c.producing_cell_id === id);
}
