import {
  applyArtifactIframeSandbox,
  grantedHtmlPreviewSrc,
  htmlPreviewSrc,
  INTERACTIVE_ARTIFACT_SANDBOX,
  resolveSandboxOrigin,
} from "../../islands/frames";
import { api, el, translate } from "./api";
import { artifactRendererVersion } from "./cache";
import type { ArtifactRow } from "./types";

/** A grant already minted for one exact artifact version, reusable until near expiry. */
interface CachedGrant {
  src: string;
  expiresAt: number;
}

const grants = new Map<string, CachedGrant>();

/**
 * Renew this long before the server would refuse. A cached URL that navigates
 * a frame into a 404 is a blank canvas the caption cannot explain.
 */
const RENEWAL_MARGIN_MS = 60_000;

/** The verified alternate loopback origin for this page, or "" when the preview stays inert. */
export function sandboxOriginForPage(): string {
  return resolveSandboxOrigin(typeof location === "undefined" ? null : location);
}

/** Drop every remembered grant (tests, and a future explicit logout hook). */
export function forgetGrants(): void {
  grants.clear();
}

function grantTtlMs(grant: unknown): number {
  const seconds = grant && typeof grant === "object"
    ? (grant as Record<string, unknown>).expires_in : undefined;
  return typeof seconds === "number" && Number.isFinite(seconds) && seconds > 0
    ? seconds * 1000 : 0;
}

function upgrade(frame: HTMLIFrameElement, note: HTMLElement, src: string): void {
  frame.setAttribute("sandbox", INTERACTIVE_ARTIFACT_SANDBOX);
  frame.src = src;
  // A cross-origin frame cannot tell us whether its navigation was refused,
  // so the explanation is retargeted, never removed: a blank canvas still
  // has a caption that says what it is and what to do about it.
  note.textContent = translate("viewer.renderer.interactive");
}

/**
 * The inert document is the fallback, not the first paint: on an origin
 * that can never grant, or once a grant is refused, the frame loads the
 * app-origin preview; otherwise it waits for the grant and navigates once.
 */
export function renderHtmlPreview(content: HTMLElement, a: ArtifactRow): void {
  const frame = el("iframe") as HTMLIFrameElement;
  applyArtifactIframeSandbox(frame, "html-preview");
  const exactVersion = a._exactVersion && a.version_id ? String(a.version_id) : "";
  // `/preview/<version-id>` pins the bytes, forces text/html and lets a
  // relative <img src="figure.png"> resolve beside the report, which the raw
  // API byte route does not.
  const inertSrc = htmlPreviewSrc(exactVersion || a.id);
  content.appendChild(frame);
  const note = el("p", "muted renderer-noscript", translate("viewer.renderer.noscript"));
  content.appendChild(note);
  const attached = () => frame.isConnected && frame.parentNode === content;

  const origin = sandboxOriginForPage();
  if (!origin) {
    frame.src = inertSrc;
    return;
  }
  // Keyed on the version the grant will actually pin, not merely on whether
  // this is an exact-version request. A head preview's grant names whatever
  // version was current at mint; when a new capture lands, `syncArtifactVersion`
  // rewrites the row's `version_id` without setting `_exactVersion`, so a key
  // built from the flag alone would still hit and replay the superseded bytes
  // under a banner naming the new version.
  const key = `${a.id}:${exactVersion}:${artifactRendererVersion(a)}`;
  const cached = grants.get(key);
  if (cached && cached.expiresAt > Date.now()) {
    upgrade(frame, note, cached.src);
    return;
  }
  grants.delete(key);
  const suffix = exactVersion ? `?version_id=${encodeURIComponent(exactVersion)}` : "";
  api(`/artifacts/${encodeURIComponent(a.id)}/sandbox-grant${suffix}`, { method: "POST" })
    .then((grant) => {
      // A user may close the viewer or switch artifacts before the POST ends.
      if (!attached()) return;
      const src = grantedHtmlPreviewSrc(origin, grant, a.id);
      if (!src) {
        frame.src = inertSrc;
        return;
      }
      const ttl = grantTtlMs(grant);
      if (ttl > RENEWAL_MARGIN_MS) {
        grants.set(key, { src, expiresAt: Date.now() + ttl - RENEWAL_MARGIN_MS });
      }
      upgrade(frame, note, src);
    })
    .catch(() => {
      // Refused (409), unauthenticated, or unreachable: keep the inert
      // document and its explanation.
      if (attached()) frame.src = inertSrc;
    });
}
