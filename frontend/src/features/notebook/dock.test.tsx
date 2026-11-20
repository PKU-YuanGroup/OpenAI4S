/**
 * Notebook dock components, called as plain functions: the suite runs in node
 * with no DOM, so a test reads the VNode tree Preact would commit. Hooks are
 * stubbed to their initial values.
 *
 * Not named Notebook.test.tsx: on a case-insensitive disk that shares a base
 * name with notebook.test.ts, and tsc then silently leaves one of them out.
 */
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("preact/hooks", () => ({
  useRef: (initial: unknown) => ({ current: initial }),
  useLayoutEffect: () => undefined,
  useEffect: () => undefined,
  useState: (initial: unknown) => [initial, () => undefined],
}));

import { effect } from "@preact/signals";
import { LANG, i18nReady, setLang, t } from "../../i18n/runtime";
import { cells, liveCells, pendingReplIdentity } from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { running } from "../../stores/stream";
import { actionTimeline, branchState } from "../../stores/timeline";
import { resetStoreFields } from "../../stores/signal-field";
import { markNotebookSeen, notebookDisplayEntries } from "./cells";
import { onNotebookScroll } from "./scroll";
import type { NotebookCell } from "./types";
import { activeTab, dock } from "../../stores/ui";
import { forkPending, invalidateKernelCache, kernelView, runtimeStatusText } from "./kernel";
import {
  CellActions,
  CellList,
  CellOutput,
  KernelChips,
  NewCellsPill,
  NotebookDock,
  StatusStrip,
  kernelExitText,
  kernelStatusLine,
  outputIsLong,
  outputLabel,
  writtenImages,
} from "./Notebook";
import { nbLabelT, nbT } from "./copy";
import { kernelLabel } from "./labels";

type VNode = {
  type?: unknown;
  key?: unknown;
  props?: Record<string, unknown> & { children?: unknown };
};

/** Expand plain function components down to host elements and read the text. */
function textOf(node: unknown): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  const vnode = node as VNode;
  if (typeof vnode.type === "function") {
    return textOf((vnode.type as (props: unknown) => unknown)(vnode.props || {}));
  }
  return textOf(vnode.props?.children);
}

/** The keyed cell entries a CellList renders. */
function listed(tree: unknown): VNode[] {
  const children = (tree as VNode).props?.children;
  return (Array.isArray(children) ? children : [children]) as VNode[];
}

// Binary by any heuristic: nothing but control characters, 2048 of them.
const BINARY = "\u0000\u0001\u0002\u0003".repeat(512);

beforeAll(async () => {
  await i18nReady();
});

beforeEach(() => {
  resetStoreFields();
});

// SCI-35: one chip read "Python", the other "r"; cells said "python [1]" / "r [2]".
describe("Notebook language names", () => {
  it("names Python and R the same way in chips and cell labels", () => {
    expect(kernelLabel("python")).toBe("Python");
    expect(kernelLabel("r")).toBe("R");
    expect(kernelLabel("python — lab_env")).toBe("Python — lab_env");
    expect(kernelLabel("r — lab_env")).toBe("R — lab_env");
    expect(kernelLabel("rust")).toBe("rust");
  });
});

describe("Notebook output judged binary", () => {
  it("shows the elision notice with its size instead of an empty block", () => {
    const expected = t("output.binaryElided", "2.0 KB");
    expect(expected).toContain("2.0 KB");
    expect(textOf(CellOutput({ text: BINARY, isError: false, live: false }))).toBe(expected);
    expect(textOf(CellOutput({ text: BINARY, isError: true, live: true }))).toBe(expected);
  });
});

describe("Notebook cell identity across completion", () => {
  it("keeps one component under the cell's key from its first chunk to its record", () => {
    const base = { producing_cell_id: "c1", cell_id: "c1", cell_index: 1, source: "print(1)" };
    liveCells.value = [{ ...base, live: true, status: "running" }];
    const streaming = listed(CellList({ entries: notebookDisplayEntries() }));
    liveCells.value = [];
    cells.value = [{ ...base, status: "ok", stdout: "1\n" }];
    const finished = listed(CellList({ entries: notebookDisplayEntries() }));
    expect(streaming).toHaveLength(1);
    expect(finished).toHaveLength(1);
    // A different component type under the same key makes Preact unmount the
    // card: open outputs and revisions collapse and the page jumps.
    expect(finished[0]!.key).toBe(streaming[0]!.key);
    expect(finished[0]!.type).toBe(streaming[0]!.type);
  });
});

