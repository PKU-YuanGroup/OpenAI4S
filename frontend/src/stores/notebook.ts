import { field } from "./signal-field";

/** S.cells — app.js:120 */
export const cells = field(() => [] as unknown[]);
/** S.kernels — app.js:120 */
export const kernels = field(() => [] as unknown[]);
/** S.liveCells — app.js:120 */
export const liveCells = field(() => [] as unknown[]);
/** S._liveCell — app.js:120 */
export const _liveCell = field(() => null as unknown);
/** S.kernelFilter — app.js:120 */
export const kernelFilter = field(() => null as unknown);
/** S.variableInspector — app.js:131; nested `.request` / `.results` writes */
export const variableInspector = field(() => ({
  language: "python" as string,
  results: Object.create(null) as Record<string, unknown>,
  loading: null as string | null,
  error: "",
  request: 0,
}));
/** S.pendingReplIdentity — app.js:2993 */
export const pendingReplIdentity = field(() => null as unknown);
/** S.execSources — app.js:7141; mutated in place by execSourcesState() */
export const execSources = field(() => null as unknown);
/** S.lineage — app.js:7148 */
export const lineage = field(() => null as unknown);
/** S._lineageFor — app.js:7148 */
export const _lineageFor = field(() => null as unknown);
/** S._lineageReq — app.js:8374 */
export const _lineageReq = field(() => 0);
/** S.artifactWorkbench — app.js:8710 / 10009 */
export const artifactWorkbench = field(() => false);
/** S._executionLoadReq — app.js:9747 */
export const _executionLoadReq = field(() => 0);
/** S._nbDirty — app.js:9906 */
export const _nbDirty = field(() => false);
/** S._nbReading — app.js:9906 */
export const _nbReading = field(() => false);
/** S._nbSched — app.js:9907 */
export const _nbSched = field(() => false);
/** S._replDraft — app.js:10430 */
export const _replDraft = field(() => "");
/** S._replDrafts — app.js:10430; mutated in place */
export const _replDrafts = field(() => ({ python: "", r: "" }) as Record<string, string>);
/** S._replLanguage — app.js:10431 */
export const _replLanguage = field(() => "python" as "python" | "r");

/**
 * Module-level `_kc` at app.js:9954 (not an `S` field). F-14 invalidates this
 * cache on kernel_status / turnDone / nbSwitchEnv.
 */
export type KernelCache = {
  id: string | null;
  st: unknown;
  stAt: number;
  stBusy: boolean;
  envs: unknown;
  cur: unknown;
  envAt: number;
  envBusy: boolean;
};

export const _kc = field<KernelCache>(() => ({
  id: null,
  st: null,
  stAt: 0,
  stBusy: false,
  envs: null,
  cur: null,
  envAt: 0,
  envBusy: false,
}));

export const notebookSignals = {
  cells,
  kernels,
  liveCells,
  _liveCell,
  kernelFilter,
  variableInspector,
  pendingReplIdentity,
  execSources,
  lineage,
  _lineageFor,
  _lineageReq,
  artifactWorkbench,
  _executionLoadReq,
  _nbDirty,
  _nbReading,
  _nbSched,
  _replDraft,
  _replDrafts,
  _replLanguage,
};
