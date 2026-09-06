/**
 * Dock Viewer chrome, artifact editor, and version history.
 * Port of app.js:9430-9609 plus retrievalSourcePanel 13105-13124.
 *
 * M-03 deep-link copy + stale-version banner from F-17 are kept.
 */

import { _artBust, _editing, dockArtifact } from "../stores/artifacts";
import { currentId } from "../stores/session";
import { _modalMode, provMode } from "../stores/ui";
import { isReady } from "../compat/stub";
import { API, api, apiErrorText, artifactsFetch, bytes } from "../features/artifacts/api";
import { artUrl, syncArtifactVersion } from "../features/artifacts/cache";
import { filesT } from "../features/artifacts/copy";
import { artifactDeepLinkHref, versionResolveMessage } from "../features/artifacts/deeplink";
import { loadArtifacts } from "../features/artifacts/load";
import { renderArtifactBody } from "../features/artifacts/renderers";
import { viewerVersionState } from "../features/artifacts/state";
import type { ArtifactRow } from "../features/artifacts/types";
import { closeTab } from "../features/artifacts/ui";
import {
  decorateViewerWithProvenance,
  renderProvenanceInto,
} from "../features/execution/provenance";
import { bindEditorAutocomplete, edacTeardown } from "../features/autocomplete/editor";
import { openModalEl } from "../features/chrome/modal";
import { hint, openMenu, type MenuItem } from "../features/sessions/chrome";
import { ago } from "../features/sessions/dom";
import { $, el, ghostIconBtn, icon } from "./dom";
import { callWindow, translate } from "./host";
import { molTeardown } from "./mol";

type DiffPanel = HTMLElement & { _diffRequest?: number };

type VersionRow = {
  version_id: string;
  ordinal?: number;
  is_latest?: boolean;
  size_bytes?: number;
  created_at?: string;
  retrieval_source?: Record<string, unknown>;
};

const RETRIEVAL_FIELD_ORDER = [
  "database",
  "source",
  "retrieved_at",
  "request_url",
  "query",
  "normalization_version",
  "response_sha256",
  "record_count",
];

export function retrievalSourcePanel(src: Record<string, unknown>): HTMLElement {
  const box = el("div", "ver-src");
  box.appendChild(el("div", "ver-src-head", translate("versions.retrievalSource")));
  RETRIEVAL_FIELD_ORDER.forEach((field) => {
    if (src[field] === undefined || src[field] === null || src[field] === "") return;
    const row = el("div", "ver-src-row");
    row.appendChild(el("span", "ver-src-k", field));
    row.appendChild(el("span", "ver-src-v", String(src[field])));
    box.appendChild(row);
  });
  if (Array.isArray(src.truncated_fields) && src.truncated_fields.length) {
    box.appendChild(
      el(
        "div",
        "ver-src-note",
        translate("versions.retrievalTruncated", (src.truncated_fields as unknown[]).join(", ")),
      ),
    );
  }
  if (src.undisclosed_field_count) {
    box.appendChild(
      el("div", "ver-src-note", translate("versions.retrievalWithheld", src.undisclosed_field_count)),
    );
  }
  return box;
}

/** app.js:9458-9461 */
export function isTextEditable(a: ArtifactRow | null | undefined): boolean {
  if (!a) return false;
  const nm = (a.filename || "").toLowerCase();
  const ct = a.content_type || "";
  if (ct.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg|pdb|cif|mol|mol2|sdf|xyz|pdf)$/i.test(nm))
    return false;
  return (
    /\.(md|markdown|txt|log|csv|tsv|json|py|js|ts|fasta|fa|nwk|treefile|xml|ya?ml|sh|r|tex|html?|css)$/i.test(
      nm,
    ) ||
    ct.startsWith("text/") ||
    /json|csv|xml|javascript/.test(ct)
  );
}