/** The first host element whose class list contains `name`. */
function byClass(node: unknown, name: string): VNode | null {
  if (Array.isArray(node)) {
    for (const child of node) {
      const hit = byClass(child, name);
      if (hit) return hit;
    }
    return null;
  }
  if (!node || typeof node !== "object") return null;
  const vnode = node as VNode;
  if (String(vnode.props?.class || "").split(/\s+/).includes(name)) return vnode;
  return byClass(vnode.props?.children, name);
}

describe("Notebook kernel status line", () => {
  it("renders the session's last kernel read, and keeps it across an invalidation", () => {
    currentId.value = "frame-1";
    kernelView.value = {
      sid: "frame-1",
      st: { alive: true, env: { python_version: "3.11" } },
      envs: null,
      cur: null,
    };
    const line = (): VNode => byClass(StatusStrip(), "nb-status-line")!;
    const ready = t("nb.status.ready", "Python 3.11");
    expect(textOf(line())).toBe(ready);
    expect(String(line().props?.class)).toContain("ready");
    invalidateKernelCache();
    expect(textOf(line())).toBe(ready);
    currentId.value = "frame-2";
    expect(textOf(line())).toBe("…");
  });

  it("a kernel read that leaves the REPL mode alone does not re-render the whole dock", () => {
    currentId.value = "frame-1";
    kernelView.value = { sid: "frame-1", st: { alive: true }, envs: null, cur: null };
    let renders = 0;
    const dispose = effect(() => {
      renders += 1;
      NotebookDock({ entries: [] });
    });
    try {
      kernelView.value = { sid: "frame-1", st: { alive: true, generation: 2 }, envs: null, cur: null };
      expect(renders).toBe(1);
      // The REPL panel replacing the status strip is the change the dock owns.
      kernelView.value = { sid: "frame-1", st: { alive: true, repl_enabled: true }, envs: null, cur: null };
      expect(renders).toBe(2);
    } finally {
      dispose();
    }
  });
});

describe("Notebook subscriptions", () => {
  function renders(run: () => void): { count: () => number; dispose: () => void } {
    let count = 0;
    const dispose = effect(() => {
      count += 1;
      run();
    });
    return { count: () => count, dispose };
  }

  it("a cell's action row follows what it shows, not every Timeline refresh or kernel read", () => {
    currentId.value = "frame-1";
    branchState.value = { capabilities: { fork_from_cell: true, promote: true } };
    kernelView.value = { sid: "frame-1", st: { repl_enabled: true }, envs: null, cur: null };
    const cell = { producing_cell_id: "c", source: "x = 1", fork_checkpoint_id: "cp1" };
    const row = renders(() => CellActions({ cell }));
    try {
      branchState.value = { capabilities: { fork_from_cell: true, promote: true }, branch_id: "b2" };
      actionTimeline.value = { branch_id: "b2" };
      kernelView.value = { sid: "frame-1", st: { repl_enabled: true, generation: 3 }, envs: null, cur: null };
      expect(row.count()).toBe(1);
      branchState.value = { capabilities: { promote: true } };
      expect(row.count()).toBe(2);
    } finally {
      row.dispose();
    }
  });

  it("the kernel chips follow the badge, not branch, Timeline or cell refreshes", () => {
    currentId.value = "frame-1";
    const chips = renders(() => KernelChips({ entries: [] }));
    try {
      branchState.value = { branch_id: "b1" };
      actionTimeline.value = { branch_id: "b1" };
      cells.value = [{ producing_cell_id: "x", state_revision: 4 }];
      expect(chips.count()).toBe(1);
      running.value = true;
      expect(chips.count()).toBe(2);
    } finally {
      chips.dispose();
    }
  });
});

