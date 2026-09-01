/**
 * Composer autocomplete. Port of app.js:12946-13033, 13125 + the keydown
 * branch at 13403-13411.
 *
 * `@` files (project + session, version-pinned), `#` sessions, `/` skills.
 * Popup is `#composer-ac`. `ac` is the live controller hung on window so
 * F-11's send() keydown can see `ac.open`.
 */

import { t } from "../../i18n/runtime";
import { skillsCatalog } from "../../stores/customize";
import { artifacts } from "../../stores/artifacts";
import { currentId, sessions } from "../../stores/session";
import { effProject } from "../customize/host";
import { api } from "../sessions/api";
import { $, el, grow } from "../sessions/dom";
import { renderComposerRefChips } from "../sessions/transcript";
import { acDetectFrom, type ComposerDetect } from "./detect";
import {
  artifactToAcItem,
  mergeArtifactCandidates,
  rankComposerItems,
  sessionToAcItem,
  skillToAcItem,
  type AcItem,
  type ArtifactLike,
} from "./rank";

export type AcState = {
  open: boolean;
  items: AcItem[];
  idx: number;
  trigger: string;
  start: number;
};

export const ac: AcState = {
  open: false,
  items: [],
  idx: 0,
  trigger: "",
  start: 0,
};

const acFiles: { pid: string | null; at: number; list: ArtifactLike[] } = {
  pid: null,
  at: 0,
  list: [],
};

export function acDetect(): ComposerDetect | null {
  const c = $("#composer") as HTMLTextAreaElement | null;
  if (!c) return null;
  const pos = c.selectionStart;
  const before = (c.value || "").slice(0, pos);
  return acDetectFrom(before, pos);
}

async function loadSkillsCatalog(): Promise<
  Array<{ displayName?: string; name?: string; description?: string }>
> {
  if (skillsCatalog.value) {
    return skillsCatalog.value as Array<{
      displayName?: string;
      name?: string;
      description?: string;
    }>;
  }
  try {
    const d = (await api("/skills/catalog")) as {
      skills?: Array<{ displayName?: string; name?: string; description?: string }>;
    };
    skillsCatalog.value = (d && d.skills) || [];
  } catch {
    skillsCatalog.value = [];
  }
  return (
    (skillsCatalog.value as Array<{
      displayName?: string;
      name?: string;
      description?: string;
    }>) || []
  );
}

export async function acProjectFiles(): Promise<ArtifactLike[]> {
  const pid = effProject() || null;
  if (pid && (acFiles.pid !== pid || Date.now() - acFiles.at > 4000)) {
    try {
      const a = await api(`/projects/${pid}/artifacts`);
      acFiles.list = Array.isArray(a) ? (a as ArtifactLike[]) : [];
      acFiles.pid = pid;
      acFiles.at = Date.now();
    } catch {
      /* keep last good list */
    }
  }
  const sessionList = (artifacts.value || []) as ArtifactLike[];
  return mergeArtifactCandidates(pid ? acFiles.list : [], sessionList);
}

export function ensureComposerAc(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  let box = document.getElementById("composer-ac");
  if (box) return box;
  box = el("div", "composer-ac hidden");
  box.id = "composer-ac";
  const refs = document.getElementById("composer-refs");
  const hint = document.getElementById("composer-hint");
  const composer = document.getElementById("composer");
  const parent =
    (refs && refs.parentNode) ||
    (hint && hint.parentNode) ||
    (composer && composer.parentNode) ||
    document.body;
  if (refs && refs.parentNode === parent) parent.insertBefore(box, refs.nextSibling);
  else if (hint && hint.parentNode === parent) parent.insertBefore(box, hint);
  else parent.appendChild(box);
  return box;
}

export function acClose(): void {
  ac.open = false;
  const b = $("#composer-ac");
  if (b) b.classList.add("hidden");
}

export function acRender(): void {
  const box = ensureComposerAc();
  if (!box) return;
  box.innerHTML = "";
  ac.items.forEach((it, i) => {
    const row = el("div", "ac-item" + (i === ac.idx ? " on" : ""));
    row.appendChild(el("span", "ac-lbl", ac.trigger + (it.label || "")));
    if (it.sub) row.appendChild(el("span", "ac-sub", it.sub));
    row.onmousedown = (e) => {
      e.preventDefault();
      acPick(i);
    };
    box.appendChild(row);
  });
  box.classList.remove("hidden");
}

export function acPick(i: number): void {
  const it = ac.items[i];
  if (!it) return;
  const c = $("#composer") as HTMLTextAreaElement | null;
  if (!c) return;
  const val = c.value;
  const pos = c.selectionStart;
  const token = ac.trigger + it.insert + " ";
  c.value = val.slice(0, ac.start) + token + val.slice(pos);
  const np = ac.start + token.length;
  c.setSelectionRange(np, np);
  acClose();
  grow();
  renderComposerRefChips();
  c.focus();
}

export async function acUpdate(): Promise<void> {
  const d = acDetect();
  if (!d) {
    acClose();
    return;
  }
  let items: AcItem[] = [];
  if (d.trigger === "@") {
    const files = await acProjectFiles();
    items = files.map((a) =>
      artifactToAcItem(a, currentId.value, t("ac.fromOtherSession")),
    );
  } else if (d.trigger === "#") {
    const rows = (sessions.value || []) as Array<{
      name?: string;
      task_summary?: string;
    }>;
    items = rows.map(sessionToAcItem);
  } else if (d.trigger === "/") {
    const sk = await loadSkillsCatalog();
    items = sk.map(skillToAcItem);
  }
  items = rankComposerItems(items, d.query);
  if (!items.length) {
    acClose();
    return;
  }
  ac.open = true;
  ac.items = items;
  ac.idx = 0;
  ac.trigger = d.trigger;
  ac.start = d.start;
  acRender();
}

function onComposerKeydown(e: KeyboardEvent): void {
  if (e.isComposing || e.keyCode === 229) return;
  if (!ac.open) return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    e.stopImmediatePropagation();
    ac.idx = (ac.idx + 1) % ac.items.length;
    acRender();
    return;
  }
  if (e.key === "ArrowUp") {
    e.preventDefault();
    e.stopImmediatePropagation();
    ac.idx = (ac.idx - 1 + ac.items.length) % ac.items.length;
    acRender();
    return;
  }
  if (e.key === "Enter" || e.key === "Tab") {
    e.preventDefault();
    e.stopImmediatePropagation();
    acPick(ac.idx);
    return;
  }
  if (e.key === "Escape") {
    e.preventDefault();
    e.stopImmediatePropagation();
    acClose();
  }
}

let composerBound = false;

function attachComposer(c: HTMLTextAreaElement): void {
  if (composerBound) return;
  composerBound = true;
  ensureComposerAc();
  c.addEventListener("input", () => {
    void acUpdate();
  });
  c.addEventListener("keydown", onComposerKeydown, true);
  c.addEventListener("blur", () => setTimeout(acClose, 120));
}

export function bindComposerAutocomplete(): void {
  if (typeof document === "undefined" || composerBound) return;
  const tryBind = (): void => {
    const c = document.getElementById("composer") as HTMLTextAreaElement | null;
    if (c) attachComposer(c);
  };
  tryBind();
  if (composerBound) return;
  if (typeof MutationObserver === "function") {
    const ob = new MutationObserver(() => {
      tryBind();
      if (composerBound) ob.disconnect();
    });
    ob.observe(document.documentElement || document.body, {
      childList: true,
      subtree: true,
    });
  }
}
