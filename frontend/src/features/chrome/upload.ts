/**
 * Composer upload (file input, paste, drop). Port of app.js:10899-10907,
 * paste 13414-13418, drop 13420-13423.
 */

import { currentId, project } from "../../stores/session";
import { defaultModelName } from "../../stores/customize";
import { t } from "../../i18n/runtime";
import { sub } from "../ws/connect";
import { api, apiErrorText } from "./api";
import { $, hint } from "./dom";
import { hostFn, isReady } from "./host";

export function uploadFiles(files: ArrayLike<File> | FileList | null | undefined): void {
  if (!files) return;
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    if (!file) continue;
    const rd = new FileReader();
    rd.onload = async () => {
      const raw = rd.result;
      const b64 = (typeof raw === "string" ? raw.split(",")[1] : "") || "";
      try {
        if (!currentId.value) {
          const f = (await api("/frames", {
            method: "POST",
            body: JSON.stringify({
              project_id: project.value || undefined,
              model: defaultModelName.value,
            }),
          })) as { id?: string };
          if (f.id) {
            currentId.value = f.id;
            sub(f.id);
            const loadSessions = hostFn("loadSessions");
            if (isReady(loadSessions)) await loadSessions();
            const openConversation = hostFn("openConversation");
            if (isReady(openConversation)) {
              await openConversation(f.id, project.value);
            }
          }
        }
        await api("/uploads", {
          method: "POST",
          body: JSON.stringify({
            filename: file.name,
            content_base64: b64,
            project_id: project.value || undefined,
            frame_id: currentId.value,
          }),
        });
        const loadArtifacts = hostFn("loadArtifacts");
        if (isReady(loadArtifacts) && currentId.value) loadArtifacts(currentId.value);
        hint(t("upload.uploaded", file.name));
      } catch (e) {
        hint(t("upload.failed", apiErrorText(e)), true);
      }
    };
    rd.readAsDataURL(file);
  }
}

export function bindUpload(): void {
  const input = $("#file-input") as HTMLInputElement | null;
  if (input) {
    input.addEventListener("change", (e) => {
      const files = (e.currentTarget as HTMLInputElement).files;
      uploadFiles(files);
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
        uploadFiles(files);
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
        uploadFiles(files);
        hint(t("upload.dropping"));
      }
    });
  }
}
