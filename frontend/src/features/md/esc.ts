/**
 * HTML escaping for the markdown kernel.
 *
 * app.js:5 escaped `&<>` only. F-08 adds `"` so a later attribute interpolation
 * cannot close a double-quoted attr if escQuote is skipped. Order is load-bearing:
 * `&` first, otherwise `&quot;` would become `&amp;quot;`.
 *
 * Port: app.js:5 plus the quote replacement. escQuote (app.js:12778) stays a
 * separate attribute-discipline helper.
 */

export function esc(s: string | null | undefined): string {
  return (s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Neutralize `"` in a capture interpolated into a double-quoted HTML attribute. */
export function escQuote(s: string): string {
  return String(s).replace(/"/g, "&quot;");
}
