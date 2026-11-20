/**
 * Notebook dock: kernel chips / REPL header rendered apart from the cell list.
 * CellList is keyed by producing_cell_id; live chunks append a text node;
 * completed cells are memoized. Replaces app.js:10333-10630 `innerHTML=""` rebuild.
 */

import { render } from "preact";
import type { ComponentChildren } from "preact";
import { memo } from "preact/compat";
import { useLayoutEffect, useRef, useState } from "preact/hooks";
import { isReady } from "../../compat/stub";
import { t } from "../../i18n/runtime";
import {
  _kc,
  _replDrafts,
  _replLanguage,
  execSources,
  kernelFilter,
} from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { running } from "../../stores/stream";
import { executionQueue } from "../../stores/timeline";
import { activeTab, dock } from "../../stores/ui";
import { publicText } from "../scrub/scrub";
import { cellOutput, markNotebookSeen, nbCellKey, notebookViewEntries, paintStreamedText, unseenNotebookCells } from "./cells";
import {
  notebookArtifactState,
  el,
  highlightCellSource,
  highlightTraceback,
  looksBinary,
  notebookExportLink,
  renderTableInto,
  stripAnsi,
} from "./chrome";
import {
  canForkFromCell,
  canPromote,
  canRerun,
  copyNotebookCell,
  currentKernelEnvs,
  editAsNewCell,
  currentKernelStatus,
  envChoice,
  executeNotebookCode,
  forkBusy,
  forkNotebookCell,
  identityForOwner,
  interruptRepl,
  kernelAction,
  kernelCtl,
  kernelIdFromEnv,
  kernelLabel,
  nbSwitchEnv,
  promoteNotebookCell,
  replBusy,
  replEnabled,
  replHistory,
  replLoad,
  runtimeBadge,
  runtimeStatusText,
  shortRuntime,
  syncKernel,
} from "./kernel";
import {
  bindNotebookScroll,
  followLiveOutput,
  onNotebookScroll,
  measureNotebookFollow,
  nbRender,
  setNotebookRenderImpl,
} from "./scroll";
import { bytes } from "../artifacts/api";
import { filesT } from "../artifacts/copy";
import { applyArtifactDeepLink } from "../artifacts/ui";
import { openCustomize } from "../customize";
import { iconSvg } from "../icons/paths";
import type { KernelEnvRow, KernelStatus, NotebookCell, NotebookOutputArtifact, ScrollBox } from "./types";
import { nbLabelT, nbT } from "./copy";
import { ReplHistory, caretOnFirstLine, caretOnLastLine, indent, type EditorState } from "./editor";
import { envLanguage, envSelectable, envSourceLabel } from "./envmenu";
import "./notebook.css";

function notebookCellState(cell: NotebookCell): {
  key: string;
  cls: string;
  reasons?: unknown[];
} {
  if (cell.draft) return { key: "drafting", cls: "drafting" };
  if (String(cell.replay_policy || "").toLowerCase() === "never") {
    return { key: "nonReplayable", cls: "non-replayable" };
  }
  if (cell._historicalRevision) return { key: "historical", cls: "historical" };
  if (cell.stale === true) {
    return {
      key: "stale",
      cls: "stale",
      reasons: Array.isArray(cell.stale_reasons) ? cell.stale_reasons : [],
    };
  }
  return { key: "current", cls: "current" };
}

/**
 * The notice an output judged binary shows instead (send/step.ts `binElide`).
 * It used to be an empty `<div class="bin-elide">`, so the whole output --
 * and the ordinary log lines before the binary part -- vanished without a word.
 */
function BinaryElided({ length }: { length: number }) {
  return (
    <div class="bin-elide">
      <span class="ic" dangerouslySetInnerHTML={{ __html: iconSvg("file", 13) }} />
      <span>{t("output.binaryElided", bytes(length))}</span>
    </div>
  );
}

/** Past either bound an output that is already complete starts collapsed. */
const LONG_OUTPUT_LINES = 60;
const LONG_OUTPUT_CHARS = 6000;

/** Lines in an output, counted only as far as the bound needs. */
function outputLines(text: string, stopAt = Infinity): number {
  let lines = 1;
  for (let at = text.indexOf("\n"); at >= 0 && at < text.length - 1; at = text.indexOf("\n", at + 1)) {
    lines += 1;
    if (lines > stopAt) break;
  }
  return lines;
}

export function outputIsLong(text: string): boolean {
  return text.length > LONG_OUTPUT_CHARS || outputLines(text, LONG_OUTPUT_LINES) > LONG_OUTPUT_LINES;
}

/** The disclosure's label: which stream it is, and how much a collapsed one holds. */
export function outputLabel(text: string, isError: boolean, live: boolean): string {
  const name = nbT(isError ? "nb.output.stderr" : "nb.output.stdout");
  return !live && outputIsLong(text) ? nbT("nb.output.long", name, outputLines(text)) : name;
}

/**
 * One output block for a running and a finished cell. It used to be two
 * components, and the switch at completion remounted the block, so an output
 * the reader had opened snapped shut. A running cell appends its chunks; a
 * finished one shows its final record exactly.
 *
 * Open when it first appears -- a running cell's progress is visible as it
 * prints -- except a long output that is already complete. After that the
 * reader's choice stands: `open` is set once, never passed as a prop, so a
 * re-render cannot snap it back.
 */
