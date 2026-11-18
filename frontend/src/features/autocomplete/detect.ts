/**
 * Trigger detection. Port of app.js:12946-12951 (composer) and 13151-13156
 * (editor). Pure: no DOM.
 *
 * Composer: a `@` / `#` / `/` token at the caret, started at a start-of-string
 * or whitespace boundary (a `/` also straight after CJK text). The query
 * cannot contain whitespace or another trigger character.
 *
 * Editor: an ASCII identifier of at least 2 chars. The regex excludes Han so
 * CJK/IME composition cannot open the popup. A range selection yields null.
 */

export type ComposerDetect = {
  trigger: "@" | "#" | "/";
  query: string;
  start: number;
  /** A `/` typed straight after CJK text ("请用/lit"), with no space before it. */
  afterCjk?: boolean;
};

export type EditorDetect = {
  query: string;
  start: number;
};

const COMPOSER_RE = /(^|\s)([@#/])([^\s@#/]*)$/;
/**
 * Chinese and Japanese put no space before a word, so "请用/lit" never met the
 * whitespace boundary above. Only `/` opens there: an `@` or `#` glued to CJK
 * text is ordinary prose far more often than a mention.
 */
const CJK_SLASH_RE =
  /[　-〿぀-ヿ㐀-䶿一-鿿豈-﫿＀-￯]\/([^\s@#/]*)$/;
const EDITOR_RE = /[A-Za-z_$][\w$]*$/;

export function acDetectFrom(before: string, pos: number): ComposerDetect | null {
  const m = before.match(COMPOSER_RE);
  if (m) {
    const trigger = m[2] as ComposerDetect["trigger"];
    const query = m[3] || "";
    return { trigger, query, start: pos - query.length - 1 };
  }
  const cjk = before.match(CJK_SLASH_RE);
  if (!cjk) return null;
  const query = cjk[1] || "";
  return { trigger: "/", query, start: pos - query.length - 1, afterCjk: true };
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
