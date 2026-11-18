/**
 * `@` file pins the composer carries beside its text (ART-16).
 *
 * Picking a file from the `@` menu writes `@name` into the textarea and keeps
 * the exact version here. `send()` posts the pins as
 * `mentions: [{filename, version_id}]`. The old text form `@name#v-<id>` put
 * the version hash into the composer, the message bubble and the session
 * title.
 *
 * The text still decides which pins exist: a pin goes out only while its
 * `@name` token is in the message, so deleting the token drops the pin. The
 * server applies the same rule (`openai4s/server/artifact_refs.py`).
 */

export type ComposerMention = {
  filename: string;
  version_id: string;
  artifact_id: string;
  /** The session the picked file belongs to; "" when unknown. Not sent. */
  root_frame_id: string;
};

/** What a request may carry; mirrors `artifact_refs.MAX_MENTIONS`. */
export const MAX_MENTIONS = 64;

/** Pins for the current draft, oldest pick first. */
let picked: ComposerMention[] = [];

/**
 * What may follow `@name` for the token to still be that name. Mirrors
 * `artifact_refs._MENTION_END`: `@a.csv.bak` or `@a.csv#v-…` is another
 * token, while `@a.csv，` and `@a.csv这个` are still `a.csv`.
 */
const MENTION_END = "(?![A-Za-z0-9_#/-]|\\.[A-Za-z0-9])";

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function tokenRe(filename: string): RegExp {
  return new RegExp("(^|\\s)(@" + escapeRe(filename) + ")" + MENTION_END, "g");
}

/** How many `@filename` tokens `text` holds. */
export function mentionTokenCount(text: string, filename: string): number {
  if (!filename) return 0;
  const re = tokenRe(filename);
  let count = 0;
  while (re.exec(text) !== null) count += 1;
  return count;
}

/** Keep the pin a pick made. Picking the same version again moves it last. */
export function rememberMention(m: ComposerMention): void {
  if (!m.filename || !m.version_id) return;
  picked = picked.filter((p) => p.version_id !== m.version_id);
  picked.push({ ...m });
  if (picked.length > MAX_MENTIONS) picked = picked.slice(-MAX_MENTIONS);
}

/**
 * The pins `text` still carries, oldest pick first. A filename picked more
 * often than it now appears keeps its latest picks: deleting `@data.csv` and
 * picking another `@data.csv` sends the second version, not both.
 */
export function composerMentions(text: string): ComposerMention[] {
  const left = new Map<string, number>();
  const kept: ComposerMention[] = [];
  for (let i = picked.length - 1; i >= 0; i--) {
    const m = picked[i]!;
    const n = left.has(m.filename) ? left.get(m.filename)! : mentionTokenCount(text, m.filename);
    left.set(m.filename, Math.max(0, n - 1));
    if (n > 0) kept.unshift({ ...m });
  }
  return kept;
}

/** The request field: what the server resolves, and nothing else. */
export function mentionsPayload(
  list: readonly ComposerMention[],
): Array<{ filename: string; version_id: string }> {
  return list.map((m) => ({ filename: m.filename, version_id: m.version_id }));
}

/** The draft these pins belonged to was sent. */
export function clearMentions(): void {
  picked = [];
}

/** A refused message went back into the composer: its pins go back with it. */
export function restoreMentions(list: readonly ComposerMention[]): void {
  for (const m of list) rememberMention(m);
}

/** `text` with every token of the pinned filenames overwritten by spaces. */
export function blankMentionTokens(text: string, list: readonly ComposerMention[]): string {
  let out = text;
  for (const name of new Set(list.map((m) => m.filename))) {
    out = out.replace(tokenRe(name), (_all, lead: string, token: string) => lead + " ".repeat(token.length));
  }
  return out;
}

/** A text-form pin's version suffix, at the start of an `@` token. */
const PINNED_SUFFIX = /(^|\s)(@[^\s@#]+)#v-[0-9A-Za-z]{6,}/g;

/**
 * `@name#v-<id>` as a person reads it: `@name`. For messages a client wrote
 * in the text form; the stored row keeps what was sent, the version is shown
 * by the chip under the bubble.
 */
export function stripRefVersions(text: string): string {
  return text.replace(PINNED_SUFFIX, "$1$2");
}