export function CellOutput({ text, isError, live }: { text: string; isError: boolean; live: boolean }) {
  const preRef = useRef<HTMLPreElement>(null);
  const boxRef = useRef<HTMLDetailsElement>(null);
  const seen = useRef(0);
  const opened = useRef(false);
  useLayoutEffect(() => {
    seen.current = paintStreamedText(preRef.current, seen.current, text, !live);
    const box = boxRef.current;
    if (box && !opened.current) {
      opened.current = true;
      box.open = live || !outputIsLong(text);
    }
  }, [text, live]);
  if (!text) return null;
  if (looksBinary(text)) return <BinaryElided length={text.length} />;
  return (
    <details ref={boxRef} class={"nbc-disclosure" + (isError ? " error" : "")}>
      <summary>{outputLabel(text, isError, live)}</summary>
      <pre ref={preRef} class={isError ? "nbc-err" : "nbc-out"} />
    </details>
  );
}

/** A running cell reads its output signal; a finished one its record. */
function CellStream({ cell, stream, live }: { cell: NotebookCell; stream: "stdout" | "stderr"; live: boolean }) {
  const text = live ? cellOutput(nbCellKey(cell))[stream].value : String(cell[stream] || "");
  return <CellOutput text={text} isError={stream === "stderr"} live={live} />;
}

/** A cell's status word in the UI language ("ok" / "running" read as English in Chinese). */
function cellStatusLabel(status: string): string {
  const key = "nb.cell.status." + status;
  const label = nbT(key);
  return label === key ? status : label;
}

function CodeBlock(opts: {
  cacheKey: string;
  source: string;
  lang: string;
  langLabel: string;
  status: string;
  env?: string;
}) {
  const html = highlightCellSource(opts.cacheKey, opts.source || "", opts.lang);
  return (
    <div class="os-code">
      <div class="oc-head">
        <span class="oc-lang">
          <span>{opts.langLabel}</span>
        </span>
        <div class="oc-right">
          {opts.status ? <span class={"nbc-status " + opts.status}>{cellStatusLabel(opts.status)}</span> : null}
          {opts.env ? (
            <span class="oc-env">
              <span class="oc-env-k">env</span>
              <span class="oc-env-v">{opts.env}</span>
            </span>
          ) : null}
        </div>
      </div>
      <pre class="oc-src">
        <code dangerouslySetInnerHTML={{ __html: html }} />
      </pre>
    </div>
  );
}

const KERNEL_EXITED = "the kernel process exited before this cell finished (";

/**
 * The reader-language explanation of a cell whose kernel process died. The
 * server's sentence (cell_run `KERNEL_EXITED_MESSAGE`) is English and stays in
 * the record; this says the same thing, with the exit status read from it.
 */
export function kernelExitText(error: string): string | null {
  const at = String(error || "").indexOf(KERNEL_EXITED);
  if (at < 0) return null;
  const detail = String(error).slice(at + KERNEL_EXITED.length);
  let m: RegExpExecArray | null;
  let what: string;
  if ((m = /^exit code (-?\d+): killed with SIGKILL/.exec(detail))) what = nbT("nb.crash.oom", m[1]);
  else if ((m = /^killed by signal (\d+)(?: \((\w+)\))?/.exec(detail)))
    what = nbT("nb.crash.signal", m[2] ? `${m[1]} (${m[2]})` : m[1]);
  else if ((m = /^exit code (-?\d+)/.exec(detail))) what = nbT("nb.crash.exitCode", m[1]);
  else what = nbT("nb.crash.unknown");
  return nbT("nb.crash.notice", what);
}

function KernelExitNotice({ error }: { error: string }) {
  const text = kernelExitText(error);
  return text ? (
    <div class="nbc-kernel-exit" role="alert">
      {text}
    </div>
  ) : null;
}

function ErrorBlock({ raw }: { raw: string }) {
  const txt = stripAnsi(raw).replace(/\s+$/, "");
  const nonEmpty = txt.split("\n").filter((l) => l.trim());
  const summary = nonEmpty.length
    ? (nonEmpty[nonEmpty.length - 1] as string).trim()
    : t("nb.error.default");
  const m = summary.match(
    /^([A-Za-z_][\w.]*(?:Error|Exception|Warning|Interrupt|Exit|Fault))\b:?\s*([\s\S]*)$/,
  );
  return (
    <div class="nbc-error open">
      <div class={"nbc-error-head" + (nonEmpty.length > 1 ? " clickable" : "")}>
        {m ? (
          <>
            <span class="nbc-err-type">{m[1]}</span>
            {m[2] ? <span class="nbc-err-text">{m[2]}</span> : null}
          </>
        ) : (
          <span class="nbc-err-text">{summary}</span>
        )}
      </div>
      {nonEmpty.length > 1 ? (
        <pre class="nbc-error-tb" dangerouslySetInnerHTML={{ __html: highlightTraceback(txt) }} />
      ) : null}
    </div>
  );
}

