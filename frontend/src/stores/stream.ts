import { field } from "./signal-field";

/** S.ws — app.js:120 */
export const ws = field(() => null as unknown);
/** S.stream — app.js:120; live markdown/tool wrap, mutated in place */
export const stream = field(() => null as unknown);
/** S.running — app.js:120 */
export const running = field(() => false);
/** S.planMode — app.js:120 */
export const planMode = field(() => false);
/** S.exploreMode — app.js:120 */
export const exploreMode = field(() => false);
/** S.planPending — app.js:120 */
export const planPending = field(() => false);
/** S.planReady — app.js:120 */
export const planReady = field(() => null as unknown);
/** S.planStatus — app.js:120 */
export const planStatus = field(() => null as unknown);
/** S._seqSeen — app.js:5176; nested writes `S._seqSeen[rid] = sq` */
export const _seqSeen = field(() => Object.create(null) as Record<string, number>);
/** S._streamEpoch — app.js:5180 */
export const _streamEpoch = field(() => null as string | null);
/** S._replayGap — app.js:5197 */
export const _replayGap = field(() => null as unknown);
/** S.stepEls — app.js:5458; mutated in place */
export const stepEls = field(() => Object.create(null) as Record<string, unknown>);
/** S.reviewGate — app.js:5571 */
export const reviewGate = field(() => null as unknown);
/** S.turnTicket — app.js:5680 */
export const turnTicket = field(() => 0);
/** S.pendingRequestId — app.js:5687 */
export const pendingRequestId = field(() => null as string | null);
/** S.pendingExecutionId — app.js:5690 */
export const pendingExecutionId = field(() => null as string | null);
/** S._resumeTimer — app.js:5838 */
export const _resumeTimer = field(() => null as unknown);
/** S._resumeTok — app.js:5838 */
export const _resumeTok = field(() => 0);
/** S.permCards — app.js:6490; null-proto, mutated in place */
export const permCards = field(() => Object.create(null) as Record<string, unknown>);

export const streamSignals = {
  ws,
  stream,
  running,
  planMode,
  exploreMode,
  planPending,
  planReady,
  planStatus,
  _seqSeen,
  _streamEpoch,
  _replayGap,
  stepEls,
  reviewGate,
  turnTicket,
  pendingRequestId,
  pendingExecutionId,
  _resumeTimer,
  _resumeTok,
  permCards,
};
