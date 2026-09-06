/**
 * ⌘K command palette. Port of app.js:10940-11057.
 *
 * Artifact hits follow M-03: open the owning session, then openViewer at the
 * exact version when `version_id` is present (never silently drop it). If
 * F-17 has not yet assigned `openViewer`, open the Files tab and leave a
 * TODO — B-06 is in this tree, the viewer is not.
 */

import { publicText } from "../scrub/scrub";
import { cycleTheme, themeIsDark } from "../theme/theme";
import { t } from "../../i18n/runtime";
import { skillsCatalog } from "../../stores/customize";
import { currentId } from "../../stores/session";
import { api } from "./api";
import { $, el, grow, icon } from "./dom";
import { hostFn, invokeHost, isReady } from "./host";
import { anyModalOpen } from "./modal";

export type PaletteItem = {
  group: string;
  label: string;
  sub?: string;
  icon?: string;
  run: () => void;
};

export type PaletteState = {
  open: boolean;
  items: PaletteItem[];
  idx: number;
  el: HTMLElement | null;
  listEl: HTMLElement | null;
  gen: number;
};

/** app.js:10940 */
export const PAL: PaletteState = {
  open: false,
  items: [],
  idx: 0,
  el: null,
  listEl: null,
  gen: 0,
};

export function isPaletteOpen(): boolean {
  return PAL.open;
}

type SkillRow = { name?: string; displayName?: string; description?: string };
type SessionHit = { id: string; name?: string; task_summary?: string; project_id?: string | null };
type ArtifactHit = {
  id?: string;
  artifact_id?: string;
  filename?: string;
  content_type?: string;
  root_frame_id?: string | null;
  project_id?: string | null;
  version_id?: string | null;
  latest_version_id?: string | null;
};
type DataproHit = {
  artifact_id?: string;
  root_frame_id?: string | null;
  project_id?: string | null;
  dataset_type?: string;
  json_pointer?: string;
  content?: unknown;
  query?: string;
};



/** M-03 query `?artifact={id}&version_id={vid}`. Omitted version_id → latest. */
export function parseArtifactQuery(
  search: string,
): { artifactId: string; versionId: string | null } | null {
  const raw = search.startsWith("?") ? search.slice(1) : search;
  const q = new URLSearchParams(raw);
  const artifactId = (q.get("artifact") || "").trim();
  if (!artifactId) return null;
  const versionRaw = q.get("version_id");
  const versionId =
    versionRaw != null && versionRaw.trim() !== "" ? versionRaw.trim() : null;
  return { artifactId, versionId };
}

/**
 * M-03 palette artifact hit: session first, then exact-version viewer.
 * `version_id` is forwarded as-is; it is never rewritten to latest here.
 *
 * TODO(F-17): when `openViewer` is assigned, it must refuse silent-latest
 * if `version_id` is set and show stale/not-found when that version is gone.
 */
export function openPaletteArtifact(hit: ArtifactHit): void {
  closePalette();
  const id = String(hit.id || hit.artifact_id || "");
  if (!id) return;
  const view = {
    id,
    filename: hit.filename || "",
    content_type: hit.content_type || "",
    root_frame_id: hit.root_frame_id || null,
    project_id: hit.project_id || null,
    // Exact version when the search row carries one. Do not drop this field.
    ...(hit.version_id ? { version_id: hit.version_id } : {}),
    ...(hit.latest_version_id ? { latest_version_id: hit.latest_version_id } : {}),
  };
  const go = (): void => {
    const openViewer = hostFn("openViewer");
    if (isReady(openViewer)) {
      openViewer(view);
      return;
    }
    // F-17 viewer not mounted yet. Open Files so the hit is not dropped.
    const setActiveTab = hostFn("setActiveTab");
    const dockTab = hostFn("dockTab");
    if (isReady(setActiveTab)) setActiveTab("files");
    else if (isReady(dockTab)) dockTab("files");
  };
  const openConversation = hostFn("openConversation");
  const frameId = hit.root_frame_id;
  if (frameId && isReady(openConversation) && frameId !== currentId.value) {
    Promise.resolve(openConversation(frameId, hit.project_id || null)).then(go);
    return;
  }
  go();
}

/** app.js:10973-10985 */
export function dataproPaletteSummary(hit: DataproHit | null | undefined): string {
  const parts: string[] = [];
  if (hit && hit.dataset_type) parts.push(publicText(hit.dataset_type, 60));
  if (hit && hit.json_pointer) parts.push(publicText(hit.json_pointer, 80));
  if (hit && hit.content != null) {
    let content: unknown = hit.content;
    if (typeof content !== "string") {
      try {
        content = JSON.stringify(content);
      } catch {
        content = String(content);
      }
    }
    if (content) parts.push(publicText(content, 180));
  }
  return parts.join(" · ");
}

