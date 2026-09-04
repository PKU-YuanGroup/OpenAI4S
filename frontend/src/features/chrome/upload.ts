/**
 * Composer upload (file input, paste, drop) plus the first-session single
 * flight that keeps bytes and text in the same conversation.
 *
 * Port of app.js:11049-11199 — UPLOAD_STATE, readUploadFile,
 * createUploadSession, uploadBatchMatches, uploadFailureMatches, uploadFiles,
 * UPLOAD_WAIT_LIMIT_MS, pendingUploadsFor, waitForPendingUploads — with the
 * bindings at app.js:13751-13755 (file input reset), 13414-13418 (paste) and
 * 13420-13423 (drop).
 *
 * The destination is captured ONCE, at selection. The pre-port workbench read
 * `currentId.value` at POST time, so bytes retargeted to whichever
 * conversation happened to be open when FileReader finished.
 */

import { t } from "../../i18n/runtime";
import { defaultModelName } from "../../stores/customize";
import { _openGen, currentId, project } from "../../stores/session";
import { sub } from "../ws/connect";
import { api, apiErrorText } from "./api";
import { $, hint } from "./dom";
import { hostFn, isReady } from "./host";
import { effProject } from "./notes";

export interface UploadResult {
  ok: boolean;
  frameId: string | null;
  filename: string;
  error?: unknown;
}

export interface UploadBatch {
  /** The conversation open when the files were chosen; null means "none yet". */
  frameAtSelection: string | null;
  /** Resolved destination. Stays null until the shared creation settles. */
  targetFrameId: string | null;
  projectId: string | null;
  /** Identity of the creation promise this batch adopted, for matching. */
  targetSource: Promise<string> | null;
  targetPromise: Promise<string> | null;
  promise: Promise<UploadResult[]> | null;
}

export interface UploadFailure {
  frameId: string | null;
  projectId: string | null;
  results: UploadResult[];
}

export const UPLOAD_STATE: {
  pending: Set<UploadBatch>;
  failures: Set<UploadFailure>;
  creations: Map<string, Promise<string>>;
} = { pending: new Set(), failures: new Set(), creations: new Map() };

/**
 * app.js:11051. The old `split(",")[1] || ""` turned an unreadable file into a
 * successful upload of zero bytes; every failure mode rejects here instead.
 */
export function readUploadFile(file: File): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      const comma = result.indexOf(",");
      if (comma < 0) {
        reject(new Error("file could not be encoded"));
        return;
      }
      resolve(result.slice(comma + 1));
    };
    reader.onerror = () => reject(reader.error || new Error("file could not be read"));
    reader.onabort = () => reject(new Error("file read was cancelled"));
    reader.readAsDataURL(file);
  });
}

/**
 * app.js:11066.
 *
 * `fresh` opts out of the per-project single flight. Sharing exists so that
 * Attach and the first Send cannot split bytes from text across two sibling
 * frames; a caller that already has a conversation open has nothing to
 * strand, and adopting a stranger's promise there would silently answer an
 * explicit request for a NEW session with an existing one.
 */
