/**
 * Artifact / Ketcher iframe sandbox helpers.
 *
 * Artifact HTML starts inert. Only a scoped grant on the alternate loopback
 * origin can enable scripts; PDF stays inert. Ketcher is first-party UI
 * served with `embeddable_security_headers` (`frame-ancestors 'self'`) and
 * must NOT get a sandbox attribute — scripts and same-origin are required.
 */

/** Empty sandbox: no scripts, no forms, no same-origin, no popups. */
export const ARTIFACT_IFRAME_SANDBOX = "";
export const INTERACTIVE_ARTIFACT_SANDBOX = "allow-scripts allow-same-origin";

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

/** PDF and the initial HTML preview share the empty sandbox token. */
export function applyArtifactIframeSandbox(
  frame: SandboxTarget,
  _kind: ArtifactIframeKind,
): void {
  frame.setAttribute("sandbox", ARTIFACT_IFRAME_SANDBOX);
}

/** First-party editor: always the app origin, never an artifact origin. */
export function ketcherFrameSrc(artifactId?: string | null): string {
  if (artifactId) return KETCHER_PATH + "?artifact_id=" + encodeURIComponent(artifactId);
  return KETCHER_PATH;
}

/**
 * Ketcher is a first-party `/ketcher` document (embeddable headers), not
 * Artifact bytes. Do not set sandbox: that would strip scripts the editor
 * needs. `allow` is clipboard only.
 */
export function applyKetcherFrame(frame: FrameTarget, artifactId?: string | null): void {
  frame.src = ketcherFrameSrc(artifactId);
  frame.setAttribute("allow", KETCHER_ALLOW);
  if (frame.removeAttribute) frame.removeAttribute("sandbox");
}

/** The inert app-origin preview of an artifact id or a version id. */
export function htmlPreviewSrc(ident: string): string {
  return `/preview/${encodeURIComponent(ident)}`;
}

type PreviewLocation = Pick<Location, "hostname" | "protocol" | "port">;

/**
 * Only the daemon's other loopback name at this HTTP port is verified. There
 * is no override: the server never injects one, and the grant response is
 * compared against this value before the frame navigates anywhere.
 */
export function resolveSandboxOrigin(loc: PreviewLocation | null | undefined): string {
  if (!loc || loc.protocol !== "http:") return "";
  const other = loc.hostname === "127.0.0.1" ? "http://localhost" :
    loc.hostname === "localhost" ? "http://127.0.0.1" : "";
  if (!other) return "";
  const url = new URL(other);
  url.port = loc.port;
  return url.origin;
}

/** Accept only the granted document, never a protocol-relative or API URL. */
export function grantedHtmlPreviewSrc(
  origin: string,
  grant: unknown,
  artifactId: string,
): string {
  if (!origin || !grant || typeof grant !== "object") return "";
  const { path, origin: grantedOrigin } = grant as Record<string, unknown>;
  if (grantedOrigin !== origin || typeof path !== "string") return "";
  const match = /^\/sandbox\/([A-Za-z0-9_.-]+)\/preview\/([^/?#]+)$/.exec(path);
  if (!match || match[2] !== encodeURIComponent(artifactId)) return "";
  return origin + path;
}
