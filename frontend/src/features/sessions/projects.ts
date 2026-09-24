/** Projects: menu, modal, research view. app.js:6767-6913, 6840-6861. */

import { publicText } from "../scrub/scrub";
import { actionTimelineCard } from "../timeline/island";
import type { TimelineGroup } from "../timeline/types";
import { t } from "../../i18n";
import {
  _openGen,
  currentId,
  editingProject,
  project,
  projects,
  projectsQuery,
} from "../../stores/session";
import { _modalMode } from "../../stores/ui";
import { api, apiErrorText } from "./api";
import { binds } from "./binds";
import { hint, reportFailure } from "./chrome";
import { showDashboard, showWorkspace } from "./dashboard";
import { $, closeModalEl, el, openModalEl } from "./dom";
import { iconEl } from "./icon";
import { callLane } from "./lane";
import {
  loadProjects,
  loadSessions,
  loadSessionsForScope,
  normalizeProjectQuery,
  sessionListScope,
} from "./load";
import { beginNavigation } from "./navigation";
import { chooseSessionPackage, downloadArtifactBundle } from "./actions";
import { recoverConversation } from "../messages/open";

type ProjectLike = {
  project_id?: string;
  id?: string;
  name?: string;
  description?: string;
  context?: string;
  agent_context?: string;
};

export function projName(id: string | null | undefined): string {
  const p = (projects.value as ProjectLike[]).find((x) => (x.project_id || x.id) === id);
  return p ? p.name || t("proj.fallbackName") : t("proj.fallbackName");
}

export function sanitizeProjectLineage(payload: unknown): {
  project_id: string;
  nodes: Array<Record<string, unknown>>;
  edges: Array<{ from: string; to: string; kind: string }>;
  artifact_count: number;
  version_count: number;
  truncated: boolean;
} {
  const source = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  const nodes = (Array.isArray(source.nodes) ? source.nodes : [])
    .slice(0, 5000)
    .map((item) => {
      const rec = item && typeof item === "object" ? (item as Record<string, unknown>) : {};
      return {
        id: publicText(rec.id, 160),
        kind: publicText(rec.kind, 48),
        artifact_id: publicText(rec.artifact_id, 120),
        version_id: publicText(rec.version_id, 120),
        filename: publicText(rec.filename, 240),
        root_frame_id: publicText(rec.root_frame_id, 120),
        cell_id: publicText(rec.cell_id || rec.producing_cell_id, 120),
        created_at: rec.created_at,
        latest: !!rec.latest,
      };
    })
    .filter((item) => item.id);
  const ids = new Set(nodes.map((item) => item.id));
  const edges = (Array.isArray(source.edges) ? source.edges : [])
    .slice(0, 10000)
    .map((item) => {
      const rec = item && typeof item === "object" ? (item as Record<string, unknown>) : {};
      return {
        from: publicText(rec.from, 160),
        to: publicText(rec.to, 160),
        kind: publicText(rec.kind, 48),
      };
    })
    .filter((item) => ids.has(item.from) && ids.has(item.to));
  return {
    project_id: publicText(source.project_id, 120),
    nodes,
    edges,
    artifact_count: Number.isFinite(+source.artifact_count!) ? Math.max(0, +source.artifact_count!) : 0,
    version_count: Number.isFinite(+source.version_count!) ? Math.max(0, +source.version_count!) : 0,
    truncated: !!source.truncated,
  };
}