export function createUploadSession(
  projectId: string | null,
  options?: { fresh?: boolean },
): Promise<string> {
  const shared = !(options && options.fresh);
  const key = projectId ? `project:${projectId}` : "project:<none>";
  const existing = shared ? UPLOAD_STATE.creations.get(key) : null;
  if (existing) return existing;
  const navigationGen = _openGen.value || 0;
  const self: { promise: Promise<string> | null } = { promise: null };
  const creating = (async (): Promise<string> => {
    const f = (await api("/frames", {
      method: "POST",
      body: JSON.stringify({
        project_id: projectId || undefined,
        model: defaultModelName.value,
      }),
    })) as { id?: string } | null;
    const frameId = (f && f.id) || "";
    if (!frameId) throw new Error("session creation returned no id");
    // Name the destination before openConversation. POST has already published
    // the id; waiting until this promise settles left targetFrameId null and
    // forced matchers to treat any same-project unresolved batch as ours.
    for (const pending of UPLOAD_STATE.pending) {
      if (pending.targetSource === self.promise && pending.targetFrameId === null) {
        pending.targetFrameId = frameId;
      }
    }
    // The upload began with no conversation. Open the one it created only if
    // the user has not navigated to another conversation OR another empty
    // project while the request was in flight. The upload remains bound to
    // frameId either way.
    const workspace = $("#workspace");
    const workspaceVisible = !!workspace && !workspace.classList.contains("hidden");
    if (
      !currentId.value &&
      (project.value || null) === (projectId || null) &&
      (_openGen.value || 0) === navigationGen &&
      workspaceVisible
    ) {
      currentId.value = frameId;
      sub(frameId);
      const loadSessions = hostFn("loadSessions");
      if (isReady(loadSessions)) await loadSessions();
      if (currentId.value === frameId && (project.value || null) === (projectId || null)) {
        const openConversation = hostFn("openConversation");
        if (isReady(openConversation)) await openConversation(frameId, projectId);
      }
    }
    return frameId;
  })();
  self.promise = creating;
  if (shared) {
    UPLOAD_STATE.creations.set(key, creating);
    void creating
      .finally(() => {
        if (UPLOAD_STATE.creations.get(key) === creating) UPLOAD_STATE.creations.delete(key);
      })
      .catch(() => {});
  } else {
    void creating.catch(() => {});
  }
  return creating;
}

/** app.js:11107. */
export function uploadBatchMatches(
  batch: UploadBatch,
  frameId: string | null,
  projectId: string | null,
  creationPromise: Promise<string> | null,
): boolean {
  return (
    batch.frameAtSelection === frameId ||
    batch.targetFrameId === frameId ||
    (!batch.frameAtSelection &&
      !!creationPromise &&
      batch.targetSource === creationPromise &&
      batch.projectId === projectId)
  );
}

/** app.js:11125. */
export function uploadFailureMatches(
  failure: UploadFailure,
  frameId: string | null,
  projectId: string | null,
): boolean {
  return frameId
    ? failure.frameId === frameId
    : !failure.frameId && failure.projectId === projectId;
}

/** app.js:11131. */
export function uploadFiles(
  files: ArrayLike<File> | FileList | null | undefined,
): Promise<UploadResult[]> {
  const selected = files ? Array.from(files as ArrayLike<File>) : [];
  if (!selected.length) return Promise.resolve([]);
  const frameId = currentId.value || null;
  const projectId = effProject() || project.value || null;
  // A deliberate retry supersedes a settled result for the same destination.
  // Otherwise yesterday's fast failure can block today's successful upload on
  // the next Enter even though the replacement bytes are already present.
  [...UPLOAD_STATE.failures].forEach((previous) => {
    if (uploadFailureMatches(previous, frameId, projectId)) UPLOAD_STATE.failures.delete(previous);
  });
  const batch: UploadBatch = {
    frameAtSelection: frameId,
    targetFrameId: frameId,
    projectId,
    targetSource: null,
    targetPromise: null,
    promise: null,
  };
  // Start (and share) first-session creation synchronously with registration,
  // before FileReader can finish and before Enter can dispatch a message.
  const target = frameId ? Promise.resolve(frameId) : createUploadSession(projectId);
  batch.targetSource = target;
  batch.targetPromise = target.then((targetFrame) => {
    batch.targetFrameId = targetFrame;
    return targetFrame;
  });
  const targetPromise = batch.targetPromise;
  const tasks = selected.map((file) =>
    (async (): Promise<UploadResult> => {
      let targetFrame: string | null = null;
      try {
        const prepared = await Promise.all([targetPromise, readUploadFile(file)]);
        targetFrame = prepared[0];
        const b64 = prepared[1];
        await api("/uploads", {
          method: "POST",
          body: JSON.stringify({
            filename: file.name,
            content_base64: b64,
            project_id: projectId || undefined,
            frame_id: targetFrame,
          }),
        });
        // Only the open conversation's file dock should be refreshed; the bytes
        // landed in targetFrame whether or not the user is still looking at it.
        if (currentId.value === targetFrame) {
          const loadArtifacts = hostFn("loadArtifacts");
          if (isReady(loadArtifacts)) loadArtifacts(targetFrame);
        }
        hint(t("upload.uploaded", file.name));
        return { ok: true, frameId: targetFrame, filename: file.name };
      } catch (error) {
        // FileReader can fail before the shared frame request settles. Wait for
        // that target only to bind the failure to the right composer; no bytes
        // are written on this path.
        if (!targetFrame) {
          try {
            targetFrame = await targetPromise;
          } catch {
            /* the creation itself failed; the failure stays frame-less */
          }
        }
        hint(t("upload.failed", apiErrorText(error)), true);
        return { ok: false, frameId: targetFrame, filename: file.name, error };
      }
    })(),
  );
  batch.promise = Promise.all(tasks)
    .then((results) => {
      const failed = results.filter((result) => !result.ok);
      if (failed.length) {
        UPLOAD_STATE.failures.add({
          frameId: results.find((result) => result.frameId)?.frameId || null,
          projectId,
          results: failed,
        });
        // Bound abandoned UI state even when a user never presses Enter again.
        while (UPLOAD_STATE.failures.size > 64) {
          const oldest = UPLOAD_STATE.failures.values().next().value;
          if (!oldest) break;
          UPLOAD_STATE.failures.delete(oldest);
        }
      }
      return results;
    })
    .finally(() => UPLOAD_STATE.pending.delete(batch));
  UPLOAD_STATE.pending.add(batch);
  return batch.promise;
}

