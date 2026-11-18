/**
 * The REPL input's editing keys, as pure functions over a textarea's value
 * and selection so they run without a DOM. A plain textarea let Tab leave the
 * box and had no history: changing one line meant copy, scroll, paste, edit.
 */

export type EditorState = { value: string; start: number; end: number };

export const INDENT = "    ";

/** Tab indents at the caret or every selected line; Shift+Tab dedents the lines. */
export function indent(state: EditorState, dedent: boolean): EditorState {
  const { value, start, end } = state;
  if (!dedent && start === end) {
    return { value: value.slice(0, start) + INDENT + value.slice(end), start: start + INDENT.length, end: start + INDENT.length };
  }
  const first = value.lastIndexOf("\n", start - 1) + 1;
  // A selection ending at the start of a line does not include that line.
  const lastChar = end > start && value[end - 1] === "\n" ? end - 1 : end;
  const lineEnd = value.indexOf("\n", lastChar);
  const stop = lineEnd < 0 ? value.length : lineEnd;
  const lines = value.slice(first, stop).split("\n");
  let removedFirst = 0;
  let removedTotal = 0;
  const changed = lines.map((line, index) => {
    if (!dedent) return INDENT + line;
    const lead = /^(?: {1,4}|\t)/.exec(line);
    const cut = lead ? lead[0].length : 0;
    if (index === 0) removedFirst = cut;
    removedTotal += cut;
    return line.slice(cut);
  });
  const next = value.slice(0, first) + changed.join("\n") + value.slice(stop);
  if (!dedent) {
    return { value: next, start: start + INDENT.length, end: end + INDENT.length * lines.length };
  }
  const newStart = Math.max(first, start - removedFirst);
  return { value: next, start: newStart, end: Math.max(newStart, end - removedTotal) };
}

/** The caret, with nothing selected, is on the first line: ↑ recalls history. */
export function caretOnFirstLine(state: EditorState): boolean {
  return state.start === state.end && state.value.lastIndexOf("\n", state.start - 1) < 0;
}

/** The caret, with nothing selected, is on the last line: ↓ moves forward. */
export function caretOnLastLine(state: EditorState): boolean {
  return state.start === state.end && state.value.indexOf("\n", state.end) < 0;
}

/**
 * ↑/↓ through earlier submissions, shell-style. Walking away from the draft
 * keeps it, and walking back past the newest entry restores it; typing makes
 * the recalled text the new draft.
 */
export class ReplHistory {
  private index = -1;
  private draft = "";

  reset(): void {
    this.index = -1;
  }

  older(entries: string[], current: string): string | null {
    if (!entries.length) return null;
    if (this.index < 0 || this.index > entries.length) {
      this.draft = current;
      this.index = entries.length;
    }
    if (this.index === 0) return null;
    this.index -= 1;
    return entries[this.index] ?? null;
  }

  newer(entries: string[]): string | null {
    if (this.index < 0) return null;
    this.index += 1;
    if (this.index >= entries.length) {
      this.index = -1;
      return this.draft;
    }
    return entries[this.index] ?? null;
  }
}
