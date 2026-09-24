/**
 * F-08 window export.
 *
 * `renderMd` is in the E2E contract (browser_smoke, browser_stage0_acceptance)
 * and F-05 reserves it with a stub that throws. The owning module assigns the
 * real one, the way F-06's bootWs() assigns onEvent and the i18n module
 * assigns t -- a name left for the integration step is a name that boots as a
 * placeholder.
 *
 * Importing it also binds the one delegated listener behind every code
 * block's Copy button (`copy.ts`); it needs `document`, not any node in it.
 */

export { esc, escQuote } from "./esc";
export { mdHighlight, MD_KEYWORDS } from "./highlight";
export { mdCodeBlock, mdInline, renderMd } from "./render";
export { bindCodeCopy, copyCodeBlock } from "./copy";

import { bindCodeCopy } from "./copy";
import { renderMd } from "./render";

const hostWindow = (globalThis as unknown as { window?: Record<string, unknown> }).window;
if (hostWindow) hostWindow.renderMd = renderMd;
if (typeof document !== "undefined" && typeof document.addEventListener === "function") bindCodeCopy(document);