/** app.js:10986-11014 */
export function openDataproSearchHit(hit: DataproHit): void {
  closePalette();
  if (hit && hit.artifact_id) {
    const view = {
      id: String(hit.artifact_id),
      filename: t("palette.datapro.result") + ".json",
      content_type: "application/json",
      root_frame_id: hit.root_frame_id || null,
      project_id: hit.project_id || null,
    };
    const openViewer = hostFn("openViewer");
    const openArtifact = hostFn("openArtifact");
    const openConversation = hostFn("openConversation");
    if (hit.root_frame_id && hit.root_frame_id !== currentId.value) {
      if (isReady(openConversation)) {
        Promise.resolve(openConversation(hit.root_frame_id, hit.project_id || null)).then(
          () => {
            if (isReady(openViewer)) openViewer(view);
          },
        );
      }
      return;
    }
    if (!hit.root_frame_id && !currentId.value) {
      if (isReady(openArtifact)) openArtifact(view);
      return;
    }
    if (isReady(openViewer)) openViewer(view);
    return;
  }
  const openCust = hostFn("openCust");
  if (isReady(openCust)) openCust("connectors");
}

async function loadSkillsCatalog(): Promise<SkillRow[]> {
  if (skillsCatalog.value) return skillsCatalog.value as SkillRow[];
  try {
    const d = (await api("/skills/catalog")) as { skills?: SkillRow[] } | null;
    skillsCatalog.value = (d && d.skills) || [];
  } catch {
    skillsCatalog.value = [];
  }
  return (skillsCatalog.value || []) as SkillRow[];
}

/** app.js:10963-10972 */
export function palActions(): PaletteItem[] {
  return [
    {
      group: t("palette.group.commands"),
      label: t("palette.action.newSession"),
      icon: "plus",
      run: () => {
        invokeHost(hostFn("newSession"));
      },
    },
    {
      group: t("palette.group.commands"),
      label: t("palette.action.newProject"),
      icon: "plus",
      run: () => {
        invokeHost(hostFn("openProjectModal"));
      },
    },
    {
      group: t("palette.group.commands"),
      label: t("palette.action.openNotebook"),
      icon: "notebook",
      run: () => {
        invokeHost(hostFn("setActiveTab"), "notebook");
      },
    },
    {
      group: t("palette.group.commands"),
      label: t("palette.action.customize"),
      icon: "sliders",
      run: () => {
        invokeHost(hostFn("openCust"));
      },
    },
    {
      group: t("palette.group.commands"),
      label: t("theme.toggle"),
      icon: themeIsDark() ? "sun" : "moon",
      run: () => cycleTheme(),
    },
    {
      group: t("palette.group.commands"),
      label: t("palette.action.backHome"),
      icon: "arrow-left",
      run: () => {
        invokeHost(hostFn("showDashboard"));
      },
    },
  ];
}

/** app.js:11041 */
export function openPalette(): void {
  if (PAL.open) return;
  PAL.open = true;
  const ov = el("div", "palette-ov");
  const box = el("div", "palette");
  const inp = el("input", "palette-input") as HTMLInputElement;
  inp.placeholder = t("palette.searchPlaceholder");
  inp.spellcheck = false;
  const list = el("div", "palette-list");
  box.appendChild(inp);
  box.appendChild(list);
  ov.appendChild(box);
  document.body.appendChild(ov);
  PAL.el = ov;
  PAL.listEl = list;
  ov.onclick = (e) => {
    if (e.target === ov) closePalette();
  };
  inp.addEventListener("input", () => {
    void palSearch(inp.value);
  });
  inp.addEventListener("keydown", (e) => {
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === "Escape") {
      e.preventDefault();
      closePalette();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      PAL.idx = Math.min(PAL.idx + 1, PAL.items.length - 1);
      palRender();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      PAL.idx = Math.max(PAL.idx - 1, 0);
      palRender();
    } else if (e.key === "Enter") {
      e.preventDefault();
      palPick(PAL.idx);
    }
  });
  void palSearch("");
  inp.focus();
}

/** app.js:11062 */
export function closePalette(): void {
  if (PAL.el) PAL.el.remove();
  PAL.el = null;
  PAL.listEl = null;
  PAL.open = false;
  PAL.items = [];
  PAL.idx = 0;
}

