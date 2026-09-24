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
  _replDrafts,
  cells,
  liveCells,
} from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { running } from "../../stores/stream";
import { branchState } from "../../stores/timeline";
import { activeTab, dock } from "../../stores/ui";
import { t } from "../../i18n/runtime";
import { copyFailedText } from "../chrome/clipboard";
import { LIVE_OUTPUT_CHAR_CAP, LIVE_OUTPUT_TRUNCATION } from "../stream/cap";
import { registerBuiltinHandlers, setArtifactCreatedSideEffects } from "../ws/handlers";
import { onEvent, resetWsHandlers } from "../ws/registry";
import {
  appendTextNodeDelta,
  cellOutput,
  loadExecutionLog,
  mergeNotebookCells,
  nbCellChunk,
  nbCellDraft,
  nbCellFinished,
  nbCellKey,
  nbCellStart,
  nbFindCell,
  notebookViewEntries,
  paintStreamedText,
  projectNotebookCells,
  resetCellOutputs,
  setNotebookApi,
} from "./cells";
import {
  highlightCellSource,
  highlightTraceback,
  mountLiveNotebookFigure,
  notebookArtifactState,
  renderTableInto,
  notebookExportHref,
  NOTEBOOK_EXPORTS,
  resetHighlightMemo,
  resetNotebookCellCaches,
} from "./chrome";
import { installNotebook } from "./install";
import type { NotebookCell } from "./types";
import {
  copyNotebookCell,
  currentKernelStatus,
  executeNotebookCode,
  forkNotebookCell,
  forkPending,
  invalidateKernelCache,
  kernelView,
  nbSwitchEnv,
  notebookOnTurnDone,
  refreshKernelState,
  replEnabledNow,
  shortRuntime,
  syncKernel,
} from "./kernel";
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
      const before = _kc.value;
      invalidateKernelCache();
      expectInvalidated();
      // A new object, not an in-place edit: whoever reads `_kc` hears about
      // it without a hand-maintained epoch counter.
      expect(_kc.value).not.toBe(before);
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

  describe("kernel status reads", () => {
    type Pending = { path: string; answer: (body: Record<string, unknown>) => void };
    function deferApi(): Pending[] {
      const pending: Pending[] = [];
      setNotebookApi(
        (path) =>
          new Promise((resolve) => {
            pending.push({ path, answer: resolve });
          }),
      );
      return pending;
    }
    const settle = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));

    it("a read still out for the previous session does not block the new one", async () => {
      const pending = deferApi();
      currentId.value = "frame-a";
      void refreshKernelState();
      invalidateKernelCache();
      currentId.value = "frame-b";
      void refreshKernelState();
      expect(pending.map((p) => p.path)).toEqual(["/frames/frame-a/kernel", "/frames/frame-b/kernel"]);
      pending[1]!.answer({ alive: true, state: "running" });
      pending[0]!.answer({ alive: false, state: "stopped" });
      await settle();
      expect(_kc.value.id).toBe("frame-b");
      expect(_kc.value.st).toEqual({ alive: true, state: "running" });
    });

    it("an answer that crossed an invalidation is shown, then read again", async () => {
      const pending = deferApi();
      void refreshKernelState();
      invalidateKernelCache();
      pending[0]!.answer({ alive: true, generation: 1 });
      await settle();
      expect(_kc.value.st).toEqual({ alive: true, generation: 1 });
      void refreshKernelState();
      expect(pending).toHaveLength(2);
      pending[1]!.answer({ alive: true, generation: 2 });
      await settle();
      expect(_kc.value.st).toEqual({ alive: true, generation: 2 });
    });

    it("keeps the last read on screen across an invalidation", async () => {
      setNotebookApi(async () => ({ alive: true, state: "running", repl_enabled: true }));
      await refreshKernelState();
      const shown = kernelView.value;
      expect(replEnabledNow()).toBe(true);
      invalidateKernelCache();
      expect(_kc.value.st).toBeNull();
      // Clearing what was shown flashed the status line to "…" and unmounted
      // the REPL panel after every turn, until the next read landed.
      expect(kernelView.value).toBe(shown);
      expect(currentKernelStatus()).toEqual({ alive: true, state: "running", repl_enabled: true });
      expect(replEnabledNow()).toBe(true);
    });

    it("shows nothing read for another session", async () => {
      setNotebookApi(async () => ({ alive: true, repl_enabled: true }));
      await refreshKernelState();
      currentId.value = "frame-2";
      expect(currentKernelStatus()).toBeNull();
      expect(replEnabledNow()).toBe(false);
    });

    it("a second REPL submission while the first is in flight sends nothing", async () => {
      const posts: string[] = [];
      let answer: (body: Record<string, unknown>) => void = () => undefined;
      setNotebookApi((path) => {
        posts.push(path);
        return new Promise((resolve) => {
          answer = resolve;
        });
      });
      const first = executeNotebookCode("print(1)", "python");
      // A double click on Rerun: each POST carried a new execution_id, and
      // the server's FIFO ran code with side effects twice.
      const second = executeNotebookCode("print(1)", "python");
      expect(posts).toEqual(["/frames/frame-1/kernel/execute"]);
      expect(await second).toBe(false);
      answer({ status: "accepted" });
      expect(await first).toBe(true);
    });

    it("Fork sends one request while one is out, and says when the branch exists", async () => {
      branchState.value = { capabilities: { fork_from_cell: true } };
      const hints: string[] = [];
      vi.stubGlobal("hint", (message: string) => hints.push(message));
      const posts: string[] = [];
      let answer: (response: Response) => void = () => undefined;
      vi.stubGlobal("fetch", (url: string) => {
        posts.push(url);
        return new Promise<Response>((resolve) => {
          answer = resolve;
        });
      });
      const cell = { producing_cell_id: "c7", fork_checkpoint_id: "ckpt-0123456789" };
      const first = forkNotebookCell(cell);
      const second = forkNotebookCell(cell);
      expect(posts).toEqual(["/api/v1/frames/frame-1/branches/fork"]);
      await second;
      answer(new Response(JSON.stringify({ branch_id: "b2" }), { status: 200 }));
      await first;
      expect(hints).toEqual([t("branch.forked", shortRuntime("ckpt-0123456789"))]);
      expect(forkPending.value).toBeNull();
    });

    it("Fork shows the server's refusal, not a success", async () => {
      branchState.value = { capabilities: { fork_from_cell: true } };
      const hints: string[] = [];
      vi.stubGlobal("hint", (message: string) => hints.push(message));
      const sentence = "historical source has no exact cursor checkpoint";
      vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ error: sentence }), { status: 409 }));
      await forkNotebookCell({ producing_cell_id: "c7", fork_checkpoint_id: "ckpt-1" });
      expect(hints).toEqual([t("branch.actionFailed", sentence)]);
      expect(forkPending.value).toBeNull();
    });

    it("Copy says it failed when no write was confirmed, and leaves the REPL draft alone", async () => {
      const hints: Array<[string, boolean | undefined]> = [];
      vi.stubGlobal("hint", (message: string, err?: boolean) => hints.push([message, err]));
      vi.stubGlobal("navigator", { clipboard: { writeText: () => Promise.reject(new Error("denied")) } });
      vi.stubGlobal("document", undefined);
      const drafts = _replDrafts.value;
      await copyNotebookCell("print(1)");
      expect(hints).toEqual([[copyFailedText(), true]]);
      expect(_replDrafts.value).toBe(drafts);
      expect(_replDrafts.value).toEqual({ python: "", r: "" });
    });

    it("Copy says it copied only after a confirmed write", async () => {
      const hints: Array<[string, boolean | undefined]> = [];
      vi.stubGlobal("hint", (message: string, err?: boolean) => hints.push([message, err]));
      let written = "";
      vi.stubGlobal("navigator", {
        clipboard: {
          writeText: async (text: string) => {
            written = text;
          },
        },
      });
      await copyNotebookCell("print(2)");
      expect(written).toBe("print(2)");
      expect(hints).toEqual([[t("nb.action.copied"), undefined]]);
    });

    it("reads only while the Notebook is on screen, and not again while fresh", async () => {
      const paths: string[] = [];
      setNotebookApi(async (path) => {
        paths.push(path);
        return path.endsWith("/environments") ? { environments: [], current: "python" } : { alive: true };
      });
      dock.value = { open: false, tab: "notebook" };
      syncKernel(true);
      dock.value = { open: true, tab: "files" };
      activeTab.value = "files";
      syncKernel(true);
      await settle();
      expect(paths).toEqual([]);
      activeTab.value = "notebook";
      syncKernel(true);
      await settle();
      expect(paths).toEqual(["/frames/frame-1/kernel", "/frames/frame-1/environments"]);
      syncKernel(true);
      await settle();
      expect(paths).toHaveLength(2);
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

    it("keeps the painted list while the reader is scrolled up during a turn", () => {
      const failed = {
        producing_cell_id: "a",
        cell_index: 1,
        origin: "agent",
        status: "error",
        kernel_id: "python",
        language: "python",
      };
      cells.value = [failed];
      expect(notebookViewEntries().map(nbCellKey)).toEqual(["a"]);
      running.value = true;
      _nbReading.value = true;
      // The agent retries: projected, the failed cell folds into the retry's
      // revisions. Painting that now would pull it out from under the reader.
      liveCells.value = [{ ...failed, producing_cell_id: "b", cell_index: 2, status: "running", live: true }];
      expect(notebookViewEntries().map(nbCellKey)).toEqual(["a"]);
      expect(_nbDirty.value).toBe(true);
      _nbReading.value = false;
      const flushed = notebookViewEntries();
      expect(flushed.map(nbCellKey)).toEqual(["b"]);
      expect((flushed[0]!._revisions || []).map(nbCellKey)).toEqual(["a"]);
    });

    it("never holds back another session's list", () => {
      cells.value = [{ producing_cell_id: "a", cell_index: 1 }];
      notebookViewEntries();
      running.value = true;
      _nbReading.value = true;
      currentId.value = "frame-2";
      cells.value = [{ producing_cell_id: "z", cell_index: 1 }];
      expect(notebookViewEntries().map(nbCellKey)).toEqual(["z"]);
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

  describe("paintStreamedText (a <pre> that came back after being elided)", () => {
    type FakeText = { data: string; appendData: (s: string) => void };
    function fakePre(): { firstChild: FakeText | null; appendChild: (node: FakeText) => void } {
      const pre = {
        firstChild: null as FakeText | null,
        appendChild(node: FakeText) {
          pre.firstChild = node;
        },
      };
      return pre;
    }
    function paint(pre: ReturnType<typeof fakePre> | null, seen: number, text: string): number {
      return paintStreamedText(pre as unknown as Parameters<typeof paintStreamedText>[0], seen, text);
    }

    beforeEach(() => {
      vi.stubGlobal("document", {
        createTextNode: (data: string): FakeText => ({
          data,
          appendData(s: string) {
            this.data += s;
          },
        }),
      });
    });

    it("repaints the whole output when the <pre> was unmounted in between", () => {
      const first = fakePre();
      let seen = paint(first, 0, "abc");
      expect(first.firstChild?.data).toBe("abc");
      seen = paint(null, seen, "abc\u0000\u0001");
      expect(seen).toBe(0);
      const second = fakePre();
      paint(second, seen, "abcdef");
      expect(second.firstChild?.data).toBe("abcdef");
    });

    it("starts a new, empty <pre> from zero whatever count it is handed", () => {
      const first = fakePre();
      const seen = paint(first, 0, "abc");
      const second = fakePre();
      paint(second, seen, "abcdef");
      expect(second.firstChild?.data).toBe("abcdef");
      paint(second, 6, "abcdefgh");
      expect(second.firstChild?.data).toBe("abcdefgh");
    });

    it("shows a finished record exactly, not as a tail on what streamed", () => {
      const pre = fakePre();
      const seen = paint(pre, 0, "partial line");
      paintStreamedText(pre as unknown as Parameters<typeof paintStreamedText>[0], seen, "final record!", true);
      expect(pre.firstChild?.data).toBe("final record!");
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

    // The memoized cell view only skips a cell whose props are the same
    // object; every projection used to clone every cell.
    it("returns the same projected object for finished records that did not change", () => {
      const failed = { producing_cell_id: "a", origin: "agent", status: "error", kernel_id: "python", language: "python" };
      const retry = { producing_cell_id: "b", origin: "agent", status: "ok", kernel_id: "python", language: "python" };
      const other = { producing_cell_id: "c", origin: "user", status: "ok" };
      const first = projectNotebookCells([failed, retry, other]);
      const second = projectNotebookCells([failed, retry, other]);
      expect(second).toHaveLength(2);
      expect(second[0]).toBe(first[0]);
      expect(second[1]).toBe(first[1]);
      const changed = { ...other, stdout: "new" };
      const third = projectNotebookCells([failed, retry, changed]);
      expect(third[0]).toBe(first[0]);
      expect(third[1]).not.toBe(first[1]);
      expect(third[1]!.stdout).toBe("new");
    });

    it("rebuilds a group with a running member, whose record changes in place", () => {
      const live: NotebookCell = { producing_cell_id: "r", live: true, status: "running" };
      const before = projectNotebookCells([live]);
      live.output_artifacts = [{ filename: "p.png", artifact_id: "art", version_id: "v1", url: "/u" }];
      const after = projectNotebookCells([live]);
      expect(after[0]).not.toBe(before[0]);
      expect(after[0]!.output_artifacts).toHaveLength(1);
    });

    it("keeps a record's identity when the execution log sends it back unchanged", async () => {
      let stdout = "1\n";
      setNotebookApi(async () => ({
        entries: [
          { producing_cell_id: "k1", cell_index: 1, status: "ok", stdout, figures: ["f.png"] },
          { producing_cell_id: "k2", cell_index: 2, status: "ok", stdout: "2\n" },
        ],
        kernels: ["python"],
      }));
      await loadExecutionLog("frame-1");
      const [k1, k2] = cells.value as NotebookCell[];
      await loadExecutionLog("frame-1");
      expect((cells.value as NotebookCell[])[0]).toBe(k1);
      expect((cells.value as NotebookCell[])[1]).toBe(k2);
      stdout = "1\nmore\n";
      await loadExecutionLog("frame-1");
      expect((cells.value as NotebookCell[])[0]).not.toBe(k1);
      expect((cells.value as NotebookCell[])[0]!.stdout).toBe("1\nmore\n");
      expect((cells.value as NotebookCell[])[1]).toBe(k2);
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


describe("Notebook immutable output selection", () => {
  const binding = { filename: "one/plot.png", artifact_id: "a", version_id: "v1", url: "/api/v1/artifacts/versions/v1" };
  it("matches the full filename and distinguishes saving, unknown and ambiguous history", () => {
    expect(notebookArtifactState({ output_artifacts: [binding] }, "one/plot.png").artifact).toBe(binding);
    expect(notebookArtifactState({ output_artifacts: [binding] }, "two/plot.png").state).toBe("unconfirmed");
    expect(notebookArtifactState({ live: true }, "one/plot.png").state).toBe("saving");
    expect(notebookArtifactState({}, "one/plot.png").state).toBe("unconfirmed");
    expect(notebookArtifactState({ output_artifacts: [binding, { ...binding, version_id: "v2" }] }, binding.filename).state).toBe("unconfirmed");
  });
  it("keeps a failed exact table visible and retries only its version URL", async () => {
    class Node {
      children: Node[] = [];
      textContent = "";
      className = "";
      parent: Node | null = null;
      onclick: (() => void) | null = null;
      constructor(public tag: string) {}
      appendChild(child: Node) { this.children.push(child); child.parent = this; return child; }
      remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); }
    }
    const holder = new Node("div");
    const reads: string[] = [];
    vi.stubGlobal("document", { createElement: (tag: string) => new Node(tag) });
    vi.stubGlobal("fetch", async (url: string) => {
      reads.push(url);
      return new Response(reads.length === 1 ? "missing" : "a,b\n1,2", { status: reads.length === 1 ? 404 : 200 });
    });
    try {
      const dispose = renderTableInto(holder as unknown as HTMLElement, "table.csv", "/api/v1/artifacts/versions/table-v1");
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(holder.children[0]?.className).toBe("nbc-artifact-error");
      holder.children[0]?.children[0]?.onclick?.();
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(reads).toEqual(["/api/v1/artifacts/versions/table-v1", "/api/v1/artifacts/versions/table-v1"]);
      expect(holder.children[0]?.className).toBe("nbc-table-scroll");
      dispose();
    } finally { vi.unstubAllGlobals(); }
  });
});