/** app.js:9430-9435 */
export function openArtifact(a: ArtifactRow): void {
  if (typeof document === "undefined") return;
  const title = $("#modal-title");
  if (title) title.textContent = a.filename || translate("modal.title.preview");
  const dl = $("#modal-download") as HTMLAnchorElement | null;
  if (dl) {
    dl.style.display = "";
    dl.href = `${API}/artifacts/${a.id}`;
    dl.setAttribute("download", a.filename || "artifact");
  }
  const body = $("#modal-body");
  if (body) renderArtifactBody(body, a);
  openModalEl($("#modal"));
}

export function editArtifact(a: ArtifactRow): void {
  _editing.value = a.id;
  renderViewer();
}

async function renameArtifact(a: ArtifactRow): Promise<void> {
  const name =
    typeof prompt === "function" ? prompt(translate("artifact.rename.prompt"), a.filename || "") : null;
  if (!name || name === a.filename) return;
  try {
    await api(`/artifacts/${a.id}/rename`, { method: "PATCH", body: JSON.stringify({ filename: name }) });
    a.filename = name;
    if (currentId.value) void loadArtifacts(currentId.value);
    renderViewer();
    hint(translate("artifact.renamed"));
  } catch (e) {
    hint(translate("toast.renameFailed", apiErrorText(e)), true);
  }
}

async function deleteArtifact(a: ArtifactRow): Promise<void> {
  const ok = typeof confirm === "function" ? confirm(translate("artifact.delete.confirm")) : false;
  if (!ok) return;
  try {
    await api(`/artifacts/${a.id}`, { method: "DELETE" });
    closeTab(a.id);
    if (currentId.value) void loadArtifacts(currentId.value);
    hint(translate("artifact.deleted", a.filename || ""));
  } catch (e) {
    hint(translate("toast.deleteFailed", apiErrorText(e)), true);
  }
}

function renderArtifactEditor(body: HTMLElement, a: ArtifactRow): void {
  const bar = el("div", "edit-bar");
  bar.appendChild(el("span", "edit-label", translate("editor.label", a.filename || "")));
  const save = el("button", "solid-btn small", translate("common.save"));
  const cancel = el("button", "outline-btn small", translate("common.cancel"));
  const acts = el("div", "edit-acts");
  acts.appendChild(cancel);
  acts.appendChild(save);
  bar.appendChild(acts);
  body.appendChild(bar);
  const ta = el("textarea", "edit-area");
  ta.spellcheck = false;
  ta.value = translate("common.loading");
  ta.disabled = true;
  body.appendChild(ta);
  const pop = el("div", "edit-ac hidden");
  body.appendChild(pop);
  bindEditorAutocomplete(ta, a);
  artifactsFetch(`${API}/artifacts/${a.id}?_=${Date.now()}`)
    .then((r) => r.text())
    .then((text) => {
      ta.value = text;
      ta.disabled = false;
      ta.focus();
    })
    .catch(() => {
      ta.value = "";
      ta.disabled = false;
    });
  cancel.onclick = () => {
    _editing.value = null;
    renderViewer();
  };
  save.onclick = async () => {
    save.disabled = true;
    save.textContent = translate("common.saving");
    try {
      const edited = (await api(`/artifacts/${a.id}/edit`, {
        method: "POST",
        body: JSON.stringify({ content: ta.value }),
      })) as { version_id?: string } | null;
      syncArtifactVersion({ id: a.id, version_id: edited && edited.version_id }, true);
      _editing.value = null;
      const bust = _artBust.value || {};
      bust[a.id] = Date.now();
      hint(translate("artifact.saved", a.filename || ""));
      if (currentId.value) void loadArtifacts(currentId.value);
      if (provMode.value) callWindow("showProvenance", dockArtifact.value || a);
      else renderViewer();
    } catch (e) {
      save.disabled = false;
      save.textContent = translate("common.save");
      hint(translate("artifact.save.err", apiErrorText(e)), true);
    }
  };
}