function OutputLinks({ artifact }: { artifact: NotebookOutputArtifact }) {
  return <div class="nbc-actions">
    <a href={artifact.url} download={artifact.filename}>{filesT("nb.artifact.download")}</a>
    <button class="nbc-action" onClick={() => void applyArtifactDeepLink({ artifactId: artifact.artifact_id, versionId: artifact.version_id })}>
      {filesT("nb.artifact.open")}
    </button>
    <button class="nbc-action" onClick={() => void applyArtifactDeepLink({ artifactId: artifact.artifact_id, versionId: null })}>
      {filesT("nb.artifact.latest")}
    </button>
  </div>;
}

function ConfirmedFigure({ artifact }: { artifact: NotebookOutputArtifact }) {
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  return <figure>
    {failed ? <div class="nbc-artifact-error" role="status">
      {filesT("nb.artifact.failed")}
      <button class="nbc-action" onClick={() => { setFailed(false); setAttempt(attempt + 1); }}>{filesT("nb.artifact.retry")}</button>
    </div> : <img key={attempt} class="nbc-fig" src={artifact.url} alt={artifact.filename} onError={() => setFailed(true)} />}
    <OutputLinks artifact={artifact} />
  </figure>;
}

function CellFigures({ cell, names }: { cell: NotebookCell; names: string[] }) {
  return <>{names.map((filename) => {
    const result = notebookArtifactState(cell, filename);
    return result.artifact
      ? <ConfirmedFigure key={filename + ":" + result.artifact.version_id} artifact={result.artifact} />
      : <div key={filename} class="nbc-artifact-status" role="status">{filename + " · " + filesT("nb.artifact." + result.state)}</div>;
  })}</>;
}

function CellIo({ cell }: { cell: NotebookCell }) {
  const written = cell.files_written || [];
  const read = cell.files_read || [];
  if (!written.length && !read.length) return null;
  return (
    <div class="nbc-io">
      {written.map((f) => {
        const result = notebookArtifactState(cell, f);
        return <span key={"w:" + f} class="io-w">
          {result.artifact ? <a href={result.artifact.url} download={f}>{f}</a> : <span>{f + " · " + filesT("nb.artifact." + result.state)}</span>}
        </span>;
      })}
      {read.map((f) => (
        <span key={"r:" + f} class="io-r">
          <span>{f}</span>
        </span>
      ))}
    </div>
  );
}

export function CellActions({ cell }: { cell: NotebookCell }) {
  const appendable =
    canRerun.value && !replBusy.value && !cell.live && !!String(cell.source || "").trim();
  // Loading code into the input runs nothing, so it does not wait for the REPL.
  const editable = canRerun.value && !cell.live && !!String(cell.source || "").trim();
  const canFork =
    !cell.live && canForkFromCell.value && !!publicText(cell.fork_checkpoint_id, 96);
  return (
    <div class="nbc-actions">
      <button class="nbc-action" onClick={() => void copyNotebookCell(cell.source || "")}>
        {t("nb.action.copy")}
      </button>
      <button
        class="nbc-action"
        disabled={!appendable}
        title={appendable ? t("nb.action.rerun") : t("nb.action.unavailable")}
        onClick={() => {
          if (appendable) void executeNotebookCode(cell.source || "", cell.language || "python");
        }}
      >
        {t("nb.action.rerun")}
      </button>
      <button
        class="nbc-action"
        data-action="edit-as-new-cell"
        disabled={!editable}
        title={editable ? nbT("nb.action.editTitle") : t("nb.action.unavailable")}
        onClick={() => {
          if (editable) editAsNewCell(cell);
        }}
      >
        {nbT("nb.action.edit")}
      </button>
      {canFork ? (
        <button class="nbc-action" disabled={forkBusy.value} onClick={() => void forkNotebookCell(cell)}>
          {nbLabelT("nb.action.fork")}
        </button>
      ) : null}
      <button
        class="nbc-action"
        disabled={!canPromote.value}
        onClick={() => void promoteNotebookCell(cell)}
      >
        {nbLabelT("nb.action.promote")}
      </button>
    </div>
  );
}

function CellShell({
  cell,
  children,
}: {
  cell: NotebookCell;
  children: ComponentChildren;
}) {
  const k = cell.kernel_id || "python";
  const cellState = notebookCellState(cell);
  return (
    <div
      class={"notebook-cell" + (cell.live ? " live" : "") + (cell.draft ? " draft" : "")}
      data-cell={cell.cell_index != null ? String(cell.cell_index) : ""}
      data-kernel={k}
      data-producing-cell={cell.producing_cell_id || ""}
    >
      {(cell._revisions || []).length ? (
        <details class="nbc-revisions">
          <summary>
            {t("nb.revisions.summary", (cell._revisions || []).length + 1, (cell._revisions || []).length)}
          </summary>
          <div class="nbc-revision-list">
            {(cell._revisions || []).map((rev) => (
              <MemoCellView
                key={nbCellKey(rev)}
                cell={{ ...rev, _revisions: [], _historicalRevision: true }}
              />
            ))}
          </div>
        </details>
      ) : null}
      <div class="nbc-cell-meta">
        <span
          class={"nbc-state " + cellState.cls}
          title={(cellState.reasons || []).map((r) => publicText(r, 240)).filter(Boolean).join("\n")}
        >
          {nbLabelT("nb.cell." + cellState.key)}
        </span>
        {/* NB-9: one number per cell. The state revision equals the cell number
            except on a branch or a replay; only then is it a second, labelled fact. */}
        {cell.state_revision != null && cell.state_revision !== cell.cell_index ? (
          <span class="nbc-revision" title={nbT("nb.cell.stateRevisionTip")}>
            {nbT("nb.cell.stateRevision", cell.state_revision)}
          </span>
        ) : null}
      </div>
      {children}
    </div>
  );
}

