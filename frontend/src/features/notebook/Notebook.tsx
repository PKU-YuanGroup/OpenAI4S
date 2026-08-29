/**
 * Notebook dock: kernel chips / REPL header rendered apart from the cell list.
 * CellList is keyed by producing_cell_id; live chunks append a text node;
 * completed cells are memoized. Replaces app.js:10333-10630 `innerHTML=""` rebuild.
 */

import { render } from "preact";
import type { ComponentChildren } from "preact";
import { memo } from "preact/compat";
import { useLayoutEffect, useRef } from "preact/hooks";
import { isReady } from "../../compat/stub";
import { t } from "../../i18n/runtime";
import {
  _kc,
  _replDrafts,
  _replLanguage,
  execSources,
  kernelFilter,
  pendingReplIdentity,
} from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { running } from "../../stores/stream";
import { executionQueue } from "../../stores/timeline";
import { publicText } from "../scrub/scrub";
import { appendTextNodeDelta, cellOutput, nbCellKey, notebookDisplayEntries } from "./cells";
import {
  artUrlByName,
  el,
  highlightCellSource,
  highlightTraceback,
  looksBinary,
  notebookExportLink,
  renderTableInto,
  stripAnsi,
} from "./chrome";
import {
  branchCapability,
  copyNotebookCell,
  executeNotebookCode,
  forkNotebookCell,
  identityForOwner,
  interruptRepl,
  kernelCtl,
  kernelEpoch,
  kernelLabel,
  kernelStatusOf,
  nbPopulateEnvSelect,
  nbSwitchEnv,
  promoteNotebookCell,
  refreshKernelState,
  replEnabledNow,
  runtimeSummary,
} from "./kernel";
import {
  bindNotebookScroll,
  followLiveOutput,
  measureNotebookFollow,
  nbRender,
  setNotebookRenderImpl,
} from "./scroll";
import type { NotebookCell, ScrollBox } from "./types";

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

function StreamingOutput({ text, isError }: { text: string; isError: boolean }) {
  const preRef = useRef<HTMLPreElement>(null);
  const seen = useRef(0);
  useLayoutEffect(() => {
    const pre = preRef.current;
    if (!pre) return;
    if (!pre.firstChild) pre.appendChild(document.createTextNode(""));
    const node = pre.firstChild as Text;
    seen.current = appendTextNodeDelta(node, seen.current, text);
  }, [text]);
  if (!text) return null;
  if (looksBinary(text)) {
    return <div class="bin-elide" />;
  }
  return (
    <details class={"nbc-disclosure" + (isError ? " error" : "")}>
      <summary>output</summary>
      <pre ref={preRef} class={isError ? "nbc-err" : "nbc-out"} />
    </details>
  );
}

function StaticOutput({ text, isError }: { text: string; isError: boolean }) {
  if (!text) return null;
  if (looksBinary(text)) {
    return <div class="bin-elide" />;
  }
  return (
    <details class={"nbc-disclosure" + (isError ? " error" : "")}>
      <summary>output</summary>
      <pre class={isError ? "nbc-err" : "nbc-out"}>{text}</pre>
    </details>
  );
}

function LiveStdout({ cellKey }: { cellKey: string }) {
  return <StreamingOutput text={cellOutput(cellKey).stdout.value} isError={false} />;
}

