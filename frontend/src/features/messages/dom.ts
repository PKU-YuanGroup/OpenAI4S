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
 * Guarantee the DOM contract nodes exist so feed/down/openConversation can
 * run before the layout lane composes MessageList into the shell.
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
