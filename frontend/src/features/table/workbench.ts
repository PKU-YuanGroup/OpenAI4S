import { artifactWorkbench, _kc } from "../../stores/notebook";
import {
  api,
  apiErrorText,
  el,
  fetchArtifactText,
  looksBinary,
  translate,
} from "../artifacts/api";
import { parseTable } from "../csv/csv";
import { renderSheet } from "../artifacts/sheet";
import { planTableViewer, tableCatalogPosture } from "./catalog";
import { tableT } from "./copy";
import {
  exportHrefFromState,
  resolvedTableVersionId,
  tableProfilePath,
  tableProfileSearch,
} from "./query";
import type {
  ArtifactRow,
  TablePagePayload,
  TableProfile,
  TableRendererOptions,
  TableWorkbenchState,
} from "./types";
import { renderTableZones } from "./zones";

export function readWorkbenchFlag(): boolean {
  if (artifactWorkbench.value) return true;
  const st = _kc.value.st;
  if (st && typeof st === "object" && (st as { artifact_workbench?: unknown }).artifact_workbench) {
    return true;
  }
  return false;
}

/** app.js:8718-8722 filter `col:value` shorthand. */
export function payloadFilters(text: string, a: ArtifactRow): Record<string, string> {
  const value = String(text || "").trim();
  if (!value) return {};
  const named = value.match(/^([^:]+):(.*)$/);
  if (named) return { [(named[1] || "").trim()]: (named[2] || "").trim() };
  return { [((a && a.filename) || "col").replace(/\.[^.]+$/, "")]: value };
}

function renderLegacyFailure(container: HTMLElement, a: ArtifactRow, url: string): void {
  container.innerHTML = "";
  const card = el("div", "renderer-fallback");
  card.appendChild(el("div", "renderer-fallback-text", translate("viewer.renderer.error")));
  const download = el("a", "outline-btn small", translate("common.download"));
  download.setAttribute("href", url);
  download.setAttribute("download", a.filename || "artifact");
  card.appendChild(download);
  container.appendChild(card);
}

/** Flag-off path: client parse + capped sheet. Never hits /table/profile or /export.csv. */
export function renderLegacyTable(container: HTMLElement, a: ArtifactRow, url: string): void {
  fetchArtifactText(url)
    .then((text) => {
      if (!container.isConnected) return;
      if (looksBinary(text)) {
        const card = el("div", "download-artifact");
        card.appendChild(el("strong", null, a.filename || "artifact"));
        const link = el("a", "solid-btn small", translate("common.download"));
        link.setAttribute("href", url);
        link.setAttribute("download", a.filename || "artifact");
        card.appendChild(link);
        container.appendChild(card);
        return;
      }
      const rows = parseTable(text, a);
      if (rows && rows.length) renderSheet(container, rows);
      else {
        const pre = el("pre", "renderer-source");
        pre.textContent = text.slice(0, 300000);
        container.appendChild(pre);
      }
    })
    .catch(() => renderLegacyFailure(container, a, url));
}

