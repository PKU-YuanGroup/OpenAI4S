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

/**
 * The line a cell's stream puts between its code and what it printed
 * (`NOTEBOOK_DIVIDER` in `openai4s/server/cell_run.py`).
 */
export const OUTPUT_DIVIDER = "----- output -----";
const DIVIDER_LINE = "\n" + OUTPUT_DIVIDER + "\n";

export type StreamingPreHandle = {
  append: (chunk: string) => void;
  readonly text: string;
  readonly newlines: number;
  /**
   * Lines after a cell's output divider, i.e. what the cell printed; every
   * line when the stream has no divider (a sub-agent's, a legacy tool's).
   */
  readonly outputLines: number;
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
  // Newlines through the divider line, once seen. Only text not yet searched
  // (plus a divider's length of overlap, for a divider split across chunks)
  // is searched, and only until the divider is found.
  let outputBase: number | null = null;
  let searched = 0;
  const findDivider = (): void => {
    if (outputBase !== null) return;
    const at = text.indexOf(DIVIDER_LINE, Math.max(0, searched - (DIVIDER_LINE.length - 1)));
    searched = text.length;
    if (at >= 0) outputBase = countNewlines(text.slice(0, at + DIVIDER_LINE.length));
  };
  findDivider();
  return {
    append(chunk: string): void {
      const step = liveOutputIncrement(length, truncated, chunk);
      truncated = step.truncated;
      if (!step.added) return;
      textNode.appendData(step.added);
      text += step.added;
      length += step.added.length;
      newlines += countNewlines(step.added);
      findDivider();
    },
    get text(): string {
      return text;
    },
    get newlines(): number {
      return newlines;
    },
    get outputLines(): number {
      return outputBase === null ? newlines : newlines - outputBase;
    },
    get truncated(): boolean {
      return truncated;
    },
  };
}

/** Feature-local copy: app.js hard-coded the meta line in English. */
const META_COPY: Record<"en" | "zh", { lines: string; done: string }> = {
  // NB-9: lines the cell printed (`outputLines`), not its code.
  en: { lines: "{0} output lines", done: "done" },
  zh: { lines: "输出 {0} 行", done: "完成" },
};

/**
 * app.js:5495 meta line: the printed-line count once there is more than one,
 * else "done" — but nothing while the card still runs, where "done" was
 * untrue and the code's own lines used to be counted as output.
 */
export function toolMetaLabel(lines: number, running = false): string {
  const copy = META_COPY[LANG === "zh" ? "zh" : "en"];
  if (lines > 1) return copy.lines.replace("{0}", String(lines));
  return running ? "" : copy.done;
}
