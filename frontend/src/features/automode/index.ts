/**
 * Read-only Auto Mode status and audit view (issue #217). The lane reads
 * `GET /frames/{id}/auto-mode` and `GET /frames/{id}/auto-audits` and nothing
 * else: no PATCH, no transition, no model call, no review, repair, resume or
 * cancellation.
 */

import "./automode.css";
import { installAutoModeHints } from "./hints";

export { openAutoModeAudits, autoModeAuditsOpen, AUDIT_PAGE_SIZE } from "./audits";
export { autoModeHint, registerAutoModeHandlers, AUTO_MODE_HINT_TYPES } from "./hints";
export { autoModeMenuItem, autoModeStatusVisible } from "./menu";
export { autoModeStatus, beginAutoModeStatusRead, refreshAutoModeStatus } from "./status";

/** Boot: the five hint handlers this lane owns and the reopen/reconnect watchers. */
export function installAutoMode(): void {
  installAutoModeHints();
}
