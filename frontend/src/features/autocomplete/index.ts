/**
 * F-12 autocomplete. Importing this module assigns `ac` and `edacTeardown`
 * onto `window` (the F-06 `bootWs()` / F-07 `t()` pattern) and binds the
 * composer + editor popups.
 *
 * Do not import `compat/window-exports` from here.
 */

import { isReady } from "../../compat/stub";
import {
  ac,
  acClose,
  acDetect,
  acPick,
  acUpdate,
  bindComposerAutocomplete,
} from "./composer";
import {
  bindEditorAutocomplete,
  edacTeardown,
  watchEditAreas,
} from "./editor";

export {
  acDetectFrom,
  edacDetectFrom,
  edacExt,
  type ComposerDetect,
  type EditorDetect,
} from "./detect";
export {
  AC_LIMIT,
  artifactToAcItem,
  harvestBufferIdentifiers,
  mergeArtifactCandidates,
  rankComposerItems,
  rankEditorItems,
  sessionToAcItem,
  skillToAcItem,
  type AcItem,
  type ArtifactLike,
} from "./rank";
export {
  ac,
  acClose,
  acDetect,
  acPick,
  acProjectFiles,
  acUpdate,
  bindComposerAutocomplete,
} from "./composer";
export {
  bindEditorAutocomplete,
  edacClose,
  edacDetect,
  edacItems,
  edacPick,
  edacTeardown,
  edacUpdate,
  watchEditAreas,
  type EditorController,
} from "./editor";

export type AutocompleteTarget = Record<string, unknown>;

export function installAutocomplete(
  target: AutocompleteTarget = globalThis as unknown as AutocompleteTarget,
): void {
  target.ac = ac;
  target.edacTeardown = edacTeardown;
  target.bindEditorAutocomplete = bindEditorAutocomplete;
  target.acUpdate = acUpdate;
  target.acClose = acClose;
  target.acDetect = acDetect;
  target.acPick = acPick;
  if (typeof document !== "undefined") {
    bindComposerAutocomplete();
    watchEditAreas();
  }
}

const hostWindow = (globalThis as unknown as { window?: AutocompleteTarget }).window;
if (hostWindow) installAutocomplete(hostWindow);

export function autocompleteReady(
  target: AutocompleteTarget = globalThis as unknown as AutocompleteTarget,
): boolean {
  return Boolean(target.ac) && isReady(target.edacTeardown);
}
