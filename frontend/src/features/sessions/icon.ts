/** Line icons used by dashboard / session menus. Paths live in features/icons/paths.ts. */

import { iconSvg } from "../icons/paths";
import { el } from "./dom";

export function icon(name: string, size?: number, cls?: string): string {
  return iconSvg(name, size, cls);
}

export function iconEl(name: string, size?: number, cls?: string): Node {
  const wrap = el("span", "ic");
  wrap.innerHTML = icon(name, size, cls);
  return wrap.firstChild || wrap;
}

export function paintIcons(root?: ParentNode | null): void {
  const scope = root || (typeof document !== "undefined" ? document : null);
  if (!scope) return;
  scope.querySelectorAll("[data-icon]").forEach((node) => {
    const e = node as HTMLElement & { _painted?: boolean };
    if (e._painted) return;
    e.innerHTML = icon(e.dataset.icon || "", +(e.dataset.iconSize || 16) || 16);
    e._painted = true;
  });
}