describe("Notebook cell actions", () => {
  function buttons(node: unknown, found: VNode[] = []): VNode[] {
    if (Array.isArray(node)) {
      node.forEach((child) => buttons(child, found));
      return found;
    }
    if (!node || typeof node !== "object") return found;
    const vnode = node as VNode;
    if (vnode.type === "button") found.push(vnode);
    buttons(vnode.props?.children, found);
    return found;
  }

  it("Rerun and Fork are disabled while their request is out", () => {
    currentId.value = "frame-1";
    kernelView.value = { sid: "frame-1", st: { repl_enabled: true }, envs: null, cur: null };
    branchState.value = { capabilities: { fork_from_cell: true, promote: true } };
    const cell = { producing_cell_id: "c", source: "x = 1", fork_checkpoint_id: "cp1" };
    const row = (): VNode[] => buttons(CellActions({ cell }));
    const button = (label: string): VNode => row().find((b) => textOf(b) === label)!;
    const rerun = button(t("nb.action.rerun"));
    const fork = button(t("nb.action.fork"));
    expect(rerun!.props?.disabled).toBe(false);
    expect(fork!.props?.disabled).toBe(false);
    pendingReplIdentity.value = {
      frame_id: "frame-1",
      execution_id: "repl-1",
      owner: { kind: "user_repl", id: "repl-1" },
    };
    forkPending.value = "frame-1";
    expect(button(t("nb.action.rerun")).props?.disabled).toBe(true);
    expect(button(t("nb.action.fork")).props?.disabled).toBe(true);
    // Loading a cell into the input runs nothing, so it stays available.
    expect(button(nbT("nb.action.edit")).props?.disabled).toBe(false);
  });
});

// M-54 / SCI-23: nearly every cell wore "Current"; a stale one said why only
// in a tooltip, as "… superseded by c-4653d2c179b5 (S3)".
describe("Notebook cell state", () => {
  const stateText = (cell: NotebookCell) =>
    textOf(CellList({ entries: [cell] })).replace(/\s+/g, " ");

  it("shows no badge on a cell whose results hold", () => {
    const text = stateText({ producing_cell_id: "a", cell_index: 1, source: "x = 1", status: "ok" });
    expect(text).not.toContain(nbLabelT("nb.cell.current"));
  });

  it("says why a cell is stale, naming cells the way the Notebook does", () => {
    cells.value = [
      { producing_cell_id: "c-a", cell_index: 2, language: "python", source: "y = x" },
      { producing_cell_id: "c-b", cell_index: 5, language: "python", source: "x = 2" },
    ];
    const stale: NotebookCell = {
      producing_cell_id: "c-c",
      cell_index: 3,
      source: "z = y",
      stale: true,
      stale_reasons: ["depends on c-a (S2): variable 'x' was superseded by c-b (S5)"],
    };
    const text = stateText(stale);
    expect(text).toContain(nbLabelT("nb.cell.stale"));
    expect(text).toContain(
      nbT("nb.stale.via", "Python [2]", nbT("nb.stale.superseded", "Python [5]", "x")),
    );
    expect(text).not.toContain("c-b");
  });

  it("says where the manual cells switch is when Rerun is grey", () => {
    currentId.value = "frame-1";
    kernelView.value = { sid: "frame-1", st: { repl_enabled: false }, envs: null, cur: null };
    const titles = JSON.stringify(CellActions({ cell: { producing_cell_id: "c", source: "x = 1" } }));
    expect(titles).toContain(nbT("nb.action.needsManual"));
    expect(titles).not.toContain(t("nb.action.unavailable"));
  });
});

