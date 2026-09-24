/**
 * Incremental live-output append.
 *
 * `appendLiveOutput` (F-08, app.js:5361-5371) is the cap. This module turns
 * its whole-string result into a delta so a text node can `appendData` and
 * newline counting can look only at the increment — instead of rewriting
 * up to 1MB of `textContent` on every chunk (app.js:5492-5496).
 */

import { LANG } from "../../i18n/runtime";
import {
  LIVE_OUTPUT_TRUNCATION,
  appendLiveOutput,
  liveOutputIncrement,
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
 * cap-aware delta, and only the delta is examined: the handle keeps the
 * length and whether the marker went in, instead of searching and slicing
 * the whole (up to 1MB) output on every chunk.
 */
export function bindStreamingPre(
  textNode: AppendableText,
  initial = "",
): StreamingPreHandle {
  let text = initial;
  let length = initial.length;
  let truncated = initial.includes(LIVE_OUTPUT_TRUNCATION);
  let newlines = countNewlines(initial);
  return {
    append(chunk: string): void {
      const step = liveOutputIncrement(length, truncated, chunk);
      truncated = step.truncated;
      if (!step.added) return;
      textNode.appendData(step.added);
      text += step.added;
      length += step.added.length;
      newlines += countNewlines(step.added);
    },
    get text(): string {
      return text;
    },
    get newlines(): number {
      return newlines;
    },
    get truncated(): boolean {
      return truncated;
    },
  };
}

/** Feature-local copy: app.js hard-coded the meta line in English. */
const META_COPY: Record<"en" | "zh", { lines: string; done: string }> = {
  en: { lines: "{0} lines", done: "done" },
  zh: { lines: "{0} 行", done: "完成" },
};

/** app.js:5495 meta line: the line count once there is more than one, else "done". */
export function toolMetaLabel(newlines: number): string {
  const copy = META_COPY[LANG === "zh" ? "zh" : "en"];
  return newlines > 1 ? copy.lines.replace("{0}", String(newlines)) : copy.done;
}