function LiveStderr({ cellKey }: { cellKey: string }) {
  return <StreamingOutput text={cellOutput(cellKey).stderr.value} isError={true} />;
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
          {opts.status ? <span class={"nbc-status " + opts.status}>{opts.status}</span> : null}
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

function CellFigures({ names }: { names: string[] }) {
  if (!names.length) return null;
  return (
    <>
      {names.map((f) => (
        <img
          key={f}
          class="nbc-fig"
          src={artUrlByName(f)}
          onError={(ev) => (ev.currentTarget as HTMLImageElement).remove()}
        />
      ))}
    </>
  );
}

function CellIo({ cell }: { cell: NotebookCell }) {
  const written = cell.files_written || [];
  const read = cell.files_read || [];
  if (!written.length && !read.length) return null;
  return (
    <div class="nbc-io">
      {written.map((f) => (
        <span key={"w:" + f} class="io-w">
          <span>{f}</span>
        </span>
      ))}
      {read.map((f) => (
        <span key={"r:" + f} class="io-r">
          <span>{f}</span>
        </span>
      ))}
    </div>
  );
}

function CellActions({ cell }: { cell: NotebookCell }) {
  const st = kernelStatusOf(_kc.value.st);
  const replEnabled = !!st.repl_enabled;
  const appendable = replEnabled && !cell.live && !!String(cell.source || "").trim();
  const canFork =
    !cell.live && branchCapability("fork_from_cell") && !!publicText(cell.fork_checkpoint_id, 96);
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
      {canFork ? (
        <button class="nbc-action" onClick={() => void forkNotebookCell(cell)}>
          {t("nb.action.fork")}
        </button>
      ) : null}
      <button
        class="nbc-action"
        disabled={!branchCapability("promote")}
        onClick={() => void promoteNotebookCell(cell)}
      >
        {t("nb.action.promote")}
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
              <MemoCompletedCell
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
          {t("nb.cell." + cellState.key)}
        </span>
        {cell.state_revision != null ? <span class="nbc-revision">{"S" + cell.state_revision}</span> : null}
      </div>
      {children}
    </div>
  );
}

function LiveCode({ cell }: { cell: NotebookCell }) {
  const rec = cellOutput(nbCellKey(cell));
  const source = rec.source.value;
  const status = rec.status.value || "running";
  const k = cell.kernel_id || "python";
  const idx = cell.cell_index != null ? cell.cell_index : "…";
  return (
    <CodeBlock
      cacheKey={nbCellKey(cell)}
      source={source}
      lang={cell.language || k}
      langLabel={(cell.language || k) + " [" + idx + "]"}
      status={status}
      env={cell.environment || cell.env}
    />
  );
}

function LiveFigures({ cellKey }: { cellKey: string }) {
  return <CellFigures names={cellOutput(cellKey).figures.value} />;
}

function LiveCell({ cell }: { cell: NotebookCell }) {
  const key = nbCellKey(cell);
  return (
    <CellShell cell={cell}>
      <LiveCode cell={cell} />
      <LiveStdout cellKey={key} />
      <LiveStderr cellKey={key} />
      {cell.error ? <ErrorBlock raw={cell.error} /> : null}
      <LiveFigures cellKey={key} />
    </CellShell>
  );
}

function CompletedCell({ cell }: { cell: NotebookCell }) {
  const k = cell.kernel_id || "python";
  const st = cell.status || "ok";
  const idx = cell.cell_index != null ? cell.cell_index : "…";
  const csvs = (cell.files_written || []).filter((f) => /\.(csv|tsv)$/i.test(f)).slice(0, 4);
  return (
    <CellShell cell={cell}>
      <CodeBlock
        cacheKey={nbCellKey(cell)}
        source={cell.source || ""}
        lang={cell.language || k}
        langLabel={(cell.language || k) + " [" + idx + "]"}
        status={st}
        env={cell.environment || cell.env}
      />
      <StaticOutput text={cell.stdout || ""} isError={false} />
      <StaticOutput text={cell.stderr || ""} isError={true} />
      {cell.error ? <ErrorBlock raw={cell.error} /> : null}
      <CellFigures names={cell.figures || []} />
      {csvs.map((f) => (
        <TableMount key={f} fname={f} />
      ))}
      <CellIo cell={cell} />
      {cell.draft ? null : <CellActions cell={cell} />}
    </CellShell>
  );
}

function TableMount({ fname }: { fname: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const host = ref.current;
    if (!host) return;
    host.replaceChildren();
    const name = el("div", "nbc-table-name", fname.split("/").pop() || fname);
    host.appendChild(name);
    renderTableInto(host, fname);
  }, [fname]);
  return <div class="nbc-table-wrap" ref={ref} />;
}

const MemoCompletedCell = memo(CompletedCell);

