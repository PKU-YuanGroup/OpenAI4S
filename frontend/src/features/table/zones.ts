import { el } from "../artifacts/api";
import { tableT } from "./copy";
import {
  clampHistogram,
  histogramBounds,
  isNumericBin,
  maxBinCount,
  MAX_TABLE_PROFILE_BINS,
  readApproximate,
} from "./histogram";
import type { TableCatalogPosture, TableProfile, TableProfileColumn, TableViewerPlan } from "./types";

export type ZoneRenderOptions = {
  plan: TableViewerPlan;
  exportHref: string | null;
  versionId: string;
  filename?: string | null;
};

function fmtStat(value: number | null | undefined): string {
  if (value == null || (typeof value === "number" && !Number.isFinite(value))) return "—";
  return String(value);
}

function uniqueLabel(unique: number, approximate: boolean): string {
  const n = Number.isFinite(unique) ? unique : 0;
  if (approximate) return tableT("wb.table.unique.approx", n);
  return String(n);
}

function paintSchema(section: HTMLElement, columns: TableProfileColumn[], approximate: boolean): void {
  section.appendChild(el("h3", "wb-table-zone-title", tableT("wb.table.schema")));
  const table = el("table", "wb-table-schema-table");
  const head = el("tr");
  for (const key of ["", "wb.table.col.type", "wb.table.col.missing", "wb.table.col.unique"]) {
    head.appendChild(el("th", null, key ? tableT(key) : ""));
  }
  table.appendChild(head);
  for (const col of columns) {
    const tr = el("tr");
    tr.appendChild(el("th", null, col.name));
    tr.appendChild(el("td", "wb-table-col-type", col.type || ""));
    tr.appendChild(el("td", null, String(col.missing ?? 0)));
    const unique = el("td", approximate ? "wb-table-unique-approx" : "wb-table-unique", uniqueLabel(col.unique, approximate));
    if (approximate) unique.dataset.approximate = "true";
    tr.appendChild(unique);
    table.appendChild(tr);
  }
  section.appendChild(table);
}

function paintHistogram(hold: HTMLElement, col: TableProfileColumn): void {
  const { bins, clipped } = clampHistogram(col.histogram, MAX_TABLE_PROFILE_BINS);
  if (!bins.length) {
    hold.appendChild(el("div", "wb-hist-empty", tableT("wb.table.hist.empty")));
    return;
  }
  const chart = el("div", "wb-hist");
  chart.dataset.bins = String(bins.length);
  if (clipped) chart.dataset.clipped = "true";
  const bounds = histogramBounds(bins);
  if (bounds) {
    chart.dataset.start = String(bounds.start);
    chart.dataset.end = String(bounds.end);
  }
  const peak = Math.max(1, maxBinCount(bins));
  for (const bin of bins) {
    const bar = el("div", "wb-hist-bar");
    const pct = Math.max(2, Math.round((bin.count / peak) * 100));
    bar.style.height = `${pct}%`;
    bar.dataset.count = String(bin.count);
    if (isNumericBin(bin)) {
      bar.dataset.start = String(bin.start);
      bar.dataset.end = String(bin.end);
      bar.title = `${bin.start}–${bin.end}: ${bin.count}`;
    } else {
      bar.dataset.value = bin.value;
      bar.title = `${bin.value}: ${bin.count}`;
    }
    chart.appendChild(bar);
  }
  hold.appendChild(chart);
  hold.appendChild(el("div", "wb-hist-meta", tableT("wb.table.hist.bins", bins.length)));
}

function paintDistribution(section: HTMLElement, columns: TableProfileColumn[]): void {
  section.appendChild(el("h3", "wb-table-zone-title", tableT("wb.table.distribution")));
  for (const col of columns) {
    const card = el("div", "wb-table-dist-col");
    card.dataset.column = col.name;
    const head = el("div", "wb-table-dist-head");
    head.appendChild(el("strong", null, col.name));
    head.appendChild(el("span", "wb-table-col-type", col.type || ""));
    card.appendChild(head);
    if (col.type === "integer" || col.type === "number") {
      const stats = el("dl", "wb-table-stats");
      for (const [key, value] of [
        [tableT("wb.table.col.min"), fmtStat(col.min)],
        [tableT("wb.table.col.max"), fmtStat(col.max)],
        [tableT("wb.table.col.mean"), fmtStat(col.mean)],
      ] as Array<[string, string]>) {
        stats.appendChild(el("dt", null, key));
        stats.appendChild(el("dd", null, value));
      }
      card.appendChild(stats);
    }
    paintHistogram(card, col);
    section.appendChild(card);
  }
}

function paintExport(
  section: HTMLElement,
  opts: ZoneRenderOptions,
  posture: TableCatalogPosture,
  profile: TableProfile | null,
): void {
  section.appendChild(el("h3", "wb-table-zone-title", tableT("wb.table.export")));
  if (profile && profile.filtered_rows != null) {
    section.appendChild(
      el("div", "wb-table-export-meta", tableT("wb.table.filteredRows", profile.filtered_rows)),
    );
  }
  section.appendChild(el("p", "wb-table-export-note", tableT("wb.table.export.note")));
  if (opts.exportHref) {
    const link = el("a", "solid-btn small wb-table-export-link", tableT("wb.table.export.csv"));
    link.setAttribute("href", opts.exportHref);
    link.setAttribute("download", "");
    section.appendChild(link);
  } else {
    const missing = el("div", "wb-table-export-missing", tableT("wb.table.profile.needVersion"));
    missing.dataset.missingVersion = "true";
    section.appendChild(missing);
  }
  if (posture.parquet) {
    const badge = el("div", "wb-table-parquet", tableT("wb.table.parquet.on"));
    badge.dataset.parquet = "true";
    section.appendChild(badge);
  }
}

/**
 * Schema / Distribution / Export. Approximate is a visible banner, never
 * rewritten as exact. Export is an `<a href>` so the browser streams the
 * file; this function never fetches the CSV body.
 */
export function renderTableZones(
  container: HTMLElement,
  profile: TableProfile | null,
  posture: TableCatalogPosture,
  opts: ZoneRenderOptions,
  error?: string | null,
): HTMLElement {
  const plan = opts.plan;
  const wrap = el("div", "wb-table-zones");
  const approximate = readApproximate(profile);
  wrap.dataset.approximate = approximate ? "true" : "false";
  if (opts.versionId) wrap.dataset.versionId = opts.versionId;
  if (approximate) {
    const banner = el("div", "wb-table-approx");
    banner.dataset.approximate = "true";
    banner.setAttribute("role", "status");
    banner.appendChild(el("strong", null, tableT("wb.table.approximate")));
    banner.appendChild(el("span", null, tableT("wb.table.approximate.hint")));
    wrap.appendChild(banner);
  }
  if (error) {
    wrap.appendChild(el("div", "wb-table-profile-error", error));
  }
  const columns = (profile && Array.isArray(profile.columns) ? profile.columns : []) as TableProfileColumn[];
  if (plan.schema) {
    const section = el("section", "wb-table-schema");
    paintSchema(section, columns, approximate);
    wrap.appendChild(section);
  }
  if (plan.distribution) {
    const section = el("section", "wb-table-distribution");
    paintDistribution(section, columns);
    wrap.appendChild(section);
  }
  if (plan.export) {
    const section = el("section", "wb-table-export-zone");
    paintExport(section, opts, posture, profile);
    wrap.appendChild(section);
  }
  container.appendChild(wrap);
  return wrap;
}
