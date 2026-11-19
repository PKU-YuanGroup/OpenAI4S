/**
 * ART-8: a finished answer's key figures, shown under their links.
 *
 * The completion summary links every image it produced, and the Markdown
 * renderer deliberately loads no same-origin image named by text, so the
 * three plots an analysis was for stayed one click away each. After a message
 * is rendered, a link that points at this daemon's Artifact bytes and names
 * an image file gets that image placed under it. Only `/api/v1/artifacts/...`
 * hrefs qualify: the server wrote them, and they serve the Artifact's own
 * bytes from this origin.
 */

import { API } from "../ws/connect";

const IMAGE_NAME = /\.(png|jpe?g|gif|webp|svg)$/i;
/** More than this and the answer turns into a gallery; the strip has the rest. */
export const MAX_MESSAGE_FIGURES = 3;

type LinkLike = {
  getAttribute(name: string): string | null;
  textContent: string | null;
  closest?(selector: string): ElementLike | null;
  parentElement?: ElementLike | null;
};
type ElementLike = {
  querySelector(selector: string): unknown;
  appendChild(node: unknown): unknown;
};
type RootLike = { querySelectorAll(selector: string): ArrayLike<LinkLike> };

export function embedArtifactFigures(root: RootLike | null | undefined): number {
  if (!root || typeof root.querySelectorAll !== "function" || typeof document === "undefined") {
    return 0;
  }
  let added = 0;
  for (const link of Array.from(root.querySelectorAll("a"))) {
    if (added >= MAX_MESSAGE_FIGURES) break;
    const href = link.getAttribute("href") || "";
    const name = String(link.textContent || "").trim();
    if (!href.startsWith(API + "/artifacts/") || !IMAGE_NAME.test(name)) continue;
    const holder = (typeof link.closest === "function" ? link.closest("li") : null) || link.parentElement;
    if (!holder || holder.querySelector(".msg-fig")) continue;
    const img = document.createElement("img");
    img.className = "msg-fig";
    img.src = href;
    img.alt = name;
    img.loading = "lazy";
    holder.appendChild(img);
    added += 1;
  }
  return added;
}