async function setArtPriority(a: ArtifactRow, p: number, closeAfter?: boolean): Promise<void> {
  try {
    await api(`/artifacts/${a.id}/priority`, { method: "POST", body: JSON.stringify({ priority: p }) });
    a.priority = p;
    hint(p > 0 ? translate("artifact.starred") : p < 0 ? translate("artifact.hidden") : translate("artifact.unstarred"));
    if (currentId.value) void loadArtifacts(currentId.value);
    if (closeAfter && dockArtifact.value === a) closeTab(a.id);
  } catch (e) {
    hint(translate("artifact.priority.err", apiErrorText(e)), true);
  }
}

async function exportMetadata(a: ArtifactRow): Promise<void> {
  try {
    const [versions, lineage] = await Promise.all([
      api(`/artifacts/${a.id}/versions`).catch(() => ({ versions: [] })),
      api(`/artifacts/${a.id}/lineage`).catch(() => ({})),
    ]);
    const verRec = versions && typeof versions === "object" ? (versions as { versions?: unknown }) : {};
    const meta = {
      id: a.id,
      filename: a.filename,
      content_type: a.content_type,
      size_bytes: a.size_bytes,
      priority: a.priority || 0,
      versions: verRec.versions || [],
      lineage,
    };
    const blob = new Blob([JSON.stringify(meta, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = (a.filename || "artifact") + ".metadata.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    hint(translate("artifact.metadataExported"));
  } catch (e) {
    hint(translate("toast.exportFailed", apiErrorText(e)), true);
  }
}

function artifactMenu(anchor: Element, a: ArtifactRow): void {
  const starred = (a.priority || 0) > 0;
  const items: MenuItem[] = [
    { label: translate("menu.versionHistory"), icon: "clock", onClick: () => void showVersions(a) },
    { label: translate("menu.provenance"), icon: "provenance", onClick: () => callWindow("showProvenance", a) },
    { sep: true },
    {
      label: starred ? translate("menu.unstar") : translate("menu.star"),
      icon: "star",
      onClick: () => void setArtPriority(a, starred ? 0 : 1),
    },
    { label: translate("menu.hideFromList"), icon: "eye-off", onClick: () => void setArtPriority(a, -1, true) },
    {
      label: translate("menu.copyLink"),
      icon: "link",
      onClick: () => {
        try {
          const nav = (globalThis as { navigator?: { clipboard?: { writeText?: (s: string) => void } } })
            .navigator;
          if (nav && nav.clipboard && nav.clipboard.writeText)
            nav.clipboard.writeText(location.origin + API + "/artifacts/" + a.id);
        } catch {
          /* clipboard denied */
        }
        hint(translate("artifact.linkCopied"));
      },
    },
    {
      label: translate("common.edit"),
      icon: "pencil",
      onClick: () => {
        if (isTextEditable(a)) editArtifact(a);
        else hint(translate("artifact.notEditable"));
      },
    },
    { label: translate("folder.menu.rename"), icon: "pencil", onClick: () => void renameArtifact(a) },
    { label: translate("menu.exportMetadata"), icon: "file-text", onClick: () => void exportMetadata(a) },
    { sep: true },
    { label: translate("common.delete"), icon: "trash-2", danger: true, onClick: () => void deleteArtifact(a) },
  ];
  openMenu(anchor, items);
}

async function renderArtifactVersionDiff(
  panel: DiffPanel,
  a: ArtifactRow,
  fromVersion: string,
  toVersion: string,
  fromOrdinal: unknown,
  toOrdinal: unknown,
): Promise<void> {
  const request = (panel._diffRequest = (panel._diffRequest || 0) + 1);
  panel.classList.remove("hidden");
  panel.innerHTML = "";
  panel.appendChild(el("div", "ver-diff-title", translate("versions.diff.title", fromOrdinal, toOrdinal)));
  const status = el("div", "dock-empty", translate("versions.diff.loading"));
  panel.appendChild(status);
  try {
    const query = `from=${encodeURIComponent(fromVersion)}&to=${encodeURIComponent(toVersion)}`;
    const payload = (await api(`/artifacts/${encodeURIComponent(a.id)}/diff?${query}`)) as {
      diff?: string;
      changed?: boolean;
    };
    if (panel._diffRequest !== request) return;
    status.remove();
    const raw = String((payload && payload.diff) || "");
    if (!raw || (payload && payload.changed === false)) {
      panel.appendChild(el("div", "dock-empty", translate("versions.diff.empty")));
      return;
    }
    const limit = 200000,
      pre = el("pre", "ver-diff-body");
    // The unified diff is untrusted Artifact content. textContent keeps file
    // bytes inert even when they contain HTML/script syntax.
    pre.textContent = raw.slice(0, limit);
    panel.appendChild(pre);
    if (raw.length > limit)
      panel.appendChild(el("div", "ver-diff-note", translate("versions.diff.truncated", limit)));
  } catch (error) {
    if (panel._diffRequest !== request) return;
    status.textContent = translate("versions.diff.err", apiErrorText(error));
    status.classList.add("error");
  }
}

export async function showVersions(a: ArtifactRow): Promise<void> {
  if (typeof document === "undefined") return;
  _modalMode.value = "versions:" + a.id;
  const title = $("#modal-title");
  if (title) title.textContent = translate("versions.modal.title", a.filename || "");
  const dl = $("#modal-download");
  if (dl) dl.style.display = "none";
  const body = $("#modal-body");
  if (!body) return;
  body.innerHTML = "<div class='dock-empty'>" + translate("common.loading") + "</div>";
  openModalEl($("#modal"));
  const render = async (): Promise<void> => {
    let d: { versions?: VersionRow[] } | null = null;
    try {
      d = (await api(`/artifacts/${a.id}/versions`)) as { versions?: VersionRow[] };
    } catch (e) {
      body.textContent = translate("versions.load.err", (e as { message?: string }).message || String(e));
      return;
    }
    const vs = (d && d.versions) || [];
    body.innerHTML = "";
    const wrap = el("div", "ver-list"),
      diffPanel = el("section", "ver-diff hidden") as DiffPanel;
    if (!vs.length) wrap.appendChild(el("div", "dock-empty", translate("versions.empty")));
    vs.forEach((v) => {
      const row = el("div", "ver-row" + (v.is_latest ? " current" : ""));
      const info = el("div", "ver-info");
      const vt = el("div", "ver-title");
      vt.appendChild(el("span", "ver-ord", "v" + v.ordinal));
      if (v.is_latest) vt.appendChild(el("span", "ver-badge", translate("cust.models.activePill")));
      info.appendChild(vt);
      info.appendChild(el("div", "ver-meta", (bytes(v.size_bytes) || "") + " · " + ago(v.created_at)));
      row.appendChild(info);
      const acts = el("div", "ver-acts");
      const view = el("a", "outline-btn small", translate("common.view"));
      view.href = `${API}/artifacts/${v.version_id}`;
      view.target = "_blank";
      acts.appendChild(view);
      const previous = isTextEditable(a)
        ? vs.find((candidate) => Number(candidate.ordinal) === Number(v.ordinal) - 1)
        : null;
      if (previous) {
        const compare = el(
          "button",
          "outline-btn small",
          translate("versions.diff", previous.ordinal, v.ordinal),
        );
        compare.dataset.action = "compare-artifact-versions";
        compare.onclick = () =>
          void renderArtifactVersionDiff(
            diffPanel,
            a,
            previous.version_id,
            v.version_id,
            previous.ordinal,
            v.ordinal,
          );
        acts.appendChild(compare);
      }
      if (!v.is_latest) {
        const rb = el("button", "solid-btn small", translate("versions.restore"));
        rb.onclick = async () => {
          rb.disabled = true;
          rb.textContent = translate("versions.restoring");
          try {
            const restored = (await api(`/artifacts/${a.id}/versions/${v.version_id}/restore`, {
              method: "POST",
            })) as { artifact?: ArtifactRow } | null;
            syncArtifactVersion((restored && restored.artifact) || { id: a.id, version_id: v.version_id }, true);
            hint(translate("versions.restored", v.ordinal));
            const bust = _artBust.value || {};
            bust[a.id] = Date.now();
            if (currentId.value) void loadArtifacts(currentId.value);
            const docked = dockArtifact.value as ArtifactRow | null;
            if (docked && docked.id === a.id) {
              if (provMode.value) callWindow("showProvenance", docked);
              else renderViewer();
            }
            void render();
          } catch (e) {
            rb.disabled = false;
            rb.textContent = translate("versions.restore");
            hint(translate("versions.restore.err", apiErrorText(e)), true);
          }
        };
        acts.appendChild(rb);
      }
      row.appendChild(acts);
      wrap.appendChild(row);
      // Where this version's data came from, when it came from anywhere. The
      // envelope has been recorded on every retrieved version since retrieval
      // provenance existed and read by nothing, so a figure built on a live
      // API fetch looked exactly like one computed from thin air. Read-only,
      // and already allowlisted, bounded and redacted by the server -- the
      // client renders what it is given and derives nothing.
      if (v.retrieval_source) wrap.appendChild(retrievalSourcePanel(v.retrieval_source));
    });
    body.appendChild(wrap);
    body.appendChild(diffPanel);
  };
  void render();
}

/** app.js:9438-9456 plus F-17 M-03 version banner / deep-link copy. */
export function renderViewer(): void {
  if (typeof document === "undefined") return;
  const a = dockArtifact.value as ArtifactRow | null;
  const v = document.getElementById("dock-viewer");
  if (!v) return;
  edacTeardown();
  v.innerHTML = "";
  if (!a) {
    v.appendChild(el("div", "dock-empty", translate("viewer.empty")));
    return;
  }
  const banner = versionResolveMessage(viewerVersionState.value);
  if (banner) {
    v.appendChild(el("div", "files-version-error", banner));
    if (viewerVersionState.value?.status === "stale" || viewerVersionState.value?.status === "not-found") {
      return;
    }
  }
  const head = el("div", "viewer-head");
  head.appendChild(el("div", "vh-name", a.filename || "artifact"));
  const acts = el("div", "vh-acts");
  const copy = el("button", "outline-btn small", filesT("files.deeplink.copy"));
  copy.onclick = () => {
    const href = artifactDeepLinkHref(a.id, a._exactVersion ? a.version_id : null);
    const clip = (globalThis as { navigator?: { clipboard?: { writeText?: (s: string) => Promise<void> } } })
      .navigator?.clipboard?.writeText;
    if (isReady(clip)) void clip(href);
    copy.textContent = filesT("files.deeplink.copied");
  };
  const menuBtn = ghostIconBtn("more-vertical", translate("viewer.act.more"));
  menuBtn.onclick = () => artifactMenu(menuBtn, a);
  acts.appendChild(menuBtn);
  acts.appendChild(copy);
  if (!provMode.value && isTextEditable(a)) {
    const editBtn = ghostIconBtn("pencil", translate("common.edit"));
    editBtn.onclick = () => editArtifact(a);
    acts.appendChild(editBtn);
  }
  const maxBtn = ghostIconBtn("maximize-2", translate("viewer.act.fullscreen"));
  maxBtn.onclick = () => openArtifact(a);
  const dl = el("a", "icon-ghost") as HTMLAnchorElement;
  dl.innerHTML = icon("download", 16);
  dl.href = artUrl(a);
  dl.setAttribute("download", a.filename || "artifact");
  dl.title = translate("common.download");
  const closeBtn = ghostIconBtn("x", translate("common.close"));
  closeBtn.onclick = () => {
    if (provMode.value) {
      provMode.value = false;
      renderViewer();
    } else closeTab(a.id);
  };
  acts.appendChild(maxBtn);
  acts.appendChild(dl);
  acts.appendChild(closeBtn);
  head.appendChild(acts);
  v.appendChild(head);
  if (provMode.value) {
    renderProvenanceInto(v, a);
    decorateViewerWithProvenance();
    return;
  }
  molTeardown();
  const body = el("div", "viewer-body");
  v.appendChild(body);
  if (_editing.value === a.id) renderArtifactEditor(body, a);
  else renderArtifactBody(body, a);
  decorateViewerWithProvenance();
}
