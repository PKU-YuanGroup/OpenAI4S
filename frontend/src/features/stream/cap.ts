/**
 * Live tool-output cap. Port of app.js:5361-5371.
 * Once the truncation marker is present, further appends are no-ops (idempotent).
 */

export const LIVE_OUTPUT_CHAR_CAP = 1000000;
export const LIVE_OUTPUT_TRUNCATION = "\n...(live output truncated)";

export function appendLiveOutput(
  current: string | null | undefined,
  chunk: string | null | undefined,
): string {
  const existing = String(current || "");
  const addition = String(chunk || "");
  if (existing.includes(LIVE_OUTPUT_TRUNCATION)) return existing;
  if (existing.length >= LIVE_OUTPUT_CHAR_CAP) {
    return existing.slice(0, LIVE_OUTPUT_CHAR_CAP) + LIVE_OUTPUT_TRUNCATION;
  }
  const remaining = LIVE_OUTPUT_CHAR_CAP - existing.length;
  return addition.length > remaining
    ? existing + addition.slice(0, remaining) + LIVE_OUTPUT_TRUNCATION
    : existing + addition;
}

/**
 * The same cap for a caller that tracks how much it holds and whether the
 * marker went in. `appendLiveOutput` has to search the whole output for the
 * marker on every chunk -- up to 1MB, thousands of times per cell.
 */
export function liveOutputIncrement(
  length: number,
  truncated: boolean,
  chunk: string | null | undefined,
): { added: string; truncated: boolean } {
  if (truncated) return { added: "", truncated: true };
  if (length >= LIVE_OUTPUT_CHAR_CAP) return { added: LIVE_OUTPUT_TRUNCATION, truncated: true };
  const addition = String(chunk || "");
  const remaining = LIVE_OUTPUT_CHAR_CAP - length;
  return addition.length > remaining
    ? { added: addition.slice(0, remaining) + LIVE_OUTPUT_TRUNCATION, truncated: true }
    : { added: addition, truncated: false };
}