describe("Notebook reading gate", () => {
  it("CellList paints the entries it is handed and subscribes to no cell store", () => {
    const entries = [{ producing_cell_id: "shown", cell_index: 1, status: "error" }];
    let paints = 0;
    let painted: VNode[] = [];
    const dispose = effect(() => {
      paints += 1;
      painted = listed(CellList({ entries }));
    });
    try {
      // A retry arriving while the reader is scrolled up must not repaint
      // the list: only a render the gate allows hands CellList new entries.
      cells.value = [{ producing_cell_id: "other", cell_index: 2 }];
      liveCells.value = [{ producing_cell_id: "retry", cell_index: 3, live: true }];
      expect(paints).toBe(1);
      expect(painted.map((node) => node.key)).toEqual(["shown"]);
    } finally {
      dispose();
    }
  });

  // NB-2: the conversation was at cell 12 while the Notebook still showed
  // cell 10 and said "busy"; four cells appeared at once at the bottom.
  it("says how many cells arrived while the reader was away from the bottom", () => {
    const pill = () => textOf(NewCellsPill());
    cells.value = [{ producing_cell_id: "a", cell_index: 1, status: "ok" }];
    markNotebookSeen();
    running.value = true;
    expect(pill()).toBe("");

    // Its own subscription: it updates while the held list does not repaint.
    let paints = 0;
    const dispose = effect(() => {
      paints += 1;
      NewCellsPill();
    });
    try {
      cells.value = [...(cells.value as NotebookCell[]), { producing_cell_id: "b", cell_index: 2, status: "ok" }];
      expect(paints).toBe(2);
      expect(pill()).toBe(nbT("nb.newCells.one"));
      liveCells.value = [{ producing_cell_id: "c", cell_index: 3, status: "running", live: true }];
      expect(pill()).toBe(nbT("nb.newCells", 2));

      // Back at the bottom with the Notebook on screen: nothing is new.
      dock.value = { open: true, tab: "notebook" };
      activeTab.value = "notebook";
      onNotebookScroll({ scrollHeight: 500, scrollTop: 400, clientHeight: 100 });
      expect(pill()).toBe("");
      // Scrolled up again and another cell arrives.
      onNotebookScroll({ scrollHeight: 5000, scrollTop: 0, clientHeight: 100 });
      cells.value = [...(cells.value as NotebookCell[]), { producing_cell_id: "d", cell_index: 4, status: "ok" }];
      expect(pill()).toBe(nbT("nb.newCells.one"));
      // An idle Notebook announces nothing.
      running.value = false;
      expect(pill()).toBe("");
    } finally {
      dispose();
    }
  });
});

describe("Notebook after a kernel crash", () => {
  it("explains a dead kernel in the reader's language, with its exit status", () => {
    const oom =
      "the kernel process exited before this cell finished (exit code -9: killed with " +
      "SIGKILL, which usually means it ran out of memory); variables from earlier cells " +
      "are lost and the next cell starts a fresh kernel (kernel_execution_failed req-1)";
    expect(kernelExitText(oom)).toBe(nbT("nb.crash.notice", nbT("nb.crash.oom", "-9")));
    const segv = oom.replace(/\(exit code -9[^)]*\)/, "(killed by signal 11 (SIGSEGV))");
    expect(kernelExitText(segv)).toBe(nbT("nb.crash.notice", nbT("nb.crash.signal", "11 (SIGSEGV)")));
    expect(kernelExitText("NameError: name 'x' is not defined")).toBeNull();
  });

  it("marks where a new kernel generation starts, per language", () => {
    const cell = (id: string, generation: string, language = "python") => ({
      producing_cell_id: id,
      cell_index: id,
      generation_id: generation,
      language,
      status: "ok",
    });
    const tree = CellList({
      entries: [
        cell("a", "gen-1"),
        cell("r1", "r-gen-1", "r"),
        cell("b", "gen-1"),
        cell("c", "gen-2"),
        cell("r2", "r-gen-1", "r"),
      ],
    });
    const keys = listed(tree).map((node) => String(node.key));
    expect(keys).toEqual(["a", "r1", "b", "gen:c", "c", "r2"]);
    expect(textOf(listed(tree)[3])).toBe(nbT("nb.generation.divider"));
  });
});

describe("Notebook cell output disclosure", () => {
  it("names stdout and stderr apart, and says how long a long output is", () => {
    expect(textOf(CellOutput({ text: "4\n", isError: false, live: false }))).toContain(nbT("nb.output.stdout"));
    expect(textOf(CellOutput({ text: "warn\n", isError: true, live: false }))).toContain(nbT("nb.output.stderr"));
    const long = Array.from({ length: 200 }, (_, i) => `line ${i}`).join("\n");
    expect(outputIsLong(long)).toBe(true);
    expect(outputIsLong("a\nb\n")).toBe(false);
    expect(outputIsLong("x".repeat(7000))).toBe(true);
    expect(outputLabel(long, false, false)).toBe(nbT("nb.output.long", nbT("nb.output.stdout"), 200));
    // A running cell is never labelled (or collapsed) as long.
    expect(outputLabel(long, false, true)).toBe(nbT("nb.output.stdout"));
  });
});

