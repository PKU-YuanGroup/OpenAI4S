/** Artifact DTO the Files dock and Viewer share. Port of `_artifact_json` fields. */

export type ArtifactRow = {
  id: string;
  artifact_id?: string;
  filename?: string | null;
  content_type?: string | null;
  size_bytes?: number | null;
  version_id?: string | null;
  latest_version_id?: string | null;
  checksum?: string | null;
  project_id?: string | null;
  root_frame_id?: string | null;
  priority?: number | null;
  created_at?: string | null;
  is_user_upload?: boolean | number | null;
  producing_cell_id?: string | null;
  /** When true, Viewer/artUrl pin this `version_id` and never fall back to latest. */
  _exactVersion?: boolean;
  [key: string]: unknown;
};

export type ArtifactPatch = {
  id?: unknown;
  artifact_id?: unknown;
  version_id?: unknown;
  latest_version_id?: unknown;
  checksum?: unknown;
  filename?: unknown;
  content_type?: unknown;
  [key: string]: unknown;
};

export type ArtifactVersionRow = {
  version_id: string;
  ordinal?: number;
  is_latest?: boolean;
  size_bytes?: number;
  content_type?: string | null;
  checksum?: string | null;
  producing_cell_id?: string | null;
  created_at?: string | null;
};

export type RendererCatalogItem = {
  renderer_id: string;
  label?: string;
  capabilities?: string[];
  sandboxed?: boolean;
  [key: string]: unknown;
};

export type RendererDescriptor = {
  artifact_id: string;
  version_id?: string;
  matched_by?: string;
  trusted_html?: boolean;
  renderer?: {
    renderer_id?: string;
    label?: string;
    capabilities?: string[];
    sandboxed?: boolean;
    [key: string]: unknown;
  };
  [key: string]: unknown;
};

export type FilesOrigin = "" | "uploaded" | "generated";

export type ArtifactIndexPage = {
  artifacts: ArtifactRow[];
  next_cursor: string | null;
  has_more: boolean;
};

export type ArtifactDeepLink = {
  artifactId: string;
  /** `null` means latest. A provided string must resolve exactly. */
  versionId: string | null;
};

export type VersionResolve =
  | { status: "latest"; artifact: ArtifactRow; versionId: string | null }
  | { status: "exact"; artifact: ArtifactRow; versionId: string }
  | { status: "not-found"; artifactId: string; versionId: string | null }
  | {
      status: "stale";
      artifactId: string;
      versionId: string;
      latestVersionId: string | null;
    };

/** app.js:8414 */
export const TEXT_EXT =
  /\.(md|markdown|txt|text|rst|log|py|ipynb|r|jl|js|ts|sh|bash|zsh|yaml|yml|toml|ini|cfg|conf|env|tex|bib|xml|css|sql|c|cc|cpp|h|hpp|java|go|rs|rb|php|fasta|fa|fastq|nwk|nb)$/i;

/** app.js:8415 */
export const MOL_EXT = /\.(pdb|cif|mmcif|ent|xyz|mol|mol2|sdf|gro)$/i;

/** B-06 / M-03 default page size. First screen ≤ 50. */
export const FILES_PAGE_SIZE = 50;
/** B-06 hard cap. */
export const FILES_MAX_PAGE_SIZE = 100;