function CellList() {
  const entries = notebookDisplayEntries();
  const filter = kernelFilter.value;
  const shown = filter
    ? entries.filter((e) => (e.kernel_id || "python") === filter)
    : entries;
  if (!shown.length) return <div class="dock-empty">{t("nb.empty")}</div>;
  return (
    <>
      {shown.map((cell) => {
        const key = nbCellKey(cell);
        if (cell.live || cell.draft) return <LiveCell key={key} cell={cell} />;
        return <MemoCompletedCell key={key} cell={cell} />;
      })}
    </>
  );
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

function KernelChips() {
  kernelEpoch.value;
  const entries = notebookDisplayEntries();
  const kernels: string[] = [];
  entries.forEach((e) => {
    const k = e.kernel_id || "python";
    if (!kernels.includes(k)) kernels.push(k);
  });
  const filter = kernelFilter.value;
  const kc = _kc.value;
  const cachedRunning = !!(running.value || (kc.id === currentId.value && kernelStatusOf(kc.st).turn_running));
  const cachedReady = !cachedRunning && !!(kc.id === currentId.value && kernelStatusOf(kc.st).alive);
  const runtimeMode = runtimeSummary().status;
  const badgeMode = runtimeMode || (cachedRunning ? "busy" : cachedReady ? "live" : "ended");
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
        <span>{t("runtime.status." + badgeMode)}</span>
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
            {t("nb.owner." + kind)}
          </span>
        );
      })}
    </div>
  );
}

function StatusStrip() {
  kernelEpoch.value;
  const lineRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const line = lineRef.current;
    void refreshKernelState({ strip: { line: line || undefined } });
  });
  return (
    <div class="nb-status">
      <div class="nb-status-line" ref={lineRef}>
        …
      </div>
      <div class="nb-status-hint">{t("nb.status.hint")}</div>
    </div>
  );
}