describe("Notebook images a cell wrote", () => {
  it("shows saved image files inline, once, beside captured figures", () => {
    const cell = {
      producing_cell_id: "c-img",
      figures: ["figure_cell3_1.png"],
      files_written: ["figure_cell3_1.png", "plots/volcano.png", "Rplot_2_01.png", "table.csv", "Rplots.pdf"],
    };
    expect(writtenImages(cell)).toEqual(["plots/volcano.png", "Rplot_2_01.png"]);
    const many = { files_written: Array.from({ length: 20 }, (_, i) => `p${i}.png`) };
    expect(writtenImages(many)).toHaveLength(8);
  });
});

describe("Notebook read-only mode (NB-5)", () => {
  it("tells a kernel that never ran from one that ended, stopped or exited", () => {
    const env = { python_version: "3.11" };
    expect(kernelStatusLine({ state: "none", env }).text).toBe(nbT("nb.status.notStarted", "Python 3.11"));
    expect(kernelStatusLine({ state: "ended", generation_id: "g", env }).text).toBe(t("nb.status.ended", "Python 3.11"));
    expect(kernelStatusLine({ state: "stopped", generation_id: "g", env }).text).toBe(nbT("nb.status.stopped", "Python 3.11"));
    expect(kernelStatusLine({ state: "none", generation_id: "g", env }).text).toBe(nbT("nb.status.exited", "Python 3.11"));
    expect(kernelStatusLine({ alive: true, state: "running", env }).cls).toBe("ready");
  });

  it("says what the read-only Notebook is and where manual cells are turned on", () => {
    currentId.value = "frame-1";
    kernelView.value = { sid: "frame-1", st: { state: "none" }, envs: null, cur: null };
    const text = textOf(StatusStrip());
    expect(text).toContain(nbT("nb.readonly.hint"));
    expect(text).toContain(nbT("nb.readonly.openSettings"));
    expect(textOf(KernelChips({ entries: [] }))).toContain(nbT("nb.kernel.notStarted"));
  });

  it("calls a kernel whose process died 'exited', not 'not started' or 'view only'", () => {
    currentId.value = "frame-1";
    kernelView.value = { sid: "frame-1", st: { state: "none", generation_id: "g-dead" }, envs: null, cur: null };
    const chips = textOf(KernelChips({ entries: [] }));
    expect(chips).toContain(nbT("nb.kernel.stateExited"));
    expect(chips).not.toContain(nbT("nb.kernel.notStarted"));
  });

  // I18N-1: owner chips, cell states and the Variable Inspector were English
  // in a Chinese UI.
  it("labels owners, cell states and the variable inspector in Chinese", async () => {
    const original = LANG;
    try {
      await setLang("zh");
      expect(nbLabelT("nb.owner.agent")).toBe("智能体");
      expect(nbLabelT("nb.cell.current")).toBe("当前");
      expect(nbLabelT("nb.variables.title")).toBe("变量查看器");
      expect(nbLabelT("nb.action.promote")).toBe("保存为产物");
      expect(nbT("nb.cell.status.ok")).toBe("完成");
      expect(nbLabelT("nb.kernel.stopLabel")).toBe(t("nb.kernel.stopLabel"));
    } finally {
      await setLang(original);
    }
  });

  // Follow-up A: the Timeline's runtime strip said "Ended · view only" for a
  // kernel that never started after the Notebook badge stopped saying it; both
  // read one function now, and its words are not English in Chinese.
  it("names the runtime state the same way for the badge and the Timeline", () => {
    expect(runtimeStatusText("ended", { state: "none" })).toBe(nbT("nb.kernel.notStarted"));
    expect(runtimeStatusText("ended", { state: "none", generation_id: "g" })).toBe(nbT("nb.kernel.stateExited"));
    expect(runtimeStatusText("ended", { state: "stopped", generation_id: "g" })).toBe(t("nb.kernel.stateStopped"));
    expect(runtimeStatusText("ended", { state: "ended", generation_id: "g" })).toBe(nbT("nb.runtime.ended"));
    expect(runtimeStatusText("ended", null)).toBe(nbT("nb.runtime.ended"));
    expect(runtimeStatusText("busy", { alive: true })).toBe(nbT("nb.runtime.busy"));
    for (const mode of ["live", "busy", "ended", "restoring", "partial", "failed"]) {
      expect(runtimeStatusText(mode, null)).not.toMatch(/view only|仅供查看/);
    }
  });
});
