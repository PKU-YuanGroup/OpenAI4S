/**
 * Read-only view over a saved science-search Artifact (A5, report P1-02).
 *
 * A Stage 10 search writes a JSON envelope into the workspace and the Gateway
 * captures it as a versioned Artifact:
 *
 *   { query, endpoint|request_url, retrieved_at, source_checksum|response_sha256,
 *     accessions, result: { database, request_url, provenance{...}, next_cursor,
 *                           results: [{ id, title, url, type, attributes }] } }
 *
 * `readScienceSearchEnvelope` accepts exactly that shape: the top-level key set
 * is closed, `result.results` must be an array of objects, and the record fields
 * that the view renders have checked types. Anything else — including an
 * unrelated JSON document that merely carries a `result` property — returns
 * null, so the generic JSON path stays in charge.
 *
 * Only bytes already saved in the file are shown: nothing here fetches a remote
 * next page, the record list never claims to be a database total, and the raw
 * JSON stays one click away on the same exact version.
 *
 * Everything the file supplies is untrusted: titles/attributes render as text,
 * external links must parse as http(s) without credentials and lose
 * credential-shaped query parameters, and their display text is scrubbed.
 */

import { publicText as scrubbedText } from "../scrub/scrub";
import { el, translate } from "./api";
import { filesT } from "./copy";
import type { ArtifactRow } from "./types";

/** One page of the saved-record list. */
export const SCIENCE_RECORD_PAGE = 50;
/** The list shows at most this many saved records; the rest stay in the file. */
export const SCIENCE_RECORD_MAX = 500;

/**
 * The complete top-level key set this envelope may carry (F13-era saved files,
 * plus the canonical names the projection now writes).
 */
export const SCIENCE_ENVELOPE_KEYS: ReadonlySet<string> = new Set([
  "query",
  "endpoint",
  "request_url",
  "retrieved_at",
  "source_checksum",
  "response_sha256",
  "accessions",
  "result",
]);

export type ScienceRecord = {
  /** Identifier the response already carried; "" when absent. */
  id: string;
  title: string;
  /** Recorded URL as written; only http(s) may become a link. */
  url: string;
  type: string;
  /** The saved attributes object; scalar values may be expanded. */
  attributes: Record<string, unknown>;
};

export type ScienceSearch = {
  query: string;
  requestUrl: string;
  retrievedAt: string;
  responseSha256: string;
  /** Non-empty when the file records that the search has later pages. */
  nextCursor: string;
  /** Canonical/legacy field pairs whose recorded values disagree. */
  conflicts: string[];
  records: ScienceRecord[];
};

const CREDENTIAL_QUERY_KEY =
  /^(?:key|api[_-]?key|token|access[_-]?token|refresh[_-]?token|secret|signature|sig|password|passwd|auth)$/i;
const HIDDEN_DIGEST = /<redacted|\[redacted\]|^hidden$/i;
const ATTRIBUTE_SCALAR_LIMIT = 8;

function record(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function textField(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return "";
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === "string" && !!item.trim())
    .map((item) => publicText(item, 60));
}

function unique(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}

/** The existing credential-shaped substring scrubber, applied to visible text. */
function publicText(value: unknown, limit: number): string {
  return scrubbedText(value, limit);
}

/**
 * A link the preview may navigate to: absolute http(s), no embedded
 * credentials, and credential-shaped query parameters removed rather than
 * merely hidden. Returns "" when the value may not become a navigable link.
 */
export function sanitizeExternalUrl(value: unknown): string {
  if (typeof value !== "string") return "";
  const raw = value.trim();
  if (!raw || raw.length > 2000) return "";
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return "";
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return "";
  if (url.username || url.password) return "";
  for (const key of [...url.searchParams.keys()]) {
    if (CREDENTIAL_QUERY_KEY.test(key)) url.searchParams.delete(key);
  }
  url.hash = "";
  return url.href;
}

function hasValue(value: unknown): boolean {
  if (value == null || value === "") return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value as object).length > 0;
  return true;
}