export async function openProjectResearchView(initialTab = "timeline"): Promise<void> {
  if (!project.value) return;
  const projectId = project.value;
  const mode = "project-research:" + projectId;
  _modalMode.value = mode;
  const title = $("#modal-title");
  if (title) title.textContent = t("projectResearch.title", projName(projectId));
  const dl = $("#modal-download");
  if (dl) dl.style.display = "none";
  const body = $("#modal-body");
  if (!body) return;
  body.innerHTML = "";
  $("#modal")?.classList.remove("hidden");
  const tabs = el("div", "project-research-tabs");
  const content = el("div", "project-research-content");
  body.appendChild(tabs);
  body.appendChild(content);
  const cache: Record<string, unknown> = { timeline: null, lineage: null };
  const renderTimeline = (data: {
    session_count?: number;
    total_count?: number;
    count?: number;
    groups?: Array<{ session?: { name?: string; root_frame_id?: string } }>;
  }) => {
    content.innerHTML = "";
    const summary = el(
      "div",
      "project-research-summary",
      t("projectResearch.timelineSummary", data.session_count || 0, data.total_count || data.count || 0),
    );
    content.appendChild(summary);
    if (!(data.groups || []).length) {
      content.appendChild(el("div", "dock-empty", t("timeline.empty")));
      return;
    }
    (data.groups || []).forEach((group) => {
      const wrapper = el("div", "project-timeline-entry");
      if (group.session) {
        wrapper.appendChild(
          el(
            "div",
            "project-session-label",
            group.session.name || publicText(group.session.root_frame_id, 12),
          ),
        );
      }
      wrapper.appendChild(actionTimelineCard(group as TimelineGroup));
      content.appendChild(wrapper);
    });
  };
  const renderLineage = (data: ReturnType<typeof sanitizeProjectLineage>) => {
    content.innerHTML = "";
    content.appendChild(
      el(
        "div",
        "project-research-summary",
        t("projectResearch.lineageSummary", data.artifact_count, data.version_count, data.edges.length),
      ),
    );
    const byId = new Map((data.nodes || []).map((item) => [item.id, item]));
    (data.nodes || [])
      .filter((item) => item.kind === "artifact_version")
      .forEach((item) => {
        const row = el("div", "project-lineage-node");
        row.appendChild(
          el("span", "project-lineage-name", String(item.filename || publicText(item.version_id, 12))),
        );
        if (item.latest) row.appendChild(el("span", "timeline-pill", t("projectResearch.latest")));
        if (item.cell_id) {
          row.appendChild(el("span", "project-lineage-cell", publicText(item.cell_id, 12)));
        }
        content.appendChild(row);
      });
    if (!(data.nodes || []).some((item) => item.kind === "artifact_version")) {
      content.appendChild(el("div", "dock-empty", t("projectResearch.noLineage")));
    }
    if ((data.edges || []).length) {
      const edges = el("details", "project-lineage-edges");
      edges.appendChild(el("summary", null, t("projectResearch.edges", data.edges.length)));
      data.edges.slice(0, 500).forEach((edge) => {
        const from = byId.get(edge.from);
        const to = byId.get(edge.to);
        edges.appendChild(
          el(
            "div",
            "project-lineage-edge",
            String(
              (from && (from.filename || from.cell_id)) || publicText(edge.from, 12),
            ) +
              " → " +
              String((to && (to.filename || to.cell_id)) || publicText(edge.to, 12)),
          ),
        );
      });
      content.appendChild(edges);
    }
  };
  const select = async (tab: string) => {
    if (_modalMode.value !== mode) return;
    Array.from(tabs.children).forEach((button) => {
      (button as HTMLElement).classList.toggle("active", (button as HTMLElement).dataset.tab === tab);
    });
    content.innerHTML = "";
    content.appendChild(el("div", "dock-empty", t("common.loading")));
    try {
      if (!cache[tab]) {
        if (tab === "timeline") {
          const raw = await api(
            `/projects/${encodeURIComponent(projectId)}/action-timeline?limit=500`,
          );
          cache[tab] = callLane("sanitizeActionTimeline", raw) ?? raw;
        } else {
          cache[tab] = sanitizeProjectLineage(
            await api(`/projects/${encodeURIComponent(projectId)}/lineage?limit=2000`),
          );
        }
      }
      if (_modalMode.value !== mode) return;
      if (tab === "timeline") renderTimeline(cache[tab] as Parameters<typeof renderTimeline>[0]);
      else renderLineage(cache[tab] as ReturnType<typeof sanitizeProjectLineage>);
    } catch (error) {
      if (_modalMode.value === mode) {
        content.innerHTML = "";
        content.appendChild(
          el("div", "timeline-error", publicText((error as Error) && (error as Error).message, 240)),
        );
      }
    }
  };
  (
    [
      ["timeline", t("projectResearch.timeline")],
      ["lineage", t("projectResearch.lineage")],
    ] as Array<[string, string]>
  ).forEach(([key, label]) => {
    const button = el("button", "seg-btn", label);
    button.type = "button";
    button.dataset.tab = key;
    button.onclick = () => {
      void select(key);
    };
    tabs.appendChild(button);
  });
  void select(initialTab === "lineage" ? "lineage" : "timeline");
}