export function renderWorkbenchTable(
  container: HTMLElement,
  a: ArtifactRow,
  options: TableRendererOptions = {},
): void {
  const posture = tableCatalogPosture(
    { capabilities: options.capabilities || [] },
    { workbenchOn: true },
  );
  const plan = planTableViewer(posture);
  const state: TableWorkbenchState = {
    sort: "",
    dir: "asc",
    filters: {},
    offset: 0,
    limit: 50,
  };
  const chrome = el("div", "wb-table");
  const controls = el("div", "wb-table-controls");
  const filter = el("input", "wb-filter");
  filter.placeholder = translate("wb.table.filter");
  const prev = el("button", "outline-btn small", translate("wb.table.prev"));
  const next = el("button", "outline-btn small", translate("wb.table.next"));
  const meta = el("div", "wb-table-meta");
  controls.appendChild(filter);
  controls.appendChild(prev);
  controls.appendChild(next);
  chrome.appendChild(controls);
  chrome.appendChild(meta);
  const hold = el("div", "wb-table-hold");
  chrome.appendChild(hold);
  const zonesHost = el("div", "wb-table-zones-host");
  chrome.appendChild(zonesHost);
  container.appendChild(chrome);

  let request = 0;
  const load = async () => {
    const gen = ++request;
    const query = new URLSearchParams({
      sort: state.sort,
      dir: state.dir,
      offset: String(state.offset),
      limit: String(state.limit),
    });
    if (a._exactVersion && a.version_id) query.set("version_id", String(a.version_id));
    Object.entries(state.filters).forEach(([key, value]) => {
      if (value) query.set("q_" + key, value);
    });
    let payload: TablePagePayload;
    try {
      payload = (await api(`/artifacts/${encodeURIComponent(a.id)}/table?${query}`)) as TablePagePayload;
    } catch (error) {
      if (gen !== request) return;
      hold.textContent = apiErrorText(error);
      return;
    }
    if (gen !== request || !container.isConnected) return;
    const rows = payload.rows || [];
    const offset = payload.offset || 0;
    const total = payload.total_rows || 0;
    meta.textContent = translate(
      "wb.table.meta",
      total,
      offset + 1,
      Math.min(offset + rows.length, total),
    );
    hold.innerHTML = "";
    const table = el("table", "sheet");
    const head = el("tr");
    (payload.columns || []).forEach((name) => {
      const th = el("th", payload.sorted_by === name ? "wb-sorted" : "", name);
      th.onclick = () => {
        state.sort = name;
        state.dir = payload.sorted_by === name && state.dir === "asc" ? "desc" : "asc";
        state.offset = 0;
        void load();
      };
      head.appendChild(th);
    });
    table.appendChild(head);
    rows.forEach((row) => {
      const tr = el("tr");
      (payload.columns || []).forEach((_, index) =>
        tr.appendChild(el("td", null, String(row[index] ?? ""))),
      );
      table.appendChild(tr);
    });
    hold.appendChild(table);
    prev.disabled = offset <= 0;
    next.disabled = offset + rows.length >= total;

    const versionId = resolvedTableVersionId(a, payload.version_id);
    zonesHost.innerHTML = "";
    if (!plan.schema && !plan.distribution && !plan.export) return;

    const exportHref = plan.export ? exportHrefFromState(a.id, versionId, state) : null;
    const zoneOpts = {
      plan,
      exportHref,
      versionId,
      filename: a.filename,
    };
    if (!versionId && (plan.schema || plan.distribution || plan.export)) {
      renderTableZones(zonesHost, null, posture, zoneOpts, tableT("wb.table.profile.needVersion"));
      return;
    }
    let profile: TableProfile | null = null;
    let profileError: string | null = null;
    if (plan.schema || plan.distribution) {
      try {
        const search = tableProfileSearch({ versionId, filters: state.filters });
        if (!search) {
          profileError = tableT("wb.table.profile.needVersion");
        } else {
          profile = (await api(tableProfilePath(a.id, search))) as TableProfile;
        }
      } catch (error) {
        profileError = apiErrorText(error);
      }
    }
    if (gen !== request || !container.isConnected) return;
    zonesHost.innerHTML = "";
    renderTableZones(zonesHost, profile, posture, zoneOpts, profileError);
  };
  filter.onchange = () => {
    state.filters = payloadFilters((filter as HTMLInputElement).value, a);
    state.offset = 0;
    void load();
  };
  prev.onclick = () => {
    state.offset = Math.max(0, state.offset - state.limit);
    void load();
  };
  next.onclick = () => {
    state.offset += state.limit;
    void load();
  };
  void load();
}

export function renderTableArtifact(
  container: HTMLElement,
  a: ArtifactRow,
  url: string,
  options: TableRendererOptions = {},
): void {
  const workbenchOn = options.workbenchOn ?? readWorkbenchFlag();
  const posture = tableCatalogPosture(
    { capabilities: options.capabilities || [] },
    { workbenchOn },
  );
  const plan = planTableViewer(posture);
  if (plan.mode === "legacy-sheet") return renderLegacyTable(container, a, url);
  return renderWorkbenchTable(container, a, { ...options, workbenchOn: true });
}
