import { API } from "../artifacts/api";
import type { ArtifactRow, TableWorkbenchState } from "./types";

export const PROFILE_FORBIDDEN = ["sort", "dir", "offset", "limit"] as const;
export const EXPORT_FORBIDDEN = ["offset", "limit"] as const;

export type ProfileQueryInput = {
  versionId: string;
  filters: Record<string, string>;
};

export type ExportQueryInput = {
  versionId: string;
  sort?: string;
  dir?: string;
  filters: Record<string, string>;
};

/**
 * Concrete version to send. A present `latest_version_id` is that snapshot's
 * id, not an omitted param (B-07: profile/export never silently use latest).
 */
export function resolvedTableVersionId(
  a: ArtifactRow,
  pageVersion?: string | null,
): string {
  return String(pageVersion || a.version_id || a.latest_version_id || "").trim();
}

function setFilters(search: URLSearchParams, filters: Record<string, string>): void {
  for (const [key, value] of Object.entries(filters || {})) {
    if (value) search.set("q_" + key, value);
  }
}

function assertForbidden(search: URLSearchParams, names: readonly string[], label: string): void {
  const hit = names.filter((name) => search.has(name));
  if (hit.length) {
    throw new Error(`${label} does not accept ${hit.join(", ")}`);
  }
}

/** `null` when version_id is missing — callers must not hit the profile route. */
export function tableProfileSearch(input: ProfileQueryInput): URLSearchParams | null {
  const versionId = String(input.versionId || "").trim();
  if (!versionId) return null;
  const search = new URLSearchParams();
  search.set("version_id", versionId);
  setFilters(search, input.filters);
  return search;
}

export function tableExportSearch(input: ExportQueryInput): URLSearchParams | null {
  const versionId = String(input.versionId || "").trim();
  if (!versionId) return null;
  const search = new URLSearchParams();
  search.set("version_id", versionId);
  if (input.sort) search.set("sort", input.sort);
  if (input.dir) search.set("dir", input.dir);
  setFilters(search, input.filters);
  return search;
}

export function tableProfilePath(artifactId: string, search: URLSearchParams): string {
  if (!search.get("version_id")) throw new Error("version_id is required");
  assertForbidden(search, PROFILE_FORBIDDEN, "profile");
  return `/artifacts/${encodeURIComponent(artifactId)}/table/profile?${search}`;
}

export function tableExportPath(artifactId: string, search: URLSearchParams): string {
  if (!search.get("version_id")) throw new Error("version_id is required");
  assertForbidden(search, EXPORT_FORBIDDEN, "export");
  return `/artifacts/${encodeURIComponent(artifactId)}/table/export.csv?${search}`;
}

/**
 * Same-origin download URL. The browser streams the CSV; JS must not
 * `fetch().text()` / `blob()` the body (that would materialize the file).
 */
export function tableExportHref(artifactId: string, search: URLSearchParams): string {
  return `${API}${tableExportPath(artifactId, search)}`;
}

export function exportHrefFromState(
  artifactId: string,
  versionId: string,
  state: Pick<TableWorkbenchState, "sort" | "dir" | "filters">,
): string | null {
  const search = tableExportSearch({
    versionId,
    sort: state.sort,
    dir: state.dir,
    filters: state.filters,
  });
  if (!search) return null;
  return tableExportHref(artifactId, search);
}
