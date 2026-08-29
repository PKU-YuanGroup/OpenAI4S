import { field } from "./signal-field";

/** S.dock — app.js:120; nested `S.dock.open` writes */
export const dock = field(() => ({ open: false, tab: "notebook" }));
/** S.openTabs — app.js:120 */
export const openTabs = field(() => [] as unknown[]);
/** S.activeTab — app.js:120 */
export const activeTab = field(() => "notebook");
/** S.provMode — app.js:120 */
export const provMode = field(() => false);
/** S.provSub — app.js:120 */
export const provSub = field(() => "code");
/** S._menu — app.js:120 */
export const _menu = field(() => null as unknown);
/** S._dashPoll — app.js:6755 */
export const _dashPoll = field(() => null as unknown);
/** S._modalMode — app.js:6790 */
export const _modalMode = field(() => null as unknown);
/** S._jobPoll — app.js:11789 */
export const _jobPoll = field(() => null as unknown);
/** S._messagesFollow — app.js:12938; default follow (`!== false`) */
export const _messagesFollow = field(() => true);

export const uiSignals = {
  dock,
  openTabs,
  activeTab,
  provMode,
  provSub,
  _menu,
  _dashPoll,
  _modalMode,
  _jobPoll,
  _messagesFollow,
};