/**
 * Validate one saved-search envelope. Returns null unless the document is
 * exactly this shape, so an unrelated JSON with a `result` key cannot be
 * mistaken for a retrieval result list.
 */
export function readScienceSearchEnvelope(text: string): ScienceSearch | null {
  let root: unknown;
  try {
    root = JSON.parse(text);
  } catch {
    return null;
  }
  const top = record(root);
  if (!top) return null;
  if (!Object.keys(top).every((key) => SCIENCE_ENVELOPE_KEYS.has(key))) return null;
  const result = record(top.result);
  if (!result) return null;
  const rawResults = result.results;
  if (!Array.isArray(rawResults)) return null;
  // A retrieval identity distinguishes this envelope from arbitrary JSON that
  // happens to contain `{"result": {"results": [...]}}`.
  const identity =
    hasValue(top.query) ||
    hasValue(top.request_url) ||
    hasValue(top.endpoint) ||
    hasValue(top.retrieved_at) ||
    hasValue(top.source_checksum) ||
    hasValue(top.response_sha256) ||
    hasValue(top.accessions) ||
    hasValue(result.query) ||
    hasValue(result.database) ||
    hasValue(result.request_url) ||
    hasValue(result.endpoint) ||
    hasValue(result.retrieved_at) ||
    hasValue(result.response_sha256) ||
    hasValue(result.source_checksum) ||
    hasValue(result.provenance);
  if (!identity) return null;

  const records: ScienceRecord[] = [];
  for (const item of rawResults) {
    const row = record(item);
    if (!row) return null;
    for (const field of ["id", "title", "url", "type"] as const) {
      if (row[field] !== undefined && typeof row[field] !== "string") return null;
    }
    if (row.attributes !== undefined && record(row.attributes) === null) return null;
    records.push({
      id: typeof row.id === "string" ? row.id : "",
      title: typeof row.title === "string" ? row.title : "",
      url: typeof row.url === "string" ? row.url : "",
      type: typeof row.type === "string" ? row.type : "",
      attributes: record(row.attributes) || {},
    });
  }
  // A saved file can carry both spellings (an old writer upgraded by a newer
  // one, or a hand-built fixture). The canonical value is what the view uses;
  // a disagreement is named instead of silently picking one, matching S1's
  // `alias_conflicts` contract for the source projection.
  const canonicalUrl = textField(top.request_url) || textField(result.request_url);
  const aliasUrl = textField(top.endpoint) || textField(result.endpoint);
  const canonicalDigest =
    textField(top.response_sha256) || textField(result.response_sha256);
  const aliasDigest = textField(top.source_checksum) || textField(result.source_checksum);
  const conflicts = [
    ...(canonicalUrl && aliasUrl && canonicalUrl !== aliasUrl
      ? ["request_url ↔ endpoint"]
      : []),
    ...(canonicalDigest && aliasDigest && canonicalDigest !== aliasDigest
      ? ["response_sha256 ↔ source_checksum"]
      : []),
  ];
  return {
    query: textField(top.query) || textField(result.query),
    requestUrl:
      textField(top.request_url) ||
      textField(result.request_url) ||
      textField(top.endpoint) ||
      textField(result.endpoint),
    retrievedAt: textField(top.retrieved_at) || textField(result.retrieved_at),
    responseSha256: textField(top.response_sha256) || textField(result.response_sha256),
    nextCursor: typeof result.next_cursor === "string" ? result.next_cursor.trim() : "",
    conflicts,
    records,
  };
}

export type ScienceRecordPage = {
  records: ScienceRecord[];
  page: number;
  hasMore: boolean;
  /** Records beyond `SCIENCE_RECORD_MAX` are not reachable in the list. */
  capped: boolean;
};

/** Slice the saved record list; never requests anything. */
export function scienceRecordPage(
  search: ScienceSearch,
  page: number,
  pageSize: number = SCIENCE_RECORD_PAGE,
): ScienceRecordPage {
  const size = Math.max(1, Math.min(Math.floor(pageSize) || SCIENCE_RECORD_PAGE, SCIENCE_RECORD_PAGE));
  const current = Math.max(0, Math.floor(page) || 0);
  const start = Math.min(current * size, SCIENCE_RECORD_MAX);
  const end = Math.min(start + size, search.records.length, SCIENCE_RECORD_MAX);
  return {
    records: start < end ? search.records.slice(start, end) : [],
    page: current,
    hasMore: end < search.records.length && end < SCIENCE_RECORD_MAX,
    capped: search.records.length > SCIENCE_RECORD_MAX,
  };
}

