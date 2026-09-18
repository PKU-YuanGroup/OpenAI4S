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

const CREDENTIAL_SHAPE =
  /\b(?:sk|ark|api[_-]?key|access[_-]?token|refresh[_-]?token)[-_][A-Za-z0-9._-]{8,}\b/gi;

const CREDENTIAL_PREFIX = /^(?:sk|ark|api[_-]?key|access[_-]?token|refresh[_-]?token)[-_]/i;

/**
 * Whether a credential-prefixed token carries a run no model id has:
 * - a body after the prefix of 30+ characters containing a digit, however it
 *   is chunked -- the shape `scripts/source_secret_scan.py`'s ark rule takes.
 *   An Ark key is `ark-` + a UUID + a short suffix (segments 8/4/4/4/12/5), so
 *   no single segment is long and the segment rule alone let it through;
 * - a separator-free segment of 24+ characters, or of 16+ mixing letters and
 *   digits (OpenAI, Anthropic, DeepSeek-style keys).
 * Model ids are short words and dates joined by `-` / `.` / `_`:
 * `ark-code-latest` and `ark-deepseek-v3-250324` stay well under both.
 */
function looksRandom(token: string): boolean {
  const body = token.replace(CREDENTIAL_PREFIX, "");
  if (body.length >= 30 && /\d/.test(body)) return true;
  return token
    .split(/[-_.]/)
    .some((seg) => seg.length >= 24 || (seg.length >= 16 && /\d/.test(seg) && /[A-Za-z]/.test(seg)));
}

/**
 * `publicText` for a model id or protocol name. The generic scrubber redacts
 * any `sk-` / `ark-` token of 8+ characters, which rewrites legitimate ids:
 * `ark-code-latest`, the Ark router default, rendered as "[redacted]". Here a
 * prefixed token is redacted only when it also has one of the key-shaped runs
 * `looksRandom` lists; Bearer tokens and query credentials are stripped as
 * before. A key shape outside those rules would be shown, so a new provider's
 * key format belongs in `looksRandom` and its test.
 */
export function publicModelId(value: unknown, limit = 200): string {
  const out = String(value == null ? "" : value)
    .trim()
    .replace(/\bBearer\s+[^\s,;]+/gi, "Bearer [redacted]")
    .replace(CREDENTIAL_SHAPE, (match) => (looksRandom(match) ? "[redacted]" : match))
    .replace(/([?&](?:key|token|api_key)=)[^&#\s]+/gi, "$1[redacted]");
  return out.length > limit ? out.slice(0, Math.max(0, limit - 1)) + "…" : out;
}