/** Source and status: a running cell's signals, a finished cell's record. */
function CellCode({ cell, live }: { cell: NotebookCell; live: boolean }) {
  const rec = live ? cellOutput(nbCellKey(cell)) : null;
  const k = cell.kernel_id || "python";
  const idx = cell.cell_index != null ? cell.cell_index : "…";
  return (
    <CodeBlock
      cacheKey={nbCellKey(cell)}
      source={rec ? rec.source.value : cell.source || ""}
      lang={cell.language || k}
      langLabel={(cell.language || k) + " [" + idx + "]"}
      status={rec ? rec.status.value || "running" : cell.status || "ok"}
      env={cell.environment || cell.env}
    />
  );
}

function CellFiguresSlot({ cell, live }: { cell: NotebookCell; live: boolean }) {
  const names = live ? cellOutput(nbCellKey(cell)).figures.value : cell.figures || [];
  return <CellFigures cell={cell} names={names} />;
}

const INLINE_IMAGE = /\.(png|jpe?g|gif|webp)$/i;
const MAX_INLINE_IMAGES = 8;

/**
 * Image files the cell wrote itself -- `plt.savefig("x.png")`, an R plot --
 * shown like the figures the kernel captured. Only those appeared inline; a
 * saved image was a file-name chip to click through.
 */
export function writtenImages(cell: NotebookCell): string[] {
  const captured = new Set(cell.figures || []);
  return (cell.files_written || [])
    .filter((name) => INLINE_IMAGE.test(name) && !captured.has(name))
    .slice(0, MAX_INLINE_IMAGES);
}

/**
 * One component for a cell from its first chunk to its final record. A live
 * cell and a finished one used to be different components under the same
 * key, so completion unmounted the card: open outputs and revisions
 * collapsed and the page jumped.
 */
function CellView({ cell }: { cell: NotebookCell }) {
  const live = !!(cell.live || cell.draft);
  const csvs = live ? [] : (cell.files_written || []).filter((f) => /\.(csv|tsv)$/i.test(f)).slice(0, 4);
  const images = live ? [] : writtenImages(cell);
  return (
    <CellShell cell={cell}>
      <CellCode cell={cell} live={live} />
      <CellStream cell={cell} stream="stdout" live={live} />
      <CellStream cell={cell} stream="stderr" live={live} />
      {cell.error ? <KernelExitNotice error={cell.error} /> : null}
      {cell.error ? <ErrorBlock raw={cell.error} /> : null}
      <CellFiguresSlot cell={cell} live={live} />
      {images.length ? <CellFigures cell={cell} names={images} /> : null}
      {csvs.map((f) => (
        <TableMount key={f} fname={f} cell={cell} />
      ))}
      {live ? null : <CellIo cell={cell} />}
      {live && cell.origin === "user" && !cell.draft ? <InterruptButton inCell /> : null}
      {live || cell.draft ? null : <CellActions cell={cell} />}
    </CellShell>
  );
}

/**
 * Interrupt the running REPL cell. It was a dark dot with no icon, text or
 * accessible name at the bottom of the panel -- below the fold once there
 * were a few cells -- beside a prominent "Stop" that stops the kernel.
 */
function InterruptButton({ inCell = false }: { inCell?: boolean }) {
  return (
    <div class={inCell ? "nbc-actions nbc-live-actions" : "nb-interrupt-slot"}>
      <button
        class="nb-interrupt"
        data-action={inCell ? "interrupt-cell" : "interrupt-repl"}
        title={nbT("nb.interrupt.title")}
        aria-label={nbT("nb.interrupt.title")}
        onClick={() => void interruptRepl()}
      >
        <span class="nb-interrupt-icon" aria-hidden="true">
          ■
        </span>
        {nbT("nb.interrupt.label")}
      </button>
    </div>
  );
}

function TableMount({ fname, cell }: { fname: string; cell: NotebookCell }) {
  const ref = useRef<HTMLDivElement>(null);
  const result = notebookArtifactState(cell, fname);
  const artifact = result.artifact;
  useLayoutEffect(() => {
    const host = ref.current;
    if (!host) return;
    host.replaceChildren();
    if (artifact) return renderTableInto(host, fname, artifact.url);
  }, [fname, artifact?.url]);
  return <div class="nbc-table-wrap">
    <div class="nbc-table-name">{fname}</div>
    <div ref={ref} />
    {artifact ? <OutputLinks artifact={artifact} /> : <div role="status">{filesT("nb.artifact." + result.state)}</div>}
  </div>;
}

const MemoCellView = memo(CellView);

function GenerationDivider() {
  return (
    <div class="nb-generation-divider" role="note">
      <span>{nbT("nb.generation.divider")}</span>
    </div>
  );
}

/** The kernel slot a cell ran in: R cells share one, every Python env the other. */
function cellSlot(cell: NotebookCell): string {
  return /^r\b/i.test(String(cell.language || cell.kernel_id || "python")) ? "r" : "python";
}

