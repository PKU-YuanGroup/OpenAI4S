import { field } from "./signal-field";

/** S.artifacts — app.js:120 */
export const artifacts = field(() => [] as unknown[]);
/** S.dockArtifact — app.js:120 */
export const dockArtifact = field(() => null as unknown);
/** S.filesScope — app.js:120 */
export const filesScope = field(() => "frame" as string);
/** S.projectArtifacts — app.js:120 */
export const projectArtifacts = field(() => [] as unknown[]);
/** S._projArtFor — app.js:120 */
export const _projArtFor = field(() => null as string | null);
/** S.rendererCatalog — app.js:121 */
export const rendererCatalog = field(() => null as unknown);
/** S._rendererCatalogPromise — app.js:121 */
export const _rendererCatalogPromise = field(() => null as unknown);
/** S.rendererDescriptors — app.js:121; mutated in place */
export const rendererDescriptors = field(() => Object.create(null) as Record<string, unknown>);
/** S._artBust — app.js:5323; nested writes `(S._artBust = S._artBust || {})[aid] = …` */
export const _artBust = field(() => Object.create(null) as Record<string, unknown>);
/** S._tbl — app.js:5334 / 7147; nested `delete S._tbl[k]` */
export const _tbl = field(() => Object.create(null) as Record<string, unknown>);
/** S._editing — app.js:7158 */
export const _editing = field(() => null as unknown);
/** S._computeLostSeen — app.js:8244; mutated in place */
export const _computeLostSeen = field(() => Object.create(null) as Record<string, unknown>);
/** S._artVer — app.js:8355; mutated in place */
export const _artVer = field(() => Object.create(null) as Record<string, unknown>);
/** S._envSnapById — app.js:8376; nested delete */
export const _envSnapById = field(() => Object.create(null) as Record<string, unknown>);
/** S._artifactLoadReq — app.js:8381 */
export const _artifactLoadReq = field(() => 0);
/** S._thumbCache — app.js:8429; mutated in place */
export const _thumbCache = field(() => Object.create(null) as Record<string, unknown>);
/** S._editorAC — app.js:9484 */
export const _editorAC = field(() => null as unknown);
/** S._molView — app.js:9615 */
export const _molView = field(() => null as unknown);
/** S._molViewer — app.js:9613 */
export const _molViewer = field(() => null as unknown);

export const artifactsSignals = {
  artifacts,
  dockArtifact,
  filesScope,
  projectArtifacts,
  _projArtFor,
  rendererCatalog,
  _rendererCatalogPromise,
  rendererDescriptors,
  _artBust,
  _tbl,
  _editing,
  _computeLostSeen,
  _artVer,
  _envSnapById,
  _artifactLoadReq,
  _thumbCache,
  _editorAC,
  _molView,
  _molViewer,
};
