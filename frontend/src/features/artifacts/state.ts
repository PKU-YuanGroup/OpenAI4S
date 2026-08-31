import { signal } from "@preact/signals";
import type { ArtifactRow, FilesOrigin, VersionResolve } from "./types";

/** How the Files dock obtained the current project listing. */
export type FilesIndexMode = "idle" | "index" | "error";

/** Filename substring. Bound into the artifact-index `q` query. */
export const filesQuery = signal("");
/** Content-type substring. Bound into `content_type`. */
export const filesContentType = signal("");
/** Origin closed set: "" | uploaded | generated. */
export const filesOrigin = signal<FilesOrigin>("");
export const filesHasMore = signal(false);
export const filesNextCursor = signal<string | null>(null);
/**
 * Filter fingerprint the current cursor was issued under.
 * A mismatch (q / content_type / origin / project) drops the cursor
 * rather than sending it to a different listing.
 */
export const filesCursorFilter = signal<string | null>(null);
/** Accumulated pages currently painted. First screen ≤ FILES_PAGE_SIZE. */
export const filesIndexItems = signal<ArtifactRow[]>([]);
export const filesIndexLoading = signal(false);
export const filesIndexMode = signal<FilesIndexMode>("idle");
export const filesIndexError = signal<string | null>(null);
/** Generation token: Project/filter switch drops late responses. */
export const filesIndexReq = signal(0);
/** Exact-version resolve outcome for the open Viewer. */
export const viewerVersionState = signal<VersionResolve | null>(null);

export function resetFilesIndexState(): void {
  filesQuery.value = "";
  filesContentType.value = "";
  filesOrigin.value = "";
  filesHasMore.value = false;
  filesNextCursor.value = null;
  filesCursorFilter.value = null;
  filesIndexItems.value = [];
  filesIndexLoading.value = false;
  filesIndexMode.value = "idle";
  filesIndexError.value = null;
  filesIndexReq.value = (filesIndexReq.value || 0) + 1;
  viewerVersionState.value = null;
}