export function renderProjMenu(): void {
  const current = $("#proj-current");
  if (current) {
    // Code owns this label now (see setTitle): a static repaint must not put
    // the markup's "Project" back over the current project's name.
    current.removeAttribute("data-i18n");
    current.textContent = project.value ? projName(project.value) : t("proj.current.allSessions");
  }
  const m = $("#proj-menu");
  if (!m) return;
  m.innerHTML = "";
  const item = (label: string, iconName: string, onClick: () => void) => {
    const it = el("div", "proj-item");
    it.setAttribute("role", "menuitem");
    const group = el("span");
    group.style.cssText = "display:flex;align-items:center;gap:6px";
    group.appendChild(iconEl(iconName, 16));
    group.appendChild(el("span", null, label));
    it.appendChild(group);
    it.onclick = () => {
      $("#proj-menu")?.classList.add("hidden");
      onClick();
    };
    m.appendChild(it);
    return it;
  };
  if (project.value) {
    const cur = (projects.value as ProjectLike[]).find(
      (p) => (p.project_id || p.id) === project.value,
    );
    if (cur) item(t("proj.menu.settings"), "settings", () => openProjectModal(cur));
    item(t("projectResearch.menu"), "provenance", () => {
      void openProjectResearchView("timeline");
    });
    item(t("sessionPackage.import"), "cloud-upload", () => {
      try {
        chooseSessionPackage();
      } catch (error) {
        reportFailure(error);
      }
    });
    item(t("proj.menu.downloadArtifacts"), "download", () => {
      try {
        downloadArtifactBundle(
          `/api/v1/projects/${encodeURIComponent(project.value as string)}/artifacts.zip`,
          `${projName(project.value)}-artifacts.zip`,
        );
      } catch (error) {
        reportFailure(error);
      }
    });
    m.appendChild(el("div", "ctx-sep"));
  }
  item(t("proj.menu.allProjects"), "arrow-left", showDashboard);
  (projects.value as ProjectLike[]).forEach((p) => {
    if ((p.project_id || p.id) !== project.value) {
      item((p.name || t("proj.fallbackName")).slice(0, 26), "box", () =>
        selectProject(p.project_id || p.id || ""),
      );
    }
  });
  m.appendChild(el("div", "ctx-sep"));
  item(t("proj.menu.newProject"), "plus", () => openProjectModal());
  m.setAttribute("role", "menu");
}

// The menu filters the sidebar without replacing the open conversation. It
// cancels pending project opens, but must not retire that conversation's reads.
let projectFilterVersion = 0;

export function selectProject(id: string): void {
  projectFilterVersion += 1;
  project.value = id;
  // The switcher changes the sidebar scope while keeping the open frame open.
  // Its Files snapshot is project-scoped, so it has to be read again.
  if (currentId.value) callLane("loadArtifacts", currentId.value);
  $("#proj-menu")?.classList.add("hidden");
  renderProjMenu();
  void loadSessions();
}

// openProject retires the open conversation's reads the moment it starts (the
// bump below). Running to completion always handed the view to a conversation;
// standing down for a menu filter handed it to nobody -- "load earlier" stayed
// on Loading…, a history still loading never painted and offered no Retry, the
// resume watchdog stopped. If the filter is why we stood down and nobody else
// has taken the view since, give it an owner again: reload the conversation
// that is showing, or, once the workspace was revealed with none, open the
// project the menu chose.
async function reclaimView(gen: number, filterVersion: number, workspaceShown: boolean): Promise<void> {
  if (_openGen.value !== gen || projectFilterVersion === filterVersion) return;
  if (currentId.value) await binds.openConversation(currentId.value, project.value);
  else if (workspaceShown && project.value) await openProject(project.value);
}

/** `replaceUrl`: routing resolves a project address, see `routeInitialView`. */
export async function openProject(id: string, options?: { replaceUrl?: boolean }): Promise<void> {
  // A project trip is navigation: bump the generation so any continuation still
  // parked on an await (an upload-created session about to open its
  // conversation, a resume watchdog) sees a stale token and stands down instead
  // of yanking the view back to where it started.
  const gen = beginNavigation();
  const filterVersion = projectFilterVersion;
  await loadProjects();
  if (_openGen.value !== gen || projectFilterVersion !== filterVersion) return reclaimView(gen, filterVersion, false);
  project.value = id;
  showWorkspace();
  // Follow a newer read for this same project rather than racing it, and treat
  // a failed or superseded read as "not known yet" instead of "no sessions":
  // creating a conversation on a read that never landed is how an existing
  // project got a stray empty session.
  const result = await loadSessionsForScope(sessionListScope());
  if (_openGen.value !== gen || projectFilterVersion !== filterVersion || project.value !== id) return reclaimView(gen, filterVersion, true);
  renderProjMenu();
  if (result.status !== "loaded") {
    // This navigation already retired the visible frame's history and
    // watchdog reads. A failed directory cannot leave that frame ownerless,
    // and reopening it would rewrite its address with the sidebar's project.
    const retained = currentId.value;
    if (retained) {
      if (_openGen.value !== gen || currentId.value !== retained) return;
      await Promise.allSettled([
        recoverConversation(retained, gen),
        Promise.resolve(callLane("loadArtifacts", retained)),
        Promise.resolve(callLane("loadExecutionLog", retained)),
        Promise.resolve(callLane("loadWorkbenchState", retained)),
      ]);
    }
    return;
  }
  const first = result.rows.find((row) => row.project_id === id);
  // Await the conversation. Fire-and-forget let openProject's callers (routing,
  // dashboard rows, createProject) return before the session existed, so the
  // next navigation raced the open this call had not finished.
  // The child takes its own generation; ownership checks belong before this
  // handoff, not after it.
  if (first?.id) {
    if (options?.replaceUrl) await binds.openConversation(first.id, id, { replaceUrl: true });
    else await binds.openConversation(first.id, id);
  } else await binds.newSession(id);
}

