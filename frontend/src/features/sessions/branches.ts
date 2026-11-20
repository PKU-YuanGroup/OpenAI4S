/**
 * M-73: a session's branches under its row in the sidebar. A fork used to
 * leave one line under the composer and nothing else: no row to see which
 * branch was open, and switching meant finding the Action Timeline's branch
 * panel. The open session's row now lists its branches when it has more than
 * the main line, marks the current one, and a click on another asks first
 * and switches (the same confirmed activation the panel runs).
 *
 * The branch projection is the one the workbench already reads on every
 * session open (`branchState`); nothing new is fetched. No delete: there is
 * no route for it.
 */

import { effect, untracked } from "@preact/signals";
import { onLanguageChange, t } from "../../i18n";
import { currentId } from "../../stores/session";
import { branchState } from "../../stores/timeline";
import { branchDisplayName } from "../execution/branch";
import { branchT } from "../execution/copy";
import { $, el } from "./dom";
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
    box.appendChild(row);
  }
  return box;
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
