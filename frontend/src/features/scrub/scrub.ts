/**
 * Credential-shaped substring redaction for user-visible strings.
 * Port of app.js:2761-2767.
 */

export function publicText(value: unknown, limit = 180): string {
  let out = String(value == null ? "" : value);
  out = out
    .replace(/\bBearer\s+[^\s,;]+/gi, "Bearer [redacted]")
    .replace(
      /\b(?:sk|ark|api[_-]?key|access[_-]?token|refresh[_-]?token)[-_][A-Za-z0-9._-]{8,}\b/gi,
      "[redacted]",
    )
    .replace(/([?&](?:key|token|api_key)=)[^&#\s]+/gi, "$1[redacted]");
  return out.length > limit ? out.slice(0, Math.max(0, limit - 1)) + "…" : out;
}
