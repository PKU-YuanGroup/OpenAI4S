/**
 * M-73: a session's branches under its row in the sidebar. A fork used to
 * leave one line under the composer and nothing else: no row to see which
 * branch was open, and switching meant finding the Action Timeline's branch
 * panel. The open session's row now lists its branches when it has more than
 * the main line, marks the current one, and a click on another asks first
 * and switches (the same confirmed activation the panel runs).
 *
 * The branch projection is the one the workbench already reads on every
 * session open (`branchState`); nothing new is fetched.
 *
 * Owner decision 24: a branch that is neither the current one nor the main
 * line also has a delete button. It asks first, in the app's own dialog, then
 * sends `DELETE /frames/{id}/branches/{branch_id}` and re-reads the branches.
 */

import { effect, untracked } from "@preact/signals";
import { onLanguageChange, t } from "../../i18n";
import { currentId } from "../../stores/session";
import { branchState } from "../../stores/timeline";
import { askConfirm } from "../chrome/dialog";
import { branchDisplayName } from "../execution/branch";
import { branchT } from "../execution/copy";
import { ApiError, api, apiErrorText } from "./api";
import { hint } from "./chrome";
import { branchDeleteCopy } from "./copy";
import { $, el } from "./dom";
import { iconEl } from "./icon";
import { callLane } from "./lane";

type Branch = { branch_id?: string; name?: string; activatable?: boolean };
type BranchView = {
  root_frame_id?: string;
  branch_id?: string;
  branches?: Branch[];
  capabilities?: { activate?: boolean };
};

/** The open session's branch list, or null when it has only the main line. */
export function sessionBranchList(fid: string, view: unknown = branchState.value): HTMLElement | null {
  const state = (view && typeof view === "object" ? view : null) as BranchView | null;
  if (!fid || !state || fid !== currentId.value) return null;
  if (state.root_frame_id && state.root_frame_id !== fid) return null;
  const branches = (state.branches || []).filter((branch) => branch && branch.branch_id);
  if (branches.length < 2) return null;
  const box = el("div", "session-branches");
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", branchT("branch.sidebarLabel"));
  for (const branch of branches) {
    const id = String(branch.branch_id);
    const current = id === state.branch_id;
    const name = branchDisplayName(branch, fid);
    const item = el("div", "session-branch-item");
    const row = el("button", "session-branch" + (current ? " current" : "")) as HTMLButtonElement;
    row.type = "button";
    row.dataset.branchId = id;
    row.appendChild(el("span", "sb-mark", "↳"));
    row.appendChild(el("span", "sb-name", name));
    if (current) {
      row.appendChild(el("span", "sb-tag", t("branch.current")));
      row.setAttribute("aria-current", "true");
      row.title = branchT("branch.sidebarCurrent", name);
      row.disabled = true;
    } else {
      const can = !!branch.activatable && state.capabilities?.activate !== false;
      row.disabled = !can;
      row.title = can ? branchT("branch.sidebarSwitch", name) : branchT("branch.sidebarCannot");
      row.onclick = (event) => {
        event.stopPropagation();
        // Asks first: the kernel restarts and its variables go.
        void callLane("activateSessionBranch", id);
      };
    }
    item.appendChild(row);
    // The main line is the session's own id; neither it nor the current
    // branch can be deleted (the server refuses both with 409 as well).
    if (!current && id !== fid) {
      const remove = el("button", "session-branch-del") as HTMLButtonElement;
      remove.type = "button";
      remove.dataset.branchId = id;
      remove.title = branchDeleteCopy("branchDelete.label", name);
      remove.setAttribute("aria-label", remove.title);
      remove.appendChild(iconEl("trash-2", 13));
      remove.onclick = (event) => {
        event.stopPropagation();
        void deleteSessionBranch(fid, id, name);
      };
      item.appendChild(remove);
    }
    box.appendChild(item);
  }
  return box;
}

const deleting = new Set<string>();

/** What a refused deletion says; the server's own sentence only when unmapped. */
function deleteRefusal(error: unknown, name: string): string {
  const code = error instanceof ApiError ? error.code : "";
  if (code === "branch_active") return branchDeleteCopy("branchDelete.active", name);
  if (code === "branch_main_line") return branchDeleteCopy("branchDelete.mainLine");
  if (code === "session_busy") return branchDeleteCopy("branchDelete.busy");
  if (code === "branch_not_found") return branchDeleteCopy("branchDelete.notFound", name);
  return branchDeleteCopy("branchDelete.failed", name, apiErrorText(error));
}

/**
 * Delete one branch of the open session after the in-app confirm (never the
 * browser's `confirm()`, which an embedded pane dismisses on its own). One
 * request per branch at a time; a cancel sends nothing. True when deleted.
 */
export async function deleteSessionBranch(fid: string, branchId: string, name: string): Promise<boolean> {
  const key = fid + "\0" + branchId;
  if (!fid || !branchId || fid === branchId || deleting.has(key)) return false;
  const yes = await askConfirm(branchDeleteCopy("branchDelete.confirm", name), {
    danger: true,
    okLabel: branchDeleteCopy("branchDelete.ok"),
  });
  if (!yes || deleting.has(key)) return false;
  deleting.add(key);
  let deleted = false;
  let stale = false;
  try {
    await api(`/frames/${encodeURIComponent(fid)}/branches/${encodeURIComponent(branchId)}`, {
      method: "DELETE",
    });
    deleted = true;
    if (currentId.value === fid) hint(branchDeleteCopy("branchDelete.done", name));
  } catch (error) {
    stale = error instanceof ApiError && error.code === "branch_not_found";
    if (currentId.value === fid) hint(deleteRefusal(error, name), true);
  } finally {
    deleting.delete(key);
  }
  // The list this button came from is out of date either way. A failed
  // re-read is not a failed deletion; the `branch_deleted` event retries it.
  if (deleted || stale) {
    try {
      await Promise.resolve(callLane("loadWorkbenchState", fid, true, ["branches"]));
    } catch {
      /* the next branch event or session open re-reads the list */
    }
  }
  return deleted;
}

/** Put the open session's branch list under its row (or take it away). */
export function paintSessionBranches(): void {
  if (typeof document === "undefined") return;
  const list = $("#session-list");
  const fid = currentId.value;
  if (!list || typeof list.querySelectorAll !== "function") return;
  list.querySelectorAll(".session-branches").forEach((node) => node.remove());
  if (!fid) return;
  const row = [...list.querySelectorAll(".session.active")].find(
    (node) => (node as HTMLElement).dataset.frameId === fid,
  ) as HTMLElement | undefined;
  const branches = row ? sessionBranchList(fid) : null;
  if (row && branches) row.after(branches);
}

let watching = false;

/** Repaint the list whenever the branch projection or the open session changes. */
export function watchSessionBranches(): void {
  if (watching) return;
  watching = true;
  effect(() => {
    void branchState.value;
    void currentId.value;
    untracked(paintSessionBranches);
  });
  onLanguageChange(paintSessionBranches);
}
