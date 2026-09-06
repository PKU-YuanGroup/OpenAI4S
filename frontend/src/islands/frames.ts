/**
 * Artifact / Ketcher iframe sandbox helpers.
 *
 * Three layers must agree that untrusted Artifact HTML never runs script:
 * 1. response CSP `script-src 'none'` + `sandbox allow-same-origin`
 * 2. html-preview iframe `sandbox=""` (opaque origin; no allow-scripts)
 * 3. the noscript note next to the preview
 *
 * PDF iframes historically had no sandbox attribute (audit finding). F-18
 * adds the same empty sandbox as html-preview. Ketcher is first-party UI
 * served with `embeddable_security_headers` (`frame-ancestors 'self'`) and
 * must NOT get a sandbox attribute — scripts and same-origin are required.
 */

/** Empty sandbox: no scripts, no forms, no same-origin, no popups. */
export const ARTIFACT_IFRAME_SANDBOX = "";

export const KETCHER_PATH = "/ketcher";
export const KETCHER_ALLOW = "clipboard-read; clipboard-write";

export type ArtifactIframeKind = "pdf" | "html-preview";

export type SandboxTarget = {
  setAttribute: (name: string, value: string) => void;
  getAttribute?: (name: string) => string | null;
  removeAttribute?: (name: string) => void;
};

export type FrameTarget = SandboxTarget & {
  src: string;
};

/** app.js:8663-8664. PDF and html-preview share the empty sandbox token. */
export function applyArtifactIframeSandbox(
  frame: SandboxTarget,
  _kind: ArtifactIframeKind,
): void {
  frame.setAttribute("sandbox", ARTIFACT_IFRAME_SANDBOX);
}

export function ketcherFrameSrc(
  origin: string | null | undefined,
  artifactId?: string | null,
): string {
  const base = String(origin || "") + KETCHER_PATH;
  if (artifactId) return base + "?artifact_id=" + encodeURIComponent(artifactId);
  return base;
}

/**
 * Ketcher is a first-party `/ketcher` document (embeddable headers), not
 * Artifact bytes. Do not set sandbox: that would strip scripts the editor
 * needs. `allow` is clipboard only.
 */
export function applyKetcherFrame(
  frame: FrameTarget,
  origin: string | null | undefined,
  artifactId?: string | null,
): void {
  frame.src = ketcherFrameSrc(origin, artifactId);
  frame.setAttribute("allow", KETCHER_ALLOW);
  if (frame.removeAttribute) frame.removeAttribute("sandbox");
}

export function htmlPreviewSrc(origin: string | null | undefined, artifactId: string): string {
  return String(origin || "") + `/preview/${encodeURIComponent(artifactId)}`;
}