/**
 * Public-source readability for the version-history provenance panel. The
 * server already allowlists and redacts; this only decides how a deliberately
 * withheld digest, a conflict marker, or a normalized legacy alias is shown.
 */
export type ScienceSourceStatus = {
  digest: string;
  digestHidden: boolean;
  conflicts: string[];
  aliases: string[];
};

export function scienceSourceStatus(src: Record<string, unknown>): ScienceSourceStatus {
  const hiddenFields = unique([
    ...stringList(src.hidden_fields),
    ...stringList((record(src.result) || {}).hidden_fields),
  ]);
  const rawDigest = textField(src.response_sha256) || textField(src.source_checksum);
  // The public projection names the digest's state out loud
  // (`response_sha256_state: "shown" | "hidden"`): a hidden digest is not
  // "verified", and the redacted marker text must not be rendered as one.
  const digestState = textField(src.response_sha256_state);
  const conflicts = unique([
    // S1 projection: canonical field names whose legacy alias disagreed
    // (`alias_conflicts` is the current key; `source_conflicts` was the
    // earlier spelling and stays readable for projections in flight).
    ...stringList(src.alias_conflicts),
    ...stringList(src.source_conflicts),
    ...stringList(src.conflict_fields),
    ...stringList(src.conflicts),
  ]).slice(0, 8);
  const aliases = unique([
    // S1 projection: canonical field names promoted from a legacy alias.
    ...stringList(src.legacy_normalized),
    ...stringList(src.alias_fields),
    ...stringList(src.normalized_fields),
    ...stringList(src.aliases),
  ]).slice(0, 8);
  const legacyHidden =
    src.response_sha256_hidden === true ||
    hiddenFields.includes("response_sha256") ||
    hiddenFields.includes("source_checksum") ||
    HIDDEN_DIGEST.test(rawDigest);
  const digestHidden = digestState === "hidden" || (digestState !== "shown" && legacyHidden);
  return { digest: digestHidden ? "" : rawDigest, digestHidden, conflicts, aliases };
}


function noteBox(message: string): HTMLElement {
  const node = el("div", "renderer-note science-note", message);
  node.setAttribute("role", "status");
  return node;
}

function downloadLink(url: string, a: ArtifactRow): HTMLAnchorElement {
  const link = el("a", "outline-btn small", translate("common.download"));
  link.href = url;
  link.setAttribute("download", a.filename || "artifact");
  return link;
}

function scalarAttributes(attributes: Record<string, unknown>): Array<[string, string]> {
  const out: Array<[string, string]> = [];
  for (const [key, value] of Object.entries(attributes)) {
    if (typeof value !== "string" && typeof value !== "number" && typeof value !== "boolean") continue;
    out.push([key, String(value)]);
    if (out.length >= ATTRIBUTE_SCALAR_LIMIT) break;
  }
  return out;
}

function recordLinkCell(record: ScienceRecord): HTMLElement {
  const cell = el("span", "science-record-link");
  if (!record.url) return cell;
  const display = publicText(record.url, 300);
  const href = sanitizeExternalUrl(record.url);
  if (href) {
    const link = el("a", null, display) as HTMLAnchorElement;
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    cell.appendChild(link);
  } else {
    // Other protocols (javascript:, data:) and unparsable URLs stay plain text.
    cell.textContent = display;
  }
  return cell;
}