/** Paints the entries it is handed (the reading gate's list), never the cell stores. */
export function CellList({ entries }: { entries: NotebookCell[] }) {
  const filter = kernelFilter.value;
  const shown = filter
    ? entries.filter((e) => (e.kernel_id || "python") === filter)
    : entries;
  if (!shown.length) return <div class="dock-empty">{t("nb.empty")}</div>;
  // A cell that ran in a different kernel generation from the previous cell
  // of its language starts a fresh namespace: after a crash, a restart or an
  // environment switch. The NameError that follows used to be the only sign.
  const generations: Record<string, string> = {};
  const items: ComponentChildren[] = [];
  for (const cell of shown) {
    const key = nbCellKey(cell);
    const slot = cellSlot(cell);
    const generation = publicText(cell.generation_id, 96);
    const previous = generations[slot];
    if (generation) {
      if (previous && previous !== generation) items.push(<GenerationDivider key={"gen:" + key} />);
      generations[slot] = generation;
    }
    items.push(<MemoCellView key={key} cell={cell} />);
  }
  return <>{items}</>;
}

function toggleExecutedCodeLocal(): void {
  const fn = (globalThis as unknown as { toggleExecutedCode?: unknown }).toggleExecutedCode;
  if (isReady(fn)) {
    (fn as () => void)();
    return;
  }
  type ExecSt = {
    open: boolean;
    data: unknown;
    selected: unknown;
    cells: unknown;
    loading: boolean;
    error: string;
    request: number;
  };
  let st = execSources.value as ExecSt | null;
  if (!st) {
    st = {
      open: false,
      data: null,
      selected: null,
      cells: {},
      loading: false,
      error: "",
      request: 0,
    };
    execSources.value = st;
  }
  st.open = !st.open;
  execSources.value = st;
  nbRender();
}

export function KernelChips({ entries }: { entries: NotebookCell[] }) {
  const kernels: string[] = [];
  entries.forEach((e) => {
    const k = e.kernel_id || "python";
    if (!kernels.includes(k)) kernels.push(k);
  });
  const filter = kernelFilter.value;
  const badgeMode = runtimeBadge.value;
  // "Ended · view only" for a session whose kernel never ran was wrong; the
  // Timeline's runtime strip says the same words (runtimeStatusText).
  const badgeText = runtimeStatusText(badgeMode, currentKernelStatus());
  const execOpen = !!(execSources.value && (execSources.value as { open?: boolean }).open);
  return (
    <div class="kernel-chips">
      <button
        class={"kchip" + (filter == null ? " on" : "")}
        onClick={() => {
          kernelFilter.value = null;
          nbRender();
        }}
      >
        {t("nb.chips.all")}
      </button>
      {kernels.map((k) => (
        <button
          key={k}
          class={"kchip" + (filter === k ? " on" : "")}
          onClick={() => {
            kernelFilter.value = k;
            nbRender();
          }}
        >
          {kernelLabel(k)}
        </button>
      ))}
      <div class={"nb-live-badge " + badgeMode}>
        <span class="ld" />
        <span>{badgeText}</span>
      </div>
      {currentId.value ? <ExportMount frameId={currentId.value} /> : null}
      {currentId.value ? (
        <button class={"kchip nb-exec-toggle" + (execOpen ? " on" : "")} onClick={toggleExecutedCodeLocal}>
          {t("nb.exec.toggle")}
        </button>
      ) : null}
    </div>
  );
}

function ExportMount({ frameId }: { frameId: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const host = ref.current;
    if (!host) return;
    host.replaceChildren();
    host.appendChild(notebookExportLink(frameId));
  }, [frameId]);
  return <span ref={ref} />;
}

function OwnerChips() {
  const kinds = ["agent", "user_repl", "repair", "review_scratch"];
  return (
    <div class="nb-owners">
      {kinds.map((kind) => {
        const active = identityForOwner(executionQueue.value, kind);
        return (
          <span
            key={kind}
            class={"nb-owner-chip" + (active ? " active" : "")}
            title={active && active.execution_id ? active.execution_id : kind}
          >
            {nbLabelT("nb.owner." + kind)}
          </span>
        );
      })}
    </div>
  );
}

/** The kstate chip: paintKernel()'s text and class, rendered. */
function kernelStateLabel(st: KernelStatus): { text: string; cls: string } {
  const names: Record<string, string> = {
    running: t("nb.kernel.stateActive"),
    stopped: t("nb.kernel.stateStopped"),
    none: t("nb.kernel.stateNone"),
  };
  // After a crash the slot is "none" again but a generation ran: "exited",
  // not "not started".
  const label = st.turn_running
    ? t("dash.badge.running")
    : kernelExited(st)
      ? nbT("nb.kernel.stateExited")
      : names[st.state || ""] || st.state;
  return {
    text: String(label || "") + (st.generation ? t("nb.kernel.generation", st.generation) : ""),
    cls: "kstate " + (st.turn_running ? "run" : st.state || ""),
  };
}

function kernelTitle(st: KernelStatus): string {
  const env = st.env || {};
  return (
    kernelLabel(kernelIdFromEnv(env)) +
    " kernel · " +
    t("nb.kernel.shared") +
    (st.generation_id ? " · " + nbLabelT("nb.owner.generation", shortRuntime(st.generation_id)) : "") +
    (env.pending ? t("nb.kernel.pendingSwitch", env.pending) : "")
  );
}

