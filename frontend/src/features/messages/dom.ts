/** app.js:3-4 `$` / `el`, plus the #messages host used by the stream. */

export function $(sel: string): HTMLElement | null {
  try {
    if (typeof document === "undefined") return null;
    return document.querySelector(sel);
  } catch {
    return null;
  }
}

export function el(
  tag: string,
  className?: string | null,
  text?: string | null,
): HTMLElement {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

export function messagesHost(): HTMLElement | null {
  return $("#messages");
}

/**
 * The session's file strips ("This session's inputs / files",
 * `artifacts/ui.ts` `renderConversationArtifacts`) stay last when a turn adds
 * a row. A new turn used to remove one of them and leave the other where it
 * was, so the two followed different rules between repaints.
 */
export function keepStripsLast(host: HTMLElement | null = messagesHost()): void {
  if (!host || !host.children) return;
  for (const cls of ["uploaded", "generated"]) {
    const strip = Array.from(host.children).find((node) => node.classList?.contains(cls));
    if (strip && host.lastElementChild !== strip) host.appendChild(strip);
  }
}

/**
 * Guarantee the DOM contract nodes exist when a conversation opens. The
 * Shell renders both, so in the workbench this is a no-op; never call it
 * before `render()`, where Preact would reuse the node for another element.
 */
export function ensureMessageDom(): void {
  if (typeof document === "undefined") return;
  if (!document.getElementById("messages")) {
    const host = document.createElement("div");
    host.id = "messages";
    const mount = document.getElementById("app") || document.body;
    if (mount) mount.appendChild(host);
  }
  if (!document.getElementById("jump-pill")) {
    const pill = document.createElement("button");
    pill.id = "jump-pill";
    pill.type = "button";
    pill.className = "hidden";
    const messages = document.getElementById("messages");
    const parent = (messages && messages.parentNode) || document.body;
    if (parent) parent.appendChild(pill);
  }
}