function renderRecord(record: ScienceRecord): HTMLElement {
  const row = el("div", "science-record");
  row.appendChild(el("span", "science-record-id", record.id));
  row.appendChild(el("span", "science-record-title", record.title));
  row.appendChild(el("span", "science-record-type", record.type));
  row.appendChild(recordLinkCell(record));
  const attributes = scalarAttributes(record.attributes);
  if (attributes.length) {
    const details = el("details", "science-record-attributes");
    details.appendChild(el("summary", null, filesT("science.record.attributes", attributes.length)));
    for (const [key, value] of attributes) {
      const line = el("div", "science-record-attribute");
      line.appendChild(el("span", "science-record-attribute-key", key));
      line.appendChild(el("span", "science-record-attribute-value", value));
      details.appendChild(line);
    }
    row.appendChild(details);
  }
  return row;
}

/**
 * Render the saved-search view. `readScienceSearchEnvelope` decides whether the
 * bytes are this envelope; the caller only calls this after a non-null read.
 * `renderRaw` is the shared raw-source renderer (`renderRawSource`) so the "raw
 * JSON" tab keeps the original bytes display/expand/download behaviour; a
 * direct caller may omit it and gets the same bounded source view.
 */
export function renderScienceResults(
  container: HTMLElement,
  a: ArtifactRow,
  url: string,
  text: string,
  search: ScienceSearch,
  renderRaw?: (target: HTMLElement) => void,
): void {
  const shell = el("div", "science-results");
  const tabs = el("div", "science-tabs");
  const recordsTab = el("button", "science-tab active", filesT("science.tab.records")) as HTMLButtonElement;
  const jsonTab = el("button", "science-tab", filesT("science.tab.json")) as HTMLButtonElement;
  tabs.appendChild(recordsTab);
  tabs.appendChild(jsonTab);
  shell.appendChild(tabs);
  const recordsPane = el("div", "science-pane");
  const jsonPane = el("div", "science-pane hidden");
  shell.appendChild(recordsPane);
  shell.appendChild(jsonPane);
  container.appendChild(shell);

  recordsPane.appendChild(noteBox(filesT("science.records.saved", search.records.length.toLocaleString())));
  if (search.conflicts.length) {
    recordsPane.appendChild(
      noteBox(filesT("science.conflictNote", search.conflicts.join(", "))),
    );
  }
  if (search.nextCursor) recordsPane.appendChild(noteBox(filesT("science.records.nextPage")));
  if (!search.records.length) recordsPane.appendChild(noteBox(filesT("science.records.empty")));

  const list = el("div", "science-records");
  const columns = el("div", "science-record science-record-head");
  columns.appendChild(el("span", "science-record-id", filesT("science.column.id")));
  columns.appendChild(el("span", "science-record-title", filesT("science.column.title")));
  columns.appendChild(el("span", "science-record-type", filesT("science.column.type")));
  columns.appendChild(el("span", "science-record-link", filesT("science.column.link")));
  list.appendChild(columns);
  recordsPane.appendChild(list);
  const more = el("button", "outline-btn small", filesT("science.records.loadMore"));
  more.hidden = true;
  recordsPane.appendChild(more);
  const cap = el("div", "science-records-cap");
  recordsPane.appendChild(cap);
  let page = 0;
  let capShown = false;
  const paint = (): void => {
    const result = scienceRecordPage(search, page);
    for (const record of result.records) list.appendChild(renderRecord(record));
    more.hidden = !result.hasMore;
    if (result.capped && !capShown) {
      capShown = true;
      cap.appendChild(noteBox(filesT("science.records.limit")));
      cap.appendChild(downloadLink(url, a));
    }
  };
  paint();
  more.onclick = () => {
    page += 1;
    paint();
  };

  let jsonReady = false;
  const show = (records: boolean): void => {
    if (!records && !jsonReady) {
      if (renderRaw) renderRaw(jsonPane);
      else {
        const pre = el("pre", "renderer-source");
        pre.textContent = text.slice(0, 300_000);
        jsonPane.appendChild(pre);
        jsonPane.appendChild(downloadLink(url, a));
      }
      jsonReady = true;
    }
    recordsPane.classList.toggle("hidden", !records);
    jsonPane.classList.toggle("hidden", records);
    recordsTab.classList.toggle("active", records);
    jsonTab.classList.toggle("active", !records);
  };
  recordsTab.onclick = () => show(true);
  jsonTab.onclick = () => show(false);
}