/** A kernel ran and its process is gone (a crash): the next cell starts a new one. */
export function kernelExited(st: KernelStatus): boolean {
  return !st.alive && !st.turn_running && (st.state || "none") === "none" && !!st.generation_id;
}

export function kernelStatusLine(st: KernelStatus): { text: string; cls: string } {
  const env = st.env || {};
  const rt = kernelLabel(kernelIdFromEnv(env)) + (env.python_version ? " " + env.python_version : "");
  if (st.turn_running) return { text: t("nb.status.live", rt), cls: "live" };
  if (st.alive) return { text: t("nb.status.ready", rt), cls: "ready" };
  // A fresh session said "ended — view only" before any cell had run.
  if (st.state === "stopped") return { text: nbT("nb.status.stopped", rt), cls: "ended" };
  if (st.state === "ended") return { text: t("nb.status.ended", rt), cls: "ended" };
  if (st.generation_id) return { text: nbT("nb.status.exited", rt), cls: "ended" };
  return { text: nbT("nb.status.notStarted", rt), cls: "idle" };
}

/**
 * The read-only Notebook's status line and what it is. It never said that
 * manual cells exist or where to turn them on.
 */
export function StatusStrip() {
  const st = currentKernelStatus();
  const line = st ? kernelStatusLine(st) : null;
  return (
    <div class="nb-status">
      <div class={"nb-status-line" + (line ? " " + line.cls : "")}>{line ? line.text : "…"}</div>
      <div class="nb-status-hint">{t("nb.status.hint")}</div>
      <div class="nb-status-hint nb-readonly-hint">
        {nbT("nb.readonly.hint")}{" "}
        <button type="button" class="nbc-action" data-action="open-notebook-settings" onClick={() => void openCustomize("general")}>
          {nbT("nb.readonly.openSettings")}
        </button>
      </div>
    </div>
  );
}

function envOptionLabel(e: KernelEnvRow): string {
  const notable = e.notable && e.notable.length ? " — " + e.notable.slice(0, 4).join("/") : "";
  return e.name + (envLanguage(e) === "r" ? " · R" : "") + " · " + envSourceLabel(e) + notable;
}

/** Options from the session's last environment read; a pick shows until the next read answers. */
function EnvSelect() {
  const sid = currentId.value;
  const { envs, cur } = currentKernelEnvs();
  const choice = envChoice.value;
  const picked = choice && choice.sid === sid ? choice : null;
  return (
    <select
      class="nb-env-select"
      title={nbT("nb.env.selectTitle")}
      disabled={!sid || !!(picked && picked.posting)}
      value={picked ? picked.name : cur || undefined}
      onChange={(ev) => void nbSwitchEnv((ev.currentTarget as HTMLSelectElement).value)}
    >
      {envs ? (
        envs.map((e) => (
          <option key={e.name} value={e.name} disabled={!envSelectable(e)} title={e.description || ""}>
            {envOptionLabel(e)}
          </option>
        ))
      ) : (
        <option>{t("nb.env.placeholder")}</option>
      )}
    </select>
  );
}

function readEditor(box: HTMLTextAreaElement): EditorState {
  return { value: box.value, start: box.selectionStart, end: box.selectionEnd };
}

/** The last `replLoad` a panel applied: a remount must not re-apply an old one. */
let appliedReplLoad = 0;