export function resetPalette(): void {
  closePalette();
  PAL.gen = 0;
}

/** app.js:11015-11041 */
export async function palSearch(query: string): Promise<void> {
  const q = (query || "").trim().toLowerCase();
  const gen = (PAL.gen = (PAL.gen || 0) + 1);
  const items: PaletteItem[] = [];
  palActions().forEach((a) => {
    if (!q || a.label.toLowerCase().includes(q)) items.push(a);
  });
  const sk = await loadSkillsCatalog();
  sk.filter(
    (s) =>
      !q ||
      (s.name || "").toLowerCase().includes(q) ||
      (s.displayName || "").toLowerCase().includes(q),
  )
    .slice(0, 6)
    .forEach((s) =>
      items.push({
        group: t("palette.group.skills"),
        label: s.displayName || s.name || "",
        sub: s.description || "",
        icon: "sparkles",
        run: () => {
          closePalette();
          const c = $("#composer") as HTMLTextAreaElement | null;
          if (c) {
            c.value = (c.value ? c.value + " " : "") + "/" + s.name + " ";
            c.focus();
            const g = hostFn("grow");
            if (isReady(g)) g();
            else grow();
          }
        },
      }),
    );
  if (q) {
    try {
      const r = (await api("/search?q=" + encodeURIComponent(q))) as {
        sessions?: SessionHit[];
        artifacts?: ArtifactHit[];
        datapro?: DataproHit[];
      };
      (r.sessions || []).slice(0, 8).forEach((s) =>
        items.push({
          group: t("conv.title.default"),
          label: s.name || s.task_summary || t("conv.title.default"),
          icon: "message-square",
          run: () => {
            closePalette();
            invokeHost(hostFn("openConversation"), s.id, s.project_id || null);
          },
        }),
      );
      (r.artifacts || []).slice(0, 8).forEach((a) =>
        items.push({
          group: t("palette.group.artifacts"),
          label: a.filename || "",
          sub: a.content_type || "",
          icon: "file",
          run: () => openPaletteArtifact(a),
        }),
      );
      (r.datapro || []).slice(0, 8).forEach((hit) =>
        items.push({
          group: t("palette.group.datapro"),
          label: publicText((hit && hit.query) || t("palette.datapro.result"), 140),
          sub: dataproPaletteSummary(hit),
          icon: "search",
          run: () => openDataproSearchHit(hit),
        }),
      );
    } catch {
      /* search is best-effort */
    }
  }
  if (gen !== PAL.gen) return;
  PAL.items = items;
  PAL.idx = 0;
  palRender();
}

/** app.js:11043-11056 */
export function palRender(): void {
  const list = PAL.listEl;
  if (!list) return;
  list.innerHTML = "";
  if (!PAL.items.length) {
    list.appendChild(el("div", "palette-empty", t("palette.empty")));
    return;
  }
  let lastGroup: string | null = null;
  PAL.items.forEach((it, i) => {
    if (it.group !== lastGroup) {
      lastGroup = it.group;
      list.appendChild(el("div", "palette-group", it.group));
    }
    const row = el("div", "palette-item" + (i === PAL.idx ? " on" : ""));
    if (it.icon) {
      const ic = el("span", "pi-ic");
      ic.innerHTML = icon(it.icon, 15);
      row.appendChild(ic);
    }
    const txt = el("div", "pi-txt");
    txt.appendChild(el("div", "pi-label", it.label));
    if (it.sub) txt.appendChild(el("div", "pi-sub", it.sub));
    row.appendChild(txt);
    row.onmouseenter = () => {
      PAL.idx = i;
      [...list.querySelectorAll(".palette-item")].forEach((x) => x.classList.remove("on"));
      row.classList.add("on");
    };
    row.onclick = () => palPick(i);
    list.appendChild(row);
  });
}

/** app.js:11057 */
export function palPick(i: number): void {
  const it = PAL.items[i];
  if (it && it.run) it.run();
}

/** app.js:13370-13373 — ⌘K / Ctrl-K. */
export function handlePaletteHotkey(e: KeyboardEvent): boolean {
  if (!(e.metaKey || e.ctrlKey) || (e.key !== "k" && e.key !== "K")) return false;
  if (PAL.open) {
    e.preventDefault();
    closePalette();
    return true;
  }
  if (anyModalOpen()) return true;
  e.preventDefault();
  openPalette();
  return true;
}

export function bindPaletteButton(): void {
  const btn = $("#search-btn");
  if (btn) btn.addEventListener("click", () => openPalette());
}
