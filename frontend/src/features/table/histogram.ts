import type { HistogramBin, NumericHistogramBin } from "./types";

/** Same cap as `openai4s/server/table_profile.py::MAX_TABLE_PROFILE_BINS`. */
export const MAX_TABLE_PROFILE_BINS = 50;

export function isNumericBin(bin: HistogramBin): bin is NumericHistogramBin {
  return (
    typeof (bin as NumericHistogramBin).start === "number" &&
    typeof (bin as NumericHistogramBin).end === "number" &&
    Number.isFinite((bin as NumericHistogramBin).start) &&
    Number.isFinite((bin as NumericHistogramBin).end)
  );
}

function asCount(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? n : 0;
}

/**
 * Defense in depth: never paint more than 50 bars even if a payload is over-long.
 * Returns the kept bins plus whether the input was clipped.
 */
export function clampHistogram(
  bins: unknown,
  max = MAX_TABLE_PROFILE_BINS,
): { bins: HistogramBin[]; clipped: boolean } {
  if (!Array.isArray(bins)) return { bins: [], clipped: false };
  const kept: HistogramBin[] = [];
  for (const raw of bins) {
    if (!raw || typeof raw !== "object") continue;
    const rec = raw as Record<string, unknown>;
    if (typeof rec.start === "number" && typeof rec.end === "number") {
      kept.push({ start: rec.start, end: rec.end, count: asCount(rec.count) });
    } else if (typeof rec.value === "string") {
      kept.push({ value: rec.value, count: asCount(rec.count) });
    }
  }
  const clipped = kept.length > max;
  return { bins: kept.slice(0, max), clipped };
}

export function histogramBounds(
  bins: HistogramBin[],
): { start: number; end: number } | null {
  const numeric = bins.filter(isNumericBin);
  const first = numeric[0];
  const last = numeric[numeric.length - 1];
  if (!first || !last) return null;
  return { start: first.start, end: last.end };
}

export function maxBinCount(bins: HistogramBin[]): number {
  let max = 0;
  for (const bin of bins) {
    if (bin.count > max) max = bin.count;
  }
  return max;
}

/** `approximate` is pass-through. Only the literal true/"true" is approximate. */
export function readApproximate(payload: { approximate?: unknown } | null | undefined): boolean {
  if (!payload) return false;
  return payload.approximate === true || payload.approximate === "true";
}