/** A stuck /uploads must not pin the composer indefinitely; see send(). */
export const UPLOAD_WAIT_LIMIT_MS = 120000;

/** app.js:11191. */
export function pendingUploadsFor(
  frameId: string | null,
  projectId: string | null,
  creationPromise: Promise<string> | null,
): UploadBatch[] {
  return [...UPLOAD_STATE.pending].filter((batch) =>
    uploadBatchMatches(batch, frameId, projectId, creationPromise),
  );
}

/** app.js:11195. */
export async function waitForPendingUploads(
  frameId: string | null,
  projectId: string | null,
  creationPromise: Promise<string> | null,
): Promise<{ ok: boolean; frameId: string | null; failures: UploadResult[] }> {
  // Awaiting one round is not enough: a batch registered while we were waiting
  // is still a batch this send must not overtake.
  for (;;) {
    const pending = pendingUploadsFor(frameId, projectId, creationPromise);
    if (!pending.length) break;
    await Promise.all(pending.map((batch) => batch.promise));
  }
  const failures = [...UPLOAD_STATE.failures].filter((failure) =>
    uploadFailureMatches(failure, frameId, projectId),
  );
  const results = failures.reduce<UploadResult[]>((all, failure) => all.concat(failure.results), []);
  return { ok: results.length === 0, frameId, failures: results };
}

export function bindUpload(): void {
  const input = $("#file-input") as HTMLInputElement | null;
  if (input) {
    input.addEventListener("change", (e) => {
      const el = e.currentTarget as HTMLInputElement;
      const files = Array.from(el.files || []);
      el.value = ""; // choosing the same file again is an explicit retry
      void uploadFiles(files);
    });
  }
  const composer = $("#composer");
  if (composer) {
    composer.addEventListener("paste", (e) => {
      const paste = e as ClipboardEvent;
      const items = (paste.clipboardData || { items: [] }).items || [];
      const files: File[] = [];
      for (const it of items) {
        if (it.kind === "file") {
          const f = it.getAsFile();
          if (f) files.push(f);
        }
      }
      if (files.length) {
        paste.preventDefault();
        void uploadFiles(files);
        hint(t("upload.pasting"));
      }
    });
  }
  const dz = $(".composer-wrap") || composer;
  if (dz) {
    dz.addEventListener("dragover", (e) => {
      e.preventDefault();
      dz.classList.add("dragover");
    });
    dz.addEventListener("dragleave", (e) => {
      e.preventDefault();
      dz.classList.remove("dragover");
    });
    dz.addEventListener("drop", (e) => {
      e.preventDefault();
      dz.classList.remove("dragover");
      const drag = e as DragEvent;
      const files = drag.dataTransfer && drag.dataTransfer.files;
      if (files && files.length) {
        void uploadFiles(files);
        hint(t("upload.dropping"));
      }
    });
  }
}
