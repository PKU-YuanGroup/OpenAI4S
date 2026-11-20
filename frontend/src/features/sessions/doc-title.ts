/**
 * SYNC-18: the browser tab says which session it is.
 *
 * Every tab read "OpenAI4S" -- home, a session, a running session, after a
 * rename -- so with several open nobody could tell them apart or see which
 * one had finished. The title is now `<session> · <project> — OpenAI4S`
 * while a session is open, with `⏳ ` in front while its turn runs. A turn
 * waiting for an approval keeps the "(Approval needed)" prefix
 * `messages/approval.ts` paints, in place of the hourglass.
 */

import { effect } from "@preact/signals";
import { t } from "../../i18n";
import { _titleName, currentId, project, projects } from "../../stores/session";
import { running } from "../../stores/stream";
import { approvalCopy, approvalPendingFor } from "../messages/approval";
import { displayProjectName } from "./copy";

export const APP_TITLE = "OpenAI4S";
export const RUNNING_MARK = "⏳ ";

export function documentTitle(state: {
  open: boolean;
  name: string;
  project: string;
  running: boolean;
  awaiting: boolean;
}): string {
  if (!state.open) return APP_TITLE;
  const where = state.project ? `${state.name} · ${state.project}` : state.name;
  const mark = state.awaiting ? approvalCopy("title") : state.running ? RUNNING_MARK : "";
  return `${mark}${where} — ${APP_TITLE}`;
}

function projectName(id: string | null): string {
  if (!id) return "";
  const rows = (projects.value as Array<{ project_id?: string; id?: string; name?: string }>) || [];
  const row = rows.find((p) => (p.project_id || p.id) === id);
  return row ? displayProjectName(row) : "";
}

/** Keep `document.title` in step with the open session. Returns the stop function. */
export function bootDocumentTitle(): () => void {
  return effect(() => {
    const fid = currentId.value || null;
    const title = documentTitle({
      open: !!fid,
      name: _titleName.value || t("conv.title.default"),
      project: projectName(project.value || null),
      running: !!running.value,
      awaiting: !!fid && approvalPendingFor.value === fid,
    });
    if (typeof document !== "undefined" && document.title !== title) document.title = title;
  });
}
