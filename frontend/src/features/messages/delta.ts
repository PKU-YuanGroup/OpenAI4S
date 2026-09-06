/**
 * Incremental live-output append.
 *
 * `appendLiveOutput` (F-08, app.js:5361-5371) is the cap. This module turns
 * its whole-string result into a delta so a text node can `appendData` and
 * newline counting can look only at the increment — instead of rewriting
 * up to 1MB of `textContent` on every chunk (app.js:5492-5496).
 */

import {
  LIVE_OUTPUT_TRUNCATION,
  appendLiveOutput,
} from "../stream/cap";

export type LiveOutputDelta = {
  next: string;
  added: string;
  addedNewlines: number;
};

export function countNewlines(text: string): number {
  let n = 0;
  for (let i = 0; i < text.length; i++) {
    if (text.charCodeAt(i) === 10) n++;
  }
  return n;
}

/**
 * Cap-aware delta. `added` is empty when the truncation marker is already
 * present (idempotent no-op) or when `chunk` is empty.
 */
export function liveOutputDelta(
  current: string | null | undefined,
  chunk: string | null | undefined,
): LiveOutputDelta {
  const existing = String(current || "");
  const next = appendLiveOutput(existing, chunk);
  const added = next.length > existing.length ? next.slice(existing.length) : "";
  return { next, added, addedNewlines: countNewlines(added) };
}

export type AppendableText = { appendData: (data: string) => void };

export type StreamingPreHandle = {
  append: (chunk: string) => void;
  readonly text: string;
  readonly newlines: number;
  readonly truncated: boolean;
};

/**
 * Bind a text node. `initial` is the text already in the node (do not pass
 * it through `appendData` again). Subsequent `append` calls only push the
 * cap-aware delta.
 */
export function bindStreamingPre(
  textNode: AppendableText,
  initial = "",
): StreamingPreHandle {
  let text = initial;
  let newlines = countNewlines(initial);
  return {
    append(chunk: string): void {
      const { next, added, addedNewlines } = liveOutputDelta(text, chunk);
      if (added) textNode.appendData(added);
      text = next;
      newlines += addedNewlines;
    },
    get text(): string {
      return text;
    },
    get newlines(): number {
      return newlines;
    },
    get truncated(): boolean {
      return text.includes(LIVE_OUTPUT_TRUNCATION);
    },
  };
}

/**
 * app.js:5495 meta line. `n === 1 ? " line"` is unreachable (`n > 1` already
 * failed); kept so the string matches the original.
 */
export function toolMetaLabel(newlines: number): string {
  return newlines > 1
    ? newlines + (newlines === 1 ? " line" : " lines")
    : "done";
}
