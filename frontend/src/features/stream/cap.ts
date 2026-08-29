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