function ReplPanel() {
  kernelEpoch.value;
  const envRef = useRef<HTMLSelectElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const runRef = useRef<HTMLButtonElement>(null);
  const stopRef = useRef<HTMLButtonElement>(null);
  const titleRef = useRef<HTMLSpanElement>(null);
  const stateRef = useRef<HTMLSpanElement>(null);
  const reviveRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    void refreshKernelState({
      state: stateRef.current || undefined,
      title: titleRef.current || undefined,
      revive: reviveRef.current || undefined,
    });
    void nbPopulateEnvSelect(envRef.current);
  });
  const pending =
    pendingReplIdentity.value &&
    (pendingReplIdentity.value as { frame_id?: string }).frame_id === currentId.value
      ? pendingReplIdentity.value
      : null;
  const replIdentity = pending || identityForOwner(executionQueue.value, "user_repl");
  const replBusy = !!replIdentity;
  const lang = _replLanguage.value === "r" ? "r" : "python";
  const drafts = _replDrafts.value || { python: "", r: "" };
  return (
    <div class="nb-repl">
      <div class="nb-repl-head">
        <span class="nb-kernel-title" ref={titleRef}>
          kernel
        </span>
        <div class="nb-repl-actions">
          <select
            class="nb-env-select"
            ref={envRef}
            title={t("nb.env.selectTitle")}
            disabled={!currentId.value}
            onChange={(ev) => void nbSwitchEnv((ev.currentTarget as HTMLSelectElement).value, ev.currentTarget as HTMLSelectElement)}
          >
            <option>{t("nb.env.placeholder")}</option>
          </select>
          <span class="kstate" ref={stateRef}>
            …
          </span>
          <button class="kchip" disabled={!currentId.value} onClick={() => void kernelCtl("stop")}>
            {t("nb.kernel.stopLabel")}
          </button>
          <button class="kchip" disabled={!currentId.value} onClick={() => void kernelCtl("start")}>
            {t("nb.kernel.startLabel")}
          </button>
          <button class="kchip" disabled={!currentId.value} onClick={() => void kernelCtl("restart")}>
            {t("nb.kernel.restartLabel")}
          </button>
        </div>
      </div>
      <div class="nb-revive hidden" ref={reviveRef}>
        <span>{t("nb.revive.text")}</span>
        <button class="solid-btn small" onClick={() => void kernelCtl("start")}>
          {t("nb.revive.startBtn")}
        </button>
      </div>
      <div class="nb-repl-body">{t("nb.repl.multilineHint")}</div>
      <div class="nb-live-input">
        <div class="nb-live-input-bar">
          <label class="nb-language-label">
            {t("nb.repl.language")}
            <select
              class="nb-language-select"
              disabled={replBusy}
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
              ref={runRef}
              disabled={replBusy || !currentId.value}
              onClick={() => void runDraft()}
            >
              {t("nb.repl.run")}
            </button>
            <button
              class={"repl-stop" + (replBusy ? "" : " hidden")}
              ref={stopRef}
              title={t("nb.repl.interruptTitle")}
              onClick={() => void interruptRepl()}
            />
          </div>
        </div>
        <textarea
          class="nb-repl-input"
          ref={inputRef}
          rows={7}
          spellcheck={false}
          placeholder={t("nb.repl.inputPlaceholder")}
          disabled={!currentId.value || replBusy}
          defaultValue={drafts[lang] || ""}
          onInput={(ev) => {
            drafts[_replLanguage.value] = (ev.currentTarget as HTMLTextAreaElement).value;
          }}
          onKeyDown={(event) => {
            if (event.isComposing || event.keyCode === 229) return;
            if (event.key === "Enter" && event.shiftKey) {
              event.preventDefault();
              void runDraft();
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
    const ok = await executeNotebookCode(code, currentLanguage, {
      runButton: runRef.current || undefined,
      input: box || undefined,
      stop: stopRef.current || undefined,
    });
    if (ok) {
      drafts[currentLanguage] = "";
      if (box) box.value = "";
    }
    requestAnimationFrame(() => box && box.focus());
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

export function NotebookDock() {
  kernelEpoch.value;
  const execOpen = !!(execSources.value && (execSources.value as { open?: boolean }).open);
  const repl = replEnabledNow();
  return (
    <>
      <KernelChips />
      {execOpen ? (
        <ExecutedCodeSlot />
      ) : (
        <>
          <CellList />
          <OwnerChips />
          {repl ? <ReplPanel /> : <StatusStrip />}
        </>
      )}
    </>
  );
}

/** app.js:10333-10479 without `innerHTML=""` of the cell list. */
export function renderNotebook(): void {
  if (typeof document === "undefined") return;
  const nb = document.getElementById("dock-notebook");
  if (!nb) return;
  const body = nb.parentElement as unknown as ScrollBox | null;
  const follow = measureNotebookFollow(body);
  bindNotebookScroll(body);
  render(<NotebookDock />, nb);
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
  cellMeta.appendChild(el("span", "nbc-state " + cellState.cls, t("nb.cell." + cellState.key)));
  c.appendChild(cellMeta);
  const wrap = el("div", "os-code");
  const head = el("div", "oc-head");
  const lg = el("span", "oc-lang");
  lg.appendChild(el("span", null, (e.language || k) + " [" + idx + "]"));
  head.appendChild(lg);
  const right = el("div", "oc-right");
  right.appendChild(el("span", "nbc-status " + st, String(st)));
  head.appendChild(right);
  wrap.appendChild(head);
  const pre = el("pre", "oc-src");
  const code = el("code");
  code.innerHTML = highlightCellSource(nbCellKey(e), e.source || "", e.language || k);
  pre.appendChild(code);
  wrap.appendChild(pre);
  c.appendChild(wrap);
  if (e.stdout) {
    const details = el("details", "nbc-disclosure");
    details.appendChild(el("summary", null, "output"));
    const out = el("pre", "nbc-out");
    out.textContent = e.stdout;
    details.appendChild(out);
    c.appendChild(details);
  }
  if (e.stderr) {
    const details = el("details", "nbc-disclosure error");
    details.appendChild(el("summary", null, "output"));
    const out = el("pre", "nbc-err");
    out.textContent = e.stderr;
    details.appendChild(out);
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

export function scrollToCell(idx: number | string, kernel?: string | null): void {
  kernelFilter.value = kernel || null;
  renderNotebook();
  requestAnimationFrame(() => {
    const root = document.getElementById("dock-notebook");
    if (!root) return;
    const node = (kernel &&
      root.querySelector(`.notebook-cell[data-cell="${idx}"][data-kernel="${kernel}"]`)) ||
      root.querySelector(`.notebook-cell[data-cell="${idx}"]`);
    if (node) {
      node.scrollIntoView({ behavior: "smooth", block: "center" });
      node.classList.add("flash");
      setTimeout(() => node.classList.remove("flash"), 1600);
    }
  });
}
