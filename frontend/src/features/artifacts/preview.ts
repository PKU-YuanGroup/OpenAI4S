import {
  applyArtifactIframeSandbox,
  grantedHtmlPreviewSrc,
  htmlPreviewSrc,
  INTERACTIVE_ARTIFACT_SANDBOX,
  resolveSandboxOrigin,
} from "../../islands/frames";
import { sandboxOrigin } from "../../stores/session";
import { api, el, translate } from "./api";
import { artUrl } from "./cache";
import type { ArtifactRow } from "./types";

/** Initialize the production workbench using the same verified origin policy. */
export function initializeSandboxOrigin(target?: Record<string, unknown>): void {
  const config = target?.__OPERON__;
  const override = config && typeof config === "object"
    ? (config as Record<string, unknown>).sandboxOrigin : undefined;
  sandboxOrigin.value = resolveSandboxOrigin(
    typeof location === "undefined" ? null : location,
    override,
  );
}

/** The inert preview remains usable if an interactive grant is unavailable. */
export function renderHtmlPreview(content: HTMLElement, a: ArtifactRow): void {
  const frame = el("iframe");
  applyArtifactIframeSandbox(frame, "html-preview");
  const exactVersion = a._exactVersion && a.version_id ? a.version_id : "";
  frame.src = exactVersion ? artUrl(a) : htmlPreviewSrc("", a.id);
  content.appendChild(frame);
  const note = el("p", "muted renderer-noscript", translate("viewer.renderer.noscript"));
  content.appendChild(note);

  // Revalidate the store at use time; compatibility window exports can write it.
  const origin = sandboxOrigin.value && resolveSandboxOrigin(
    typeof location === "undefined" ? null : location,
    sandboxOrigin.value,
  );
  if (!origin) return;
  const suffix = exactVersion ? `?version_id=${encodeURIComponent(exactVersion)}` : "";
  api(`/artifacts/${encodeURIComponent(a.id)}/sandbox-grant${suffix}`, { method: "POST" })
    .then((grant) => {
      // A user may close the viewer or switch artifacts before the POST ends.
      if (!frame.isConnected || frame.parentNode !== content) return;
      const src = grantedHtmlPreviewSrc(origin, grant, a.id);
      if (!src) return;
      frame.setAttribute("sandbox", INTERACTIVE_ARTIFACT_SANDBOX);
      frame.src = src;
      note.remove();
    })
    .catch(() => { /* Preserve the inert document and its explanation. */ });
}
