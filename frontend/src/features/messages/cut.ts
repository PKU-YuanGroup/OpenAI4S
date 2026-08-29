/**
 * Streaming markdown stable-prefix cut.
 *
 * Port of app.js `_mdStableCut` (5378-5402) with the scan made incremental:
 * an append-only stream resumes from the last unprocessed line instead of
 * `text.split("\\n")` on every token. Seal points, fence rules, the 120-char
 * soft tail, and the `limit < 80` short-circuit are unchanged.
 */

export type MdFence = { char: string; length: number };

export type MdCutState = {
  /** Start of the first unprocessed (complete or incomplete) line. */
  offset: number;
  /** Last sealed boundary (`0` if nothing is stable yet). */
  stable: number;
  openFence: MdFence | null;
  /** Length of `text` the last time this state was produced. */
  textLen: number;
};

const FENCE_OPEN = /^\s*(`{3,}|~{3,})[ \t]*[\w+#.\-]*[ \t]*$/;

export function emptyMdCutState(): MdCutState {
  return { offset: 0, stable: 0, openFence: null, textLen: 0 };
}

function fenceCloses(line: string, open: MdFence): boolean {
  const trimmed = line.trim();
  return (
    trimmed.length >= open.length && [...trimmed].every((ch) => ch === open.char)
  );
}

function resumeFrom(text: string, prev: MdCutState | null | undefined): MdCutState {
  if (
    prev &&
    prev.textLen <= text.length &&
    prev.offset <= text.length &&
    prev.offset > 0
  ) {
    return {
      offset: prev.offset,
      stable: prev.stable,
      openFence: prev.openFence
        ? { char: prev.openFence.char, length: prev.openFence.length }
        : null,
      textLen: text.length,
    };
  }
  return { offset: 0, stable: 0, openFence: null, textLen: text.length };
}

/**
 * Incremental scan. Returns the same `stable` as a from-scratch `_mdStableCut`
 * for any prefix of an append-only string.
 */
export function mdStableCut(
  text: string,
  prev?: MdCutState | null,
): MdCutState {
  const limit = Math.max(0, text.length - 120);
  if (limit < 80) return emptyMdCutState();

  const next = resumeFrom(text, prev);
  let offset = next.offset;
  let stable = next.stable;
  let openFence = next.openFence;

  while (offset < text.length) {
    const nl = text.indexOf("\n", offset);
    if (nl < 0) break;
    const line = text.slice(offset, nl);
    const boundary = nl + 1;
    if (boundary > limit) break;
    if (openFence) {
      if (fenceCloses(line, openFence)) {
        openFence = null;
        if (boundary >= 60) stable = boundary;
      }
    } else {
      const match = line.match(FENCE_OPEN);
      const fence = match ? match[1] : undefined;
      if (fence) {
        openFence = { char: fence.charAt(0), length: fence.length };
      } else if (!line.trim() && boundary >= 60) {
        stable = boundary;
      }
    }
    offset = boundary;
  }

  return { offset, stable, openFence, textLen: text.length };
}

/**
 * Drop-in numeric result of app.js `_mdStableCut`. Pass `prev` to scan
 * incrementally; omit it for a from-scratch cut.
 */
export function _mdStableCut(text: string, prev?: MdCutState | null): number {
  return mdStableCut(text, prev).stable;
}

/** app.js:5417 — sealed prefix only advances when the cut jumps by more than 40. */
export function shouldAdvanceSealed(cut: number, stableAt: number): boolean {
  return cut > (stableAt || 0) + 40;
}
