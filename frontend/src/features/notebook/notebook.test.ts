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
  pendingReplIdentity,
  variableInspector,
} from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { bytes, PREVIEW_READ_MAX_BYTES } from "../artifacts/api";
import { filesT } from "../artifacts/copy";
import { resetStoreFields } from "../../stores/signal-field";
import { _replayGap, running } from "../../stores/stream";
import { branchState, executionQueue, securityState } from "../../stores/timeline";
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
  notebookExportLink,
  NOTEBOOK_EXPORTS,
  relabelNotebookExports,
  resetHighlightMemo,
  resetNotebookCellCaches,
} from "./chrome";
import { LANG, setLang } from "../../i18n/runtime";
import { ReplHistory, caretOnFirstLine, caretOnLastLine, indent } from "./editor";
import { environmentMenuItems, envSelectable, envSourceLabel } from "./envmenu";
import { nbT } from "./copy";
import { installNotebook } from "./install";
import type { NotebookCell } from "./types";
import {
  copyNotebookCell,
  currentKernelStatus,
  editAsNewCell,
  executeNotebookCode,
  forkNotebookCell,
  handleKernelStatus,
  kernelAction,
  kernelCtl,
  forkPending,
  invalidateKernelCache,
  kernelView,
  nbSwitchEnv,
  notebookOnTurnDone,
  refreshKernelState,
  replBusy,
  replEnabledNow,
  replHistory,
  replLoad,
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
      kc.envs = [{ name: "python" }];
      kc.cur = "python";
      kc.envAt = 77;
    }

    function expectInvalidated(): void {
      const kc = _kc.value;
      expect(kc.id).toBeNull();
      expect(kc.st).toBeNull();
      expect(kc.stAt).toBe(0);
      expect(kc.envs).toBeNull();
      expect(kc.cur).toBeNull();
      expect(kc.envAt).toBe(0);
    }

    it("clears id/st/envs (app.js:9955)", () => {
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

    it("stores a kernel_status sandbox scrubbed, keeping the permission state", () => {
      securityState.value = {
        sandbox: { state: "old" },
        permission: { mode: "ask", pending_count: 2, unattended: "deny" },
      };
      onEvent({
        type: "kernel_status",
        frame_id: "frame-1",
        status: "restarted",
        sandbox: { state: "enforced", mode: 7, runtimes: "not-a-list", detail: "x".repeat(900) },
      });
      const state = securityState.value as {
        sandbox: { state: string; mode: string; runtimes: unknown[]; detail: string };
        permission: { mode: string; pending_count: number };
      };
      expect(state.sandbox.state).toBe("enforced");
      expect(state.sandbox.mode).toBe("7");
      expect(state.sandbox.runtimes).toEqual([]);
      expect(state.sandbox.detail.length).toBeLessThanOrEqual(500);
      expect(state.permission).toEqual({ mode: "ask", pending_count: 2, unattended: "deny" });
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

  describe("cell actions: one request each, and only true feedback", () => {
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

    it("a REPL execution whose terminal event was lost settles after a replay gap", async () => {
      // NB-2: a dropped socket lost `execution_state`; the input stayed
      // disabled until a reload because nothing else cleared the pending run.
      const tick = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));
      let listed = true;
      const reads: string[] = [];
      setNotebookApi(async (path) => {
        reads.push(path);
        if (path.endsWith("/kernel/execute")) {
          return { status: "accepted", owner: { kind: "user_repl", id: "x" } };
        }
        if (path.endsWith("/execution-queue")) {
          return listed
            ? { owner: { execution_id: (pendingReplIdentity.value as { execution_id: string }).execution_id }, queue: [] }
            : { owner: null, queue: [] };
        }
        return { cells: [] };
      });
      expect(await executeNotebookCode("for i in range(5000): print(i)", "python")).toBe(true);
      expect(replBusy.value).toBe(true);
      _replayGap.value = "frame-1";
      await tick();
      expect(reads.filter((p) => p.endsWith("/execution-queue"))).toHaveLength(1);
      expect(replBusy.value).toBe(true); // still running on the server
      _replayGap.value = null;
      listed = false;
      _replayGap.value = "frame-1";
      await tick();
      expect(replBusy.value).toBe(false);
      expect(pendingReplIdentity.value).toBeNull();
    });

    it("Restart interrupts the user's own running cell instead of queueing behind it", async () => {
      // NB-10: a restart waited for the running REPL cell, and the header kept
      // the old generation and "Busy" with nothing saying a restart was asked.
      vi.stubGlobal("confirm", () => true);
      vi.stubGlobal("hint", () => undefined);
      executionQueue.value = {
        owner: { execution_id: "repl-1", owner: { kind: "user_repl", id: "repl-1" } },
        queue: [],
      };
      const paths: string[] = [];
      let seenAction: unknown = "unset";
      setNotebookApi(async (path) => {
        paths.push(path);
        if (path.endsWith("/kernel/restart")) seenAction = kernelAction.value;
        return { ok: true };
      });
      await kernelCtl("restart");
      expect(paths).toEqual(["/frames/frame-1/kernel/interrupt", "/frames/frame-1/kernel/restart"]);
      expect(seenAction).toEqual({ sid: "frame-1", action: "restart" });
      expect(kernelAction.value).toBeNull();
    });

    it("a restart or stop drops the Variable Inspector's read of the old namespace", () => {
      variableInspector.value = {
        language: "python",
        results: { python: { variables: [{ name: "v" }] }, r: { variables: [] } },
        loading: null,
        error: "",
        request: 1,
      };
      handleKernelStatus({ type: "kernel_status", status: "env_changed" });
      expect(Object.keys(variableInspector.value.results)).toEqual(["r"]);
      handleKernelStatus({ type: "kernel_status", status: "restarted", generation: 2 });
      expect(variableInspector.value.results).toEqual({});
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

    it("scrolling another dock pane says nothing about the Notebook", () => {
      // `.dock-body` scrolls for every pane; the Timeline scrolled up must not
      // hold the Notebook's list the next time it is opened.
      activeTab.value = "timeline";
      onNotebookScroll({ scrollHeight: 2000, scrollTop: 0, clientHeight: 400 });
      expect(_nbReading.value).toBe(false);
    });

    it("comes back from another pane painting the current list", () => {
      cells.value = [{ producing_cell_id: "a", cell_index: 1 }];
      notebookViewEntries();
      onNotebookScroll({ scrollHeight: 2000, scrollTop: 0, clientHeight: 400 });
      expect(_nbReading.value).toBe(true);
      running.value = true;
      activeTab.value = "files";
      expect(_nbReading.value).toBe(false);
      cells.value = [
        { producing_cell_id: "a", cell_index: 1 },
        { producing_cell_id: "b", cell_index: 2 },
      ];
      activeTab.value = "notebook";
      expect(notebookViewEntries().map(nbCellKey)).toEqual(["a", "b"]);
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

    it("makes the clean notebook the primary export and keeps the raw log (NB-1)", () => {
      const primary = NOTEBOOK_EXPORTS[0]!;
      const raw = NOTEBOOK_EXPORTS[1]!;
      expect(primary.variant).toBe("clean");
      expect(notebookExportHref("f-exec-root", primary)).toBe(
        "/api/v1/frames/f-exec-root/notebook/export?variant=clean",
      );
      expect(raw.language).toBe("bundle");
      expect(notebookExportHref("f-exec-root", raw)).toBe(
        "/api/v1/frames/f-exec-root/notebook/export?language=bundle",
      );
      expect(primary.label?.()).toMatch(/ipynb/);
      expect(raw.label?.()).not.toBe(primary.label?.());
    });

    // SYNC-8 follow-up: the split-button is built once per session (or per
    // provenance panel) and its labels stayed in the language they were built in.
    it("relabels a built export button when the language changes", async () => {
      class Node {
        tagName: string;
        className = "";
        textContent = "";
        innerHTML = "";
        href = "";
        onclick: unknown = null;
        attrs = new Map<string, string>();
        kids: Node[] = [];
        constructor(tag: string) {
          this.tagName = tag.toUpperCase();
        }
        setAttribute(name: string, value: string) {
          this.attrs.set(name, String(value));
        }
        getAttribute(name: string) {
          return this.attrs.has(name) ? this.attrs.get(name)! : null;
        }
        appendChild(kid: Node) {
          this.kids.push(kid);
          return kid;
        }
        querySelectorAll(selector: string): Node[] {
          const out: Node[] = [];
          const match = (n: Node) =>
            selector.startsWith("[")
              ? n.attrs.has(selector.slice(1, -1))
              : selector.startsWith(".")
                ? n.className.split(/\s+/).includes(selector.slice(1))
                : false;
          const walk = (n: Node) => n.kids.forEach((kid) => { if (match(kid)) out.push(kid); walk(kid); });
          walk(this);
          return out;
        }
      }
      const body = new Node("body");
      const doc = Object.assign(body, {
        body,
        documentElement: new Node("html"),
        createElement: (tag: string) => new Node(tag),
      });
      vi.stubGlobal("document", doc);
      const before = LANG;
      try {
        await setLang("en");
        body.appendChild(notebookExportLink("f-lang") as unknown as Node);
        const label = () => body.querySelectorAll("[data-nb-export]").map((n) => n.textContent);
        expect(label()[0]).toBe(NOTEBOOK_EXPORTS[0]!.label!());
        const english = label();
        await setLang("zh");
        expect(label()[0]).toBe(NOTEBOOK_EXPORTS[0]!.label!());
        expect(label()).not.toEqual(english);
        relabelNotebookExports(doc as unknown as ParentNode);
        expect(label()).toHaveLength(NOTEBOOK_EXPORTS.length);
      } finally {
        await setLang(before);
        vi.unstubAllGlobals();
      }
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
  class ArtifactNode {
    children: ArtifactNode[] = [];
    textContent = "";
    className = "";
    href = "";
    parent: ArtifactNode | null = null;
    onclick: (() => void) | null = null;
    attrs: Record<string, string> = {};
    constructor(public tag: string) {}
    appendChild(child: ArtifactNode) { this.children.push(child); child.parent = this; return child; }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); }
    setAttribute(name: string, value: string) { this.attrs[name] = value; }
  }
  const errorCard = (holder: ArtifactNode) => holder.children[0];
  const retryButton = (holder: ArtifactNode) =>
    errorCard(holder)?.children.find(
      (node) => node.tag === "button" && node.textContent === filesT("viewer.read.retry"),
    );
  const downloadLink = (holder: ArtifactNode) =>
    errorCard(holder)?.children.find((node) => node.tag === "a");

  it("keeps a failed exact table visible with its reason, download and retry", async () => {
    const holder = new ArtifactNode("div");
    const reads: string[] = [];
    vi.stubGlobal("document", { createElement: (tag: string) => new ArtifactNode(tag) });
    vi.stubGlobal("fetch", async (url: string) => {
      reads.push(url);
      return new Response(reads.length === 1 ? "missing" : "a,b\n1,2", { status: reads.length === 1 ? 404 : 200 });
    });
    try {
      const dispose = renderTableInto(holder as unknown as HTMLElement, "table.csv", "/api/v1/artifacts/versions/table-v1");
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(errorCard(holder)?.className).toBe("nbc-artifact-error");
      expect(errorCard(holder)?.children[0]?.textContent).toBe(
        filesT("viewer.read.failedStatus", 404),
      );
      expect(downloadLink(holder)?.href).toBe("/api/v1/artifacts/versions/table-v1");
      retryButton(holder)!.onclick?.();
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(reads).toEqual(["/api/v1/artifacts/versions/table-v1", "/api/v1/artifacts/versions/table-v1"]);
      expect(holder.children[0]?.className).toBe("nbc-table-scroll");
      dispose();
    } finally { vi.unstubAllGlobals(); }
  });

  it("shows the read limit and retries when the table preview is over budget", async () => {
    const holder = new ArtifactNode("div");
    let missing = true;
    vi.stubGlobal("document", { createElement: (tag: string) => new ArtifactNode(tag) });
    vi.stubGlobal("fetch", async () => {
      if (missing) {
        return {
          ok: true,
          status: 200,
          headers: new Headers({ "content-length": String(9 * 1024 * 1024) }),
          body: {
            cancel: async () => {},
            getReader: () => {
              throw new Error("the body must not be read");
            },
          },
        } as unknown as Response;
      }
      return new Response("a,b\n1,2\n");
    });
    try {
      const url = "/api/v1/artifacts/versions/table-limit";
      const dispose = renderTableInto(holder as unknown as HTMLElement, "table.csv", url);
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(errorCard(holder)?.children[0]?.textContent).toBe(
        filesT("viewer.read.limit", bytes(PREVIEW_READ_MAX_BYTES)),
      );
      expect(retryButton(holder)).toBeDefined();
      expect(downloadLink(holder)?.href).toBe(url);
      missing = false;
      retryButton(holder)!.onclick?.();
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(holder.children[0]?.className).toBe("nbc-table-scroll");
      dispose();
    } finally { vi.unstubAllGlobals(); }
  });
});

describe("REPL editor keys (NB-7)", () => {
  it("Tab indents at the caret and every selected line; Shift+Tab dedents", () => {
    expect(indent({ value: "x = 1", start: 0, end: 0 }, false)).toEqual({ value: "    x = 1", start: 4, end: 4 });
    const block = "for i in r:\nprint(i)\nprint(2)";
    const selected = indent({ value: block, start: 12, end: block.length }, false);
    expect(selected.value).toBe("for i in r:\n    print(i)\n    print(2)");
    const back = indent({ value: selected.value, start: selected.start, end: selected.end }, true);
    expect(back.value).toBe(block);
    // A selection that ends at a line start leaves that line alone.
    expect(indent({ value: "a\nb\nc", start: 0, end: 2 }, false).value).toBe("    a\nb\nc");
  });

  it("↑ on the first line recalls earlier input and ↓ walks back to the draft", () => {
    expect(caretOnFirstLine({ value: "a\nb", start: 1, end: 1 })).toBe(true);
    expect(caretOnFirstLine({ value: "a\nb", start: 3, end: 3 })).toBe(false);
    expect(caretOnLastLine({ value: "a\nb", start: 3, end: 3 })).toBe(true);
    const history = new ReplHistory();
    const entries = ["x = 1", "y = 2"];
    expect(history.older(entries, "draft")).toBe("y = 2");
    expect(history.older(entries, "y = 2")).toBe("x = 1");
    expect(history.older(entries, "x = 1")).toBeNull();
    expect(history.newer(entries)).toBe("y = 2");
    expect(history.newer(entries)).toBe("draft");
    expect(history.newer(entries)).toBeNull();
  });

  it("history is this session's REPL cells of the language; Edit loads a cell", () => {
    cells.value = [
      { producing_cell_id: "a", origin: "user", language: "python", source: "x = 1" },
      { producing_cell_id: "b", origin: "agent", language: "python", source: "agent()" },
      { producing_cell_id: "c", origin: "user", language: "r", source: "y <- 2" },
      { producing_cell_id: "d", origin: "user", language: "python", source: "x + 1" },
    ];
    expect(replHistory("python")).toEqual(["x = 1", "x + 1"]);
    expect(replHistory("r")).toEqual(["y <- 2"]);
    editAsNewCell({ producing_cell_id: "c", language: "r", source: "y <- 2" });
    expect(replLoad.value).toMatchObject({ code: "y <- 2", language: "r" });
  });
});

describe("Session options environment picker (ENV-6)", () => {
  const envs = [
    { name: "base", language: "python", runnable: true, builtin: true, notable: ["numpy"] },
    { name: "work", language: "python", runnable: true, builtin: false },
    { name: "r-mini", language: "r", runnable: false, builtin: false },
    { name: "broken", language: "python", runnable: false, builtin: true },
  ];

  beforeEach(() => {
    vi.stubGlobal("document", {
      createElement: () => ({ className: "", textContent: "", setAttribute: () => undefined }),
      querySelector: () => null,
    });
  });

  it("labels built-in and local environments and lets an R environment be chosen", () => {
    expect(envSourceLabel(envs[0]!)).toBe(nbT("nb.env.builtin"));
    expect(envSourceLabel(envs[1]!)).toBe(nbT("nb.env.local"));
    expect(envSelectable(envs[2]!)).toBe(true);
    expect(envSelectable(envs[3]!)).toBe(false);

    const picked: string[] = [];
    const items = environmentMenuItems({ envs, current: "base", currentR: null }, (name) => picked.push(name));
    const labels = items.map((item) => ("label" in item && item.label) || (item.node ? item.node.textContent : "sep"));
    expect(labels[0]).toBe(nbT("nb.env.group", "Python", "base"));
    expect(labels[1]).toContain("✓  base · " + nbT("nb.env.builtin"));
    expect(labels[2]).toContain("work · " + nbT("nb.env.local"));
    expect(labels).toContain(nbT("nb.env.group", "R", nbT("nb.env.default")));
    const click = (name: string) =>
      items.find((item) => "label" in item && typeof item.label === "string" && item.label.includes(name + " · "));
    click("work")!.onClick!();
    click("r-mini")!.onClick!();
    click("base")!.onClick!(); // already in use: no request
    click("broken")!.onClick!(); // cannot run: explained, not sent
    expect(picked).toEqual(["work", "r-mini"]);
  });
});