export async function createProject(
  name: string,
  description: string,
  context: string,
): Promise<void> {
  // A navigation that started while the POST was in flight owns the view now;
  // this creation must not yank it to the project it just made.
  const gen = _openGen.value;
  const p = (await api("/projects", {
    method: "POST",
    body: JSON.stringify({ name, description, context }),
  })) as ProjectLike;
  if (_openGen.value !== gen) return;
  await loadProjects();
  if (_openGen.value !== gen) return;
  await openProject(p.project_id || p.id || "");
}

export function closeProjectModal(): void {
  closeModalEl($("#proj-modal"));
  editingProject.value = null;
}

export function openProjectModal(proj?: ProjectLike | null): void {
  const p = proj || null;
  editingProject.value = p ? p.project_id || p.id : null;
  const title = $("#proj-modal .modal-head span");
  if (title) title.textContent = t(p ? "projModal.editTitle" : "projModal.title");
  const name = $("#pm-name") as HTMLInputElement | null;
  const desc = $("#pm-desc") as HTMLTextAreaElement | null;
  const ctx = $("#pm-ctx") as HTMLTextAreaElement | null;
  if (name) name.value = p ? p.name || "" : "";
  if (desc) desc.value = p ? p.description || "" : "";
  if (ctx) ctx.value = p ? p.context || p.agent_context || "" : "";
  const create = $("#pm-create");
  if (create) create.textContent = t(p ? "common.save" : "projModal.create");
  $("#pm-delete")?.classList.toggle("hidden", !p);
  openModalEl($("#proj-modal"));
  requestAnimationFrame(() => name?.focus());
}

export async function submitProjectModal(): Promise<void> {
  const btn = $("#pm-create") as HTMLButtonElement | null;
  const nameEl = $("#pm-name") as HTMLInputElement | null;
  const name = (nameEl?.value || "").trim() || t("palette.action.newProject");
  if (btn) btn.disabled = true;
  try {
    if (editingProject.value) {
      await api(`/projects/${editingProject.value}`, {
        method: "PATCH",
        body: JSON.stringify({
          name,
          description: ($("#pm-desc") as HTMLTextAreaElement | null)?.value,
          context: ($("#pm-ctx") as HTMLTextAreaElement | null)?.value,
        }),
      });
      // The directory carries the new name to the header and the switcher; a
      // dashboard showing a search reloads the page its box describes too.
      const dashVisible = !$("#dashboard")?.classList.contains("hidden");
      const q = dashVisible ? normalizeProjectQuery(String(projectsQuery.value || "")) : "";
      await Promise.all([loadProjects(), q ? loadProjects({ q }) : null]);
      renderProjMenu();
      if (dashVisible) binds.renderDashProjects();
      closeProjectModal();
    } else {
      await createProject(
        name,
        ($("#pm-desc") as HTMLTextAreaElement | null)?.value || "",
        ($("#pm-ctx") as HTMLTextAreaElement | null)?.value || "",
      );
      closeProjectModal();
    }
  } catch (e) {
    hint(t("artifact.save.err", apiErrorText(e)), true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

export async function deleteProject(id: string): Promise<void> {
  try {
    await api("/projects/" + id, { method: "DELETE" });
    closeProjectModal();
    await loadProjects();
    if (project.value === id) {
      project.value = null;
      showDashboard();
    } else renderProjMenu();
  } catch (e) {
    hint(t("toast.deleteFailed", apiErrorText(e)), true);
  }
}
