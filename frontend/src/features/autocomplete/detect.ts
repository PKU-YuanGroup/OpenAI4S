/**
 * Trigger detection. Port of app.js:12946-12951 (composer) and 13151-13156
 * (editor). Pure: no DOM.
 *
 * Composer: a `@` / `#` / `/` token at the caret, started at a start-of-string
 * or whitespace boundary. The query cannot contain whitespace or another
 * trigger character.
 *
 * Editor: an ASCII identifier of at least 2 chars. The regex excludes Han so
 * CJK/IME composition cannot open the popup. A range selection yields null.
 */

export type ComposerDetect = {
  trigger: "@" | "#" | "/";
  query: string;
  start: number;
};

export type EditorDetect = {
  query: string;
  start: number;
};

const COMPOSER_RE = /(^|\s)([@#/])([^\s@#/]*)$/;
const EDITOR_RE = /[A-Za-z_$][\w$]*$/;

export function acDetectFrom(before: string, pos: number): ComposerDetect | null {
  const m = before.match(COMPOSER_RE);
  if (!m) return null;
  const trigger = m[2] as ComposerDetect["trigger"];
  const query = m[3] || "";
  return { trigger, query, start: pos - query.length - 1 };
}

export function edacDetectFrom(
  value: string,
  selectionStart: number,
  selectionEnd: number = selectionStart,
): EditorDetect | null {
  if (selectionStart !== selectionEnd) return null;
  const m = value.slice(0, selectionStart).match(EDITOR_RE);
  if (!m || !m[0] || m[0].length < 2) return null;
  return { query: m[0], start: selectionStart - m[0].length };
}

export function edacExt(filename: string | null | undefined): string {
  const m = (filename || "").toLowerCase().match(/\.([a-z0-9]+)$/);
  return m && m[1] ? m[1] : "";
}