function ReplPanel() {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const history = useRef(new ReplHistory());
  // Escape releases the next Tab to move focus, so the box is not a trap.
  const tabReleased = useRef(false);
  const load = replLoad.value;
  useLayoutEffect(() => {
    const box = inputRef.current;
    if (!box || !load || load.seq === appliedReplLoad) return;
    appliedReplLoad = load.seq;
    box.value = load.code;
    history.current.reset();
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
    if (typeof box.scrollIntoView === "function") box.scrollIntoView({ block: "nearest" });
  }, [load]);
  const sid = currentId.value;
  const st = currentKernelStatus();
  const state = !sid
    ? { text: t("nb.kernel.noSession"), cls: "kstate" }
    : st
      ? kernelStateLabel(st)
      : { text: "…", cls: "kstate" };
  const quarantined = !!(st && st.view_only === true && st.trust_state === "quarantined");
  const reviveHidden = !st || !!(st.alive || st.turn_running || quarantined);
  const busy = replBusy.value;
  const pendingAction = kernelAction.value && kernelAction.value.sid === sid ? kernelAction.value.action : "";
  const shownState = pendingAction ? { text: nbT("nb.kernel.action." + pendingAction), cls: "kstate run" } : state;
  const lang = _replLanguage.value === "r" ? "r" : "python";
  const drafts = _replDrafts.value || { python: "", r: "" };
  return (
    <div class="nb-repl">
      <div class="nb-repl-head">
        <span class="nb-kernel-title">{st ? kernelTitle(st) : "kernel"}</span>
        <div class="nb-repl-actions">
          <EnvSelect />
          <span class={shownState.cls}>{shownState.text}</span>
          <button class="kchip" disabled={!sid || !!pendingAction} title={t("nb.kernel.stopTitle")} onClick={() => void kernelCtl("stop")}>
            {t("nb.kernel.stopLabel")}
          </button>
          <button class="kchip" disabled={!sid || !!pendingAction} title={t("nb.kernel.startTitle")} onClick={() => void kernelCtl("start")}>
            {t("nb.kernel.startLabel")}
          </button>
          <button class="kchip" disabled={!sid || !!pendingAction} title={t("nb.kernel.restartTitle")} onClick={() => void kernelCtl("restart")}>
            {t("nb.kernel.restartLabel")}
          </button>
        </div>
      </div>
      <div
        class={"nb-revive" + (reviveHidden ? " hidden" : "")}
        title={quarantined ? t("runtime.quarantineHint") : ""}
      >
        <span>{t("nb.revive.text")}</span>
        <button class="solid-btn small" onClick={() => void kernelCtl("start")}>
          {t("nb.revive.startBtn")}
        </button>
      </div>
      <div class="nb-repl-body">
        {t("nb.repl.multilineHint")}
        <span class="nb-repl-keys">{nbT("nb.repl.keys")}</span>
      </div>
      <div class="nb-live-input">
        <div class="nb-live-input-bar">
          <label class="nb-language-label">
            {t("nb.repl.language")}
            <select
              class="nb-language-select"
              disabled={busy}
              value={lang}
              onChange={(ev) => {
                const next = (ev.currentTarget as HTMLSelectElement).value === "r" ? "r" : "python";
                const box = inputRef.current;
                drafts[lang] = box ? box.value : drafts[lang] || "";
                _replLanguage.value = next;
                if (box) {
                  box.value = drafts[next] || "";
                  box.placeholder = next === "r" ? "# R" : t("nb.repl.inputPlaceholder");
                  box.focus();
                }
              }}
            >
              <option value="python">Python</option>
              <option value="r">R</option>
            </select>
          </label>
          <div class="nb-live-input-actions">
            <button
              class="solid-btn small"
              disabled={busy || !sid}
              onClick={() => void runDraft()}
            >
              {t("nb.repl.run")}
            </button>
            {busy ? <InterruptButton /> : null}
          </div>
        </div>
        <textarea
          class="nb-repl-input"
          ref={inputRef}
          rows={3}
          spellcheck={false}
          placeholder={t("nb.repl.inputPlaceholder")}
          disabled={!sid || busy}
          defaultValue={drafts[lang] || ""}
          onInput={(ev) => {
            history.current.reset();
            drafts[_replLanguage.value] = (ev.currentTarget as HTMLTextAreaElement).value;
          }}
          onKeyDown={(event) => {
            if (event.isComposing || event.keyCode === 229) return;
            const box = event.currentTarget as HTMLTextAreaElement;
            const plain = !event.altKey && !event.ctrlKey && !event.metaKey;
            if (event.key === "Enter" && (event.shiftKey || event.ctrlKey || event.metaKey)) {
              event.preventDefault();
              void runDraft();
              return;
            }
            if (event.key === "Escape") {
              tabReleased.current = true;
              return;
            }
            if (event.key === "Tab" && plain) {
              if (tabReleased.current) {
                tabReleased.current = false;
                return;
              }
              event.preventDefault();
              const next = indent(readEditor(box), event.shiftKey);
              box.value = next.value;
              box.setSelectionRange(next.start, next.end);
              drafts[_replLanguage.value] = next.value;
              return;
            }
            tabReleased.current = false;
            const language = _replLanguage.value === "r" ? "r" : "python";
            let recalled: string | null = null;
            if (event.key === "ArrowUp" && plain && !event.shiftKey && caretOnFirstLine(readEditor(box))) {
              recalled = history.current.older(replHistory(language), box.value);
            } else if (event.key === "ArrowDown" && plain && !event.shiftKey && caretOnLastLine(readEditor(box))) {
              recalled = history.current.newer(replHistory(language));
            }
            if (recalled != null) {
              event.preventDefault();
              box.value = recalled;
              box.setSelectionRange(recalled.length, recalled.length);
              drafts[language] = recalled;
            }
          }}
        />
      </div>
    </div>
  );

  async function runDraft(): Promise<void> {
    const box = inputRef.current;
    const currentLanguage = _replLanguage.value === "r" ? "r" : "python";
    const code = box ? box.value : drafts[currentLanguage] || "";
    const ok = await executeNotebookCode(code, currentLanguage);
    if (ok) {
      drafts[currentLanguage] = "";
      if (box) box.value = "";
      history.current.reset();
    }
    requestAnimationFrame(() => {
      if (box) box.focus();
      // The new cell is the last one: bring it up against the input.
      const dockBody = document.getElementById("dock-notebook")?.parentElement;
      if (ok && dockBody) dockBody.scrollTop = dockBody.scrollHeight;
    });
  }
}

function ExecutedCodeSlot() {
  const st = execSources.value;
  const hostRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    host.replaceChildren();
    const fn = (globalThis as unknown as { buildExecutedCodeView?: unknown }).buildExecutedCodeView;
    if (isReady(fn) && st) {
      host.appendChild((fn as (s: unknown) => HTMLElement)(st));
    }
  }, [st]);
  return <div ref={hostRef} />;
}

/**
 * Runs the kernel reads after a dock render (kernel.ts `syncKernel`). It
 * reads the cache, the session and the tab so an invalidation, a session
 * switch or the Notebook coming into view re-runs them.
 */
function KernelSync({ envs }: { envs: boolean }) {
  _kc.value;
  currentId.value;
  activeTab.value;
  dock.value;
  useLayoutEffect(() => {
    syncKernel(envs);
  });
  return null;
}

