/**
 * Ketcher iframe. Port of app.js:8913-8920 and 10834.
 *
 * `/ketcher` is a first-party document served with embeddable_security_headers
 * (`frame-ancestors 'self'`). The iframe must not carry a sandbox attribute.
 */

import { sandboxOrigin } from "../stores/session";
import { openModalEl } from "../features/chrome/modal";
import type { ArtifactRow } from "../features/artifacts/types";
import { $, el } from "./dom";
import { applyKetcherFrame } from "./frames";
import { translate } from "./host";

/** app.js:10834 + workbench path 8918 (`?artifact_id=` when an artifact is open). */
export function openKetcher(a?: ArtifactRow | null): void {
  if (typeof document === "undefined") return;
  const title = $("#modal-title");
  if (title) title.textContent = translate("ketcher.modalTitle");
  const dl = $("#modal-download");
  if (dl) dl.style.display = "none";
  const body = $("#modal-body");
  if (!body) return;
  body.innerHTML = "";
  const frame = el("iframe");
  applyKetcherFrame(frame, sandboxOrigin.value || "", a && a.id ? a.id : null);
  body.appendChild(frame);
  openModalEl($("#modal"));
}
