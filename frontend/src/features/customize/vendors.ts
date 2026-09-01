/**
 * Pure DataPro / Doubao helpers. Port of app.js:11394-11415 and 11798-11806.
 * Isolated from the tab shell so the cards can live in vendors/*.tsx.
 */

export const DATAPRO_CONNECTOR_ID = "volcengine-datapro";

export function dataproResultText(response: unknown): string {
  if (!response || typeof response !== "object") return String(response || "");
  const row = response as Record<string, unknown>;
  const result = row.structuredContent != null ? row.structuredContent : row.content;
  if (typeof result === "string") return result;
  if (result == null) return "";
  try {
    return JSON.stringify(result, null, 2);
  } catch {
    return String(result);
  }
}

export function dataproResponseCode(response: unknown): number | null {
  const structured =
    response && typeof response === "object"
      ? (response as Record<string, unknown>).structuredContent
      : null;
  if (
    structured &&
    typeof structured === "object" &&
    typeof (structured as Record<string, unknown>).code === "number"
  ) {
    return (structured as Record<string, unknown>).code as number;
  }
  return null;
}

export function dataproIndexComplete(response: unknown): boolean {
  const index =
    response && typeof response === "object"
      ? (response as Record<string, unknown>).index
      : null;
  if (!index || typeof index !== "object") return false;
  const row = index as Record<string, unknown>;
  return !!(
    row.complete === true &&
    Number.isInteger(row.entry_count) &&
    (row.entry_count as number) >= 0 &&
    Number.isInteger(row.source_leaf_count) &&
    (row.source_leaf_count as number) >= 0 &&
    Number.isInteger(row.indexed_leaf_count) &&
    (row.indexed_leaf_count as number) >= 0 &&
    row.source_leaf_count === row.indexed_leaf_count &&
    typeof row.source_digest === "string" &&
    (row.source_digest as string).length > 0 &&
    row.source_digest === row.indexed_digest
  );
}

export function doubaoSearchResultText(response: unknown): string {
  const results =
    response && typeof response === "object"
      ? (response as Record<string, unknown>).results
      : null;
  const list = Array.isArray(results) ? results : [];
  return list
    .map((item, index) => {
      const row = item && typeof item === "object" ? (item as Record<string, unknown>) : {};
      const title = typeof row.title === "string" ? row.title.trim() : "";
      const url = typeof row.url === "string" ? row.url.trim() : "";
      const snippet = typeof row.snippet === "string" ? row.snippet.trim() : "";
      return [`${index + 1}. ${title || url}`, url, snippet].filter(Boolean).join("\n");
    })
    .filter(Boolean)
    .join("\n\n");
}

/** Dedicated Doubao check: source must be doubao, never a Tavily fallback. */
export function doubaoSearchAvailable(response: unknown): boolean {
  if (!response || typeof response !== "object") return false;
  const row = response as Record<string, unknown>;
  const results = Array.isArray(row.results) ? row.results : [];
  return !!(
    row.available === true &&
    row.source === "doubao" &&
    Number.isInteger(row.count) &&
    row.count === results.length &&
    results.length > 0
  );
}
