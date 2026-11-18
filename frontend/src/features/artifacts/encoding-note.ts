/**
 * The visible half of UP-4: a preview decoded as anything but UTF-8 says which
 * encoding it used, so a reader can tell a GB18030 table from a mangled one.
 */

import { el } from "./api";
import { filesT } from "./copy";
import { encodingLabel } from "./text-encoding";

/** Append the note when `encoding` is not plain UTF-8; returns the node, if any. */
export function appendEncodingNote(container: HTMLElement, encoding: string): HTMLElement | null {
  if (!encoding || encoding === "utf-8") return null;
  const note = el("div", "renderer-encoding", filesT("viewer.encoding.detected", encodingLabel(encoding)));
  note.setAttribute("data-encoding", encoding);
  container.appendChild(note);
  return note;
}

/** A read option that notes a non-UTF-8 encoding in `container` while the view is still there. */
export function noteEncodingIn(container: HTMLElement): (encoding: string) => void {
  return (encoding) => {
    if (container.isConnected === false) return;
    appendEncodingNote(container, encoding);
  };
}