/**
 * NB-2: the conversation's jump-to-latest, for the Notebook. It reads the
 * cell stores itself, so it appears while the list below is being held.
 */
export function NewCellsPill() {
  const count = running.value ? unseenNotebookCells() : 0;
  if (!count) return null;
  return (
    <button
      type="button"
      class="nb-new-cells"
      onClick={() => {
        const nb = document.getElementById("dock-notebook");
        const body = nb ? (nb.parentElement as unknown as ScrollBox | null) : null;
        if (!body) return;
        body.scrollTop = body.scrollHeight;
        // Resumes the held list now, without waiting for the scroll event.
        onNotebookScroll(body);
      }}
    >
      {nbT(count === 1 ? "nb.newCells.one" : "nb.newCells", count)}
    </button>
  );
}

export function NotebookDock({ entries }: { entries: NotebookCell[] }) {
  const execOpen = !!(execSources.value && (execSources.value as { open?: boolean }).open);
  const repl = replEnabled.value;
  return (
    <>
      <KernelSync envs={repl && !execOpen} />
      <KernelChips entries={entries} />
      {execOpen ? (
        <ExecutedCodeSlot />
      ) : (
        <>
          <CellList entries={entries} />
          <OwnerChips />
          {repl ? (
            // NB-3: the input used to be the last thing in a 35,000 px panel.
            <div class="nb-foot">
              <NewCellsPill />
              <ReplPanel />
            </div>
          ) : (
            <>
              <StatusStrip />
              <NewCellsPill />
            </>
          )}
        </>
      )}
    </>
  );
}

/**
 * app.js:10333-10479 without `innerHTML=""` of the cell list. Every caller
 * (nbRender, the Timeline's refreshes, a tab switch) paints the list the
 * reading gate allows, computed once for the whole dock.
 */
export function renderNotebook(): void {
  if (typeof document === "undefined") return;
  const nb = document.getElementById("dock-notebook");
  if (!nb) return;
  const body = nb.parentElement as unknown as ScrollBox | null;
  const follow = measureNotebookFollow(body);
  bindNotebookScroll(body);
  render(<NotebookDock entries={notebookViewEntries()} />, nb);
  // Following, or idle: everything painted is something the reader can see.
  if (follow || !running.value) markNotebookSeen();
  followLiveOutput(body, follow);
}

setNotebookRenderImpl(renderNotebook);

/** app.js:10567-10621 — imperative cell for F-16 executed-code view. */
export function cellNode(e: NotebookCell): HTMLElement {
  const k = e.kernel_id || "python";
  const c = el("div", "notebook-cell" + (e.live ? " live" : "") + (e.draft ? " draft" : ""));
  c.setAttribute("data-cell", e.cell_index != null ? String(e.cell_index) : "");
  c.setAttribute("data-kernel", k);
  c.setAttribute("data-producing-cell", e.producing_cell_id || "");
  const st = e.status || (e.live ? "running" : "ok");
  const idx = e.cell_index != null ? e.cell_index : "…";
  const cellState = notebookCellState(e);
  const cellMeta = el("div", "nbc-cell-meta");
  cellMeta.appendChild(el("span", "nbc-state " + cellState.cls, nbLabelT("nb.cell." + cellState.key)));
  c.appendChild(cellMeta);
  const wrap = el("div", "os-code");
  const head = el("div", "oc-head");
  const lg = el("span", "oc-lang");
  lg.appendChild(el("span", null, (e.language || k) + " [" + idx + "]"));
  head.appendChild(lg);
  const right = el("div", "oc-right");
  right.appendChild(el("span", "nbc-status " + st, cellStatusLabel(String(st))));
  head.appendChild(right);
  wrap.appendChild(head);
  const pre = el("pre", "oc-src");
  const code = el("code");
  code.innerHTML = highlightCellSource(nbCellKey(e), e.source || "", e.language || k);
  pre.appendChild(code);
  wrap.appendChild(pre);
  c.appendChild(wrap);
  if (e.stdout) {
    const details = el("details", "nbc-disclosure") as HTMLDetailsElement;
    details.appendChild(el("summary", null, outputLabel(e.stdout, false, false)));
    const out = el("pre", "nbc-out");
    out.textContent = e.stdout;
    details.appendChild(out);
    details.open = !outputIsLong(e.stdout);
    c.appendChild(details);
  }
  if (e.stderr) {
    const details = el("details", "nbc-disclosure error") as HTMLDetailsElement;
    details.appendChild(el("summary", null, outputLabel(e.stderr, true, false)));
    const out = el("pre", "nbc-err");
    out.textContent = e.stderr;
    details.appendChild(out);
    details.open = !outputIsLong(e.stderr);
    c.appendChild(details);
  }
  if (e.error) {
    const txt = stripAnsi(e.error).replace(/\s+$/, "");
    const box = el("div", "nbc-error open");
    const headEl = el("div", "nbc-error-head");
    headEl.appendChild(el("span", "nbc-err-text", txt.split("\n").filter((l) => l.trim()).pop() || t("nb.error.default")));
    box.appendChild(headEl);
    const tb = el("pre", "nbc-error-tb");
    tb.innerHTML = highlightTraceback(txt);
    box.appendChild(tb);
    c.appendChild(box);
  }
  return c;
}
