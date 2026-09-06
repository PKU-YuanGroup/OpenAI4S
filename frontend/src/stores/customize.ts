import { field } from "./signal-field";

/** S.models — app.js:120 */
export const models = field(() => [] as unknown[]);
/** S.defaultModel — app.js:120 */
export const defaultModel = field(() => null as unknown);
/** S.skillsCatalog — app.js:120 */
export const skillsCatalog = field(() => null as unknown);
/** S.environmentStatus — app.js:128 */
export const environmentStatus = field(() => null as unknown);
/** S.standardProfileReadiness — app.js:128 */
export const standardProfileReadiness = field(() => null as unknown);
/** S._environmentStatusPromise — app.js:128 */
export const _environmentStatusPromise = field(() => null as unknown);
/** S._environmentStatusRefreshFailed — app.js:128 */
export const _environmentStatusRefreshFailed = field(() => false);
/** S.defaultModelName — app.js:8344 */
export const defaultModelName = field(() => null as unknown);

export const customizeSignals = {
  models,
  defaultModel,
  skillsCatalog,
  environmentStatus,
  standardProfileReadiness,
  _environmentStatusPromise,
  _environmentStatusRefreshFailed,
  defaultModelName,
};
