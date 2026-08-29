import { api, asVersionList, isApiStatus } from "./api";
import { filesT } from "./copy";
import { viewerVersionState } from "./state";
import type {
  ArtifactDeepLink,
  ArtifactRow,
  ArtifactVersionRow,
  VersionResolve,
} from "./types";

/** Parse `?artifact={id}&version_id={vid}`. Missing version_id → latest. */
export function parseArtifactDeepLink(
  search: string | null | undefined,
): ArtifactDeepLink | null {
  const raw = String(search || "");
  const trimmed = raw.startsWith("?") ? raw.slice(1) : raw;
  if (!trimmed) return null;
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(trimmed);
  } catch {
    return null;
  }
  const artifactId = (params.get("artifact") || "").trim();
  if (!artifactId) return null;
  const versionRaw = params.get("version_id");
  const versionId = versionRaw != null && versionRaw.trim() ? versionRaw.trim() : null;
  return { artifactId, versionId };
}

/** Copyable `?artifact={id}&version_id={vid}`. Omit version_id for latest. */
export function artifactDeepLinkSearch(
  artifactId: string,
  versionId?: string | null,
): string {
  const params = new URLSearchParams();
  params.set("artifact", artifactId);
  if (versionId) params.set("version_id", versionId);
  return `?${params.toString()}`;
}

export function artifactDeepLinkHref(
  artifactId: string,
  versionId?: string | null,
): string {
  if (typeof location === "undefined") return artifactDeepLinkSearch(artifactId, versionId);
  return location.pathname + artifactDeepLinkSearch(artifactId, versionId);
}

function rowFromUnknown(value: unknown, fallbackId: string): ArtifactRow | null {
  if (Array.isArray(value)) {
    const first = value[0];
    return rowFromUnknown(first, fallbackId);
  }
  if (!value || typeof value !== "object") return null;
  const rec = value as Record<string, unknown>;
  const id = rec.id || rec.artifact_id || fallbackId;
  if (id == null || id === "") return null;
  return { ...rec, id: String(id) } as ArtifactRow;
}

/**
 * Resolve a deep link to an immutable version.
 *
 * - omitted version_id → latest
 * - provided version_id → exact match only; never substitutes latest
 * - missing exact version → `stale` (artifact exists) or `not-found`
 */
export async function resolveArtifactVersion(
  link: ArtifactDeepLink,
  fetchVersions: (id: string) => Promise<ArtifactVersionRow[]> = defaultFetchVersions,
  fetchArtifact: (id: string) => Promise<ArtifactRow | null> = defaultFetchArtifact,
): Promise<VersionResolve> {
  const artifact = await fetchArtifact(link.artifactId);
  if (!artifact) {
    return { status: "not-found", artifactId: link.artifactId, versionId: link.versionId };
  }
  if (!link.versionId) {
    return {
      status: "latest",
      artifact,
      versionId: artifact.version_id || artifact.latest_version_id || null,
    };
  }
  const wanted = link.versionId;
  const versions = await fetchVersions(link.artifactId);
  const exact = versions.find((row) => row.version_id === wanted);
  if (exact) {
    return {
      status: "exact",
      artifact: {
        ...artifact,
        version_id: exact.version_id,
        _exactVersion: true,
        size_bytes: exact.size_bytes ?? artifact.size_bytes,
        content_type: exact.content_type ?? artifact.content_type,
        checksum: exact.checksum ?? artifact.checksum,
      },
      versionId: exact.version_id,
    };
  }
  const latest =
    versions.find((row) => row.is_latest)?.version_id ||
    artifact.version_id ||
    artifact.latest_version_id ||
    null;
  return {
    status: "stale",
    artifactId: link.artifactId,
    versionId: wanted,
    latestVersionId: latest,
  };
}

async function defaultFetchVersions(id: string): Promise<ArtifactVersionRow[]> {
  try {
    const body = await api(`/artifacts/${encodeURIComponent(id)}/versions`);
    return asVersionList(body);
  } catch (e) {
    if (isApiStatus(e, 404)) return [];
    throw e;
  }
}

async function defaultFetchArtifact(id: string): Promise<ArtifactRow | null> {
  try {
    const versions = await defaultFetchVersions(id);
    if (!versions.length) return { id, artifact_id: id };
    const latest = versions.find((row) => row.is_latest) || versions[0];
    if (!latest) return { id, artifact_id: id };
    return {
      id,
      artifact_id: id,
      version_id: latest.version_id,
      latest_version_id: latest.version_id,
      size_bytes: latest.size_bytes,
      content_type: latest.content_type,
      checksum: latest.checksum,
    };
  } catch (e) {
    if (isApiStatus(e, 404)) return null;
    throw e;
  }
}

export function versionResolveMessage(result: VersionResolve | null): string | null {
  if (!result) return null;
  if (result.status === "stale") {
    return filesT("files.version.stale", result.versionId, result.latestVersionId || "—");
  }
  if (result.status === "not-found") return filesT("files.version.notFound");
  return null;
}

export function rememberViewerVersion(result: VersionResolve): void {
  viewerVersionState.value = result;
}

export { rowFromUnknown };
