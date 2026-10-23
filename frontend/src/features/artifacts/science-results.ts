/**
 * Read-only view over a saved science-search Artifact (A4, report P1-02).
 *
 * A Stage 10 search writes a JSON envelope into the workspace and the Gateway
 * captures it as a versioned Artifact:
 *
 *   { query, endpoint|request_url, retrieved_at, source_checksum|response_sha256,
 *     accessions, result: { database, request_url, provenance{...},
 *                           results: [{ id, title, url, type, ... }] } }
 *
 * Reading that as a nested JSON dump is the problem this module fixes. The
 * envelope is validated against that exact shape — a JSON document that merely
 * happens to carry a `result` property is not this view — and the records are
 * then listed with bounded pages. Only bytes already saved in the file are
 * shown: nothing here fetches a remote next page, and the original JSON stays
 * one click away on the same exact version.
 *
 * Everything the file supplies is untrusted: titles render as text, external
 * links must parse as http(s) without credentials and lose credential-shaped
 * query parameters, and unknown values say "unknown" rather than being filled
 * in.
 */

import { publicText as scrubbedText } from "../scrub/scrub";
import { el, TEXT_SOURCE_PREVIEW_CHARACTERS, translate, unicodeSafeSlice } from "./api";
import { filesT } from "./copy";
import type { ArtifactRow } from "./types";

/** One page of the saved-record list. */
export const SCIENCE_RECORD_PAGE = 50;
/** The list shows at most this many saved records; the rest stay in the file. */
export const SCIENCE_RECORD_MAX = 500;

export type ScienceRecord = {
  /** Identifier the response already carried; "" when the record had none. */
  id: string;
  title: string;
  /** Sanitized http(s) URL, or "" when missing/refused. */
  url: string;
  type: string;
  /** A few already-present scalar attributes, label → text. */
  attributes: Array<[string, string]>;
};

export type ScienceSearch = {
  database: string;
  query: string;
  /** Sanitized request URL for display (server already redacted it once). */
  requestUrl: string;
  retrievedAt: string;
  /** Public response digest when the file recorded one; "" otherwise. */
  responseSha256: string;
  responseSha256Hidden: boolean;
  /** Legacy field names the envelope used instead of the canonical ones. */
  aliasesUsed: string[];
  /** Field pairs whose canonical and legacy values disagree. */
  conflicts: string[];
  records: ScienceRecord[];
  /** Saved entries that did not pass the record shape check. */
  malformedRecords: number;
};

export type ScienceSearchParse = { ok: true; search: ScienceSearch } | { ok: false };

const CREDENTIAL_QUERY_KEY =
  /^(?:key|api[_-]?key|token|access[_-]?token|refresh[_-]?token|secret|signature|sig|password|passwd|auth)$/i;
const HIDDEN_DIGEST = /<redacted|\[redacted\]|^hidden$/i;
const ATTRIBUTE_KEYS = ["organism", "method", "date", "database", "length", "description"] as const;

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
 * merely hidden. Display and href are separate concerns — the caller renders
 * the sanitized URL as its own text.
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

function parseRecord(value: unknown): ScienceRecord | null {
  const source = record(value);
  if (!source) return null;
  const id = publicText(textField(source.id) || textField(source.accession) || textField(source.identifier), 120);
  const title = publicText(textField(source.title) || textField(source.name), 300);
  const url = sanitizeExternalUrl(source.url || source.source_url || source.link);
  const type = publicText(textField(source.type) || textField(source.kind) || textField(source.format), 60);
  const attributes: Array<[string, string]> = [];
  for (const key of ATTRIBUTE_KEYS) {
    if (attributes.length >= 3) break;
    const value = textField(source[key]);
    if (value) attributes.push([key, publicText(value, 160)]);
  }
  if (!id && !title && !url && !type && !attributes.length) return null;
  return { id, title, url, type, attributes };
}

/**
 * Validate the saved-envelope shape and project it for display.
 *
 * A document is this envelope only when it has `result.results` as an array
 * *and* a retrieval identity — a database name, a request URL, or a provenance
 * object. That second condition is what keeps `{"result": {...}}` from some
 * unrelated JSON from being rendered as a search result list.
 */
export function parseScienceSearch(text: string): ScienceSearchParse {
  let root: unknown;
  try {
    root = JSON.parse(text);
  } catch {
    return { ok: false };
  }
  const top = record(root);
  if (!top) return { ok: false };
  const result = record(top.result);
  if (!result) return { ok: false };
  const rawResults = result.results;
  if (!Array.isArray(rawResults)) return { ok: false };

  const innerProvenance = record(result.provenance);
  const outerProvenance = record(top.provenance);
  const provenance = innerProvenance || outerProvenance;
  const database = textField(result.database) || textField(top.database);
  const canonicalUrl = textField(result.request_url) || textField(top.request_url);
  const aliasUrl = textField(result.endpoint) || textField(top.endpoint);
  // Identity requirement (see above). Kept deliberately conservative: only a
  // genuinely search-shaped record passes, not any object with `result`.
  if (!database && !canonicalUrl && !aliasUrl && !provenance) return { ok: false };

  const canonicalDigest =
    textField(result.response_sha256) ||
    textField(top.response_sha256) ||
    textField(provenance?.response_sha256);
  const aliasDigest = textField(result.source_checksum) || textField(top.source_checksum);
  const hiddenFields = unique([
    ...stringList(top.hidden_fields),
    ...stringList(result.hidden_fields),
  ]);
  const digestLooksHidden = HIDDEN_DIGEST.test(canonicalDigest) || HIDDEN_DIGEST.test(aliasDigest);
  const responseSha256Hidden =
    top.response_sha256_hidden === true ||
    result.response_sha256_hidden === true ||
    hiddenFields.includes("response_sha256") ||
    hiddenFields.includes("source_checksum") ||
    digestLooksHidden;
  const digest = responseSha256Hidden ? "" : canonicalDigest || aliasDigest;

  const aliasesUsed = unique([
    ...(canonicalUrl ? [] : aliasUrl ? ["endpoint"] : []),
    ...(canonicalDigest ? [] : aliasDigest ? ["source_checksum"] : []),
    ...stringList(top.alias_fields),
    ...stringList(result.alias_fields),
    ...stringList(top.normalized_fields),
    ...stringList(result.normalized_fields),
  ]).slice(0, 8);
  const conflicts = unique([
    ...(canonicalUrl && aliasUrl && canonicalUrl !== aliasUrl ? ["request_url ↔ endpoint"] : []),
    ...(canonicalDigest && aliasDigest && canonicalDigest !== aliasDigest
      ? ["response_sha256 ↔ source_checksum"]
      : []),
    ...stringList(top.conflict_fields),
    ...stringList(result.conflict_fields),
    ...stringList(top.conflicts),
    ...stringList(result.conflicts),
  ]).slice(0, 8);

  const records: ScienceRecord[] = [];
  let malformedRecords = 0;
  for (const item of rawResults) {
    const parsed = parseRecord(item);
    if (parsed) records.push(parsed);
    else malformedRecords += 1;
  }
  const requestUrl = sanitizeExternalUrl(canonicalUrl || aliasUrl);
  return {
    ok: true,
    search: {
      database: publicText(database, 200),
      query: publicText(textField(top.query) || textField(result.query), 500),
      // Not every request URL is navigable (POST endpoints, credentials); the
      // panel shows the redacted URL as text and only links when it is safe.
      requestUrl: requestUrl || publicText(canonicalUrl || aliasUrl, 500),
      retrievedAt: publicText(textField(top.retrieved_at) || textField(provenance?.retrieved_at), 120),
      responseSha256: digest,
      responseSha256Hidden,
      aliasesUsed,
      conflicts,
      records,
      malformedRecords,
    },
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
  const start = current * size;
  const cappedStart = Math.min(start, SCIENCE_RECORD_MAX);
  const end = Math.min(start + size, search.records.length, SCIENCE_RECORD_MAX);
  return {
    records: cappedStart < end ? search.records.slice(cappedStart, end) : [],
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
  const conflicts = unique([
    ...stringList(src.conflict_fields),
    ...stringList(src.conflicts),
    ...stringList(src.source_conflicts),
  ]).slice(0, 8);
  const aliases = unique([
    ...stringList(src.alias_fields),
    ...stringList(src.normalized_fields),
    ...stringList(src.aliases),
  ]).slice(0, 8);
  const digestHidden =
    src.response_sha256_hidden === true ||
    hiddenFields.includes("response_sha256") ||
    hiddenFields.includes("source_checksum") ||
    HIDDEN_DIGEST.test(rawDigest);
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

function sourceRow(label: string, value: string): HTMLElement {
  const row = el("div", "science-source-row");
  row.appendChild(el("span", "science-source-k", label));
  row.appendChild(el("span", "science-source-v", value));
  return row;
}

function renderScienceSource(
  pane: HTMLElement,
  search: ScienceSearch,
  a: ArtifactRow,
  url: string,
): void {
  const section = el("section", "science-source");
  section.appendChild(el("div", "science-source-head", filesT("science.source.heading")));
  if (search.database) section.appendChild(sourceRow(filesT("science.field.database"), search.database));
  if (search.query) section.appendChild(sourceRow(filesT("science.field.query"), search.query));
  if (search.requestUrl) {
    // Shown as text: a recorded request URL is provenance, not navigation, and
    // the server's redaction already ran on it.
    section.appendChild(sourceRow(filesT("science.field.requestUrl"), search.requestUrl));
  }
  if (search.retrievedAt) section.appendChild(sourceRow(filesT("science.field.retrievedAt"), search.retrievedAt));
  if (search.responseSha256Hidden) {
    section.appendChild(sourceRow(filesT("science.field.responseSha256"), filesT("science.responseHidden")));
    section.appendChild(noteBox(filesT("science.responseHiddenNote")));
  } else if (search.responseSha256) {
    section.appendChild(sourceRow(filesT("science.field.responseSha256"), search.responseSha256));
  } else {
    section.appendChild(noteBox(filesT("science.digestNotRecorded")));
  }
  if (search.aliasesUsed.length) {
    section.appendChild(noteBox(filesT("science.aliasNote", search.aliasesUsed.join(", "))));
  }
  if (search.conflicts.length) {
    section.appendChild(noteBox(filesT("science.conflictNote", search.conflicts.join(", "))));
  }
  if (search.malformedRecords) {
    section.appendChild(noteBox(filesT("science.records.malformed", search.malformedRecords)));
  }
  pane.appendChild(section);
  if (!search.records.length) {
    pane.appendChild(noteBox(filesT("science.records.empty")));
    pane.appendChild(downloadLink(url, a));
    return;
  }
  pane.appendChild(noteBox(filesT("science.records.note")));
  const list = el("div", "science-records");
  pane.appendChild(list);
  const shown = el("div", "science-records-shown");
  pane.appendChild(shown);
  const more = el("button", "outline-btn small", filesT("science.records.loadMore"));
  pane.appendChild(more);
  const capped = el("div", "science-records-cap");
  pane.appendChild(capped);
  let page = 0;
  let cappedShown = false;
  const paint = (result: ScienceRecordPage): void => {
    for (const item of result.records) {
      const row = el("div", "science-record");
      const head = el("div", "science-record-head");
      // Titles and identifiers are always text nodes, never markup.
      head.appendChild(el("span", "science-record-title", item.title || filesT("science.record.unknownTitle")));
      if (item.type) head.appendChild(el("span", "science-record-type", item.type));
      row.appendChild(head);
      const meta = el("div", "science-record-meta");
      meta.appendChild(
        el("span", "science-record-id", filesT("science.record.id") + ": " + (item.id || filesT("science.record.unknown"))),
      );
      if (item.url) {
        const link = el("a", "science-record-link", publicText(item.url, 300)) as HTMLAnchorElement;
        link.href = item.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        meta.appendChild(link);
      }
      row.appendChild(meta);
      item.attributes.forEach(([label, value]) => {
        const attr = el("div", "science-record-attr");
        attr.appendChild(el("span", "science-record-attr-k", label));
        attr.appendChild(el("span", "science-record-attr-v", value));
        row.appendChild(attr);
      });
      list.appendChild(row);
    }
    const total = Math.min(search.records.length, SCIENCE_RECORD_MAX);
    shown.textContent = filesT(
      "science.records.shown",
      Math.min((result.page + 1) * SCIENCE_RECORD_PAGE, total).toLocaleString(),
      total.toLocaleString(),
    );
    more.hidden = !result.hasMore;
    if (result.capped && !cappedShown) {
      cappedShown = true;
      capped.appendChild(noteBox(filesT("science.records.limit")));
      capped.appendChild(downloadLink(url, a));
    }
  };
  paint(scienceRecordPage(search, page));
  more.onclick = () => {
    page += 1;
    paint(scienceRecordPage(search, page));
  };
}

function renderScienceJson(
  pane: HTMLElement,
  a: ArtifactRow,
  url: string,
  text: string,
): void {
  const shown = unicodeSafeSlice(text, TEXT_SOURCE_PREVIEW_CHARACTERS);
  const pre = el("pre", "renderer-source science-json");
  pre.textContent = shown;
  pane.appendChild(pre);
  if (shown.length < text.length) {
    const note = el(
      "div",
      "renderer-note renderer-completeness",
      filesT("science.json.truncated", shown.length.toLocaleString()),
    );
    note.setAttribute("data-state", "partial");
    note.setAttribute("role", "status");
    pane.appendChild(note);
  }
  // The exact-version original is always reachable from the raw-JSON tab.
  pane.appendChild(downloadLink(url, a));
}

/**
 * Paint the saved-search view. Returns false when the bytes are not this
 * envelope, so the caller can keep its generic text/JSON path.
 */
export function renderScienceResults(
  container: HTMLElement,
  a: ArtifactRow,
  url: string,
  text: string,
): boolean {
  const parsed = parseScienceSearch(text);
  if (!parsed.ok) return false;
  const search = parsed.search;
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
  renderScienceSource(recordsPane, search, a, url);
  // The raw JSON tab is built lazily; the toggle is the only way in.
  let jsonReady = false;
  const show = (records: boolean): void => {
    if (!records && !jsonReady) {
      renderScienceJson(jsonPane, a, url, text);
      jsonReady = true;
    }
    recordsPane.classList.toggle("hidden", !records);
    jsonPane.classList.toggle("hidden", records);
    recordsTab.classList.toggle("active", records);
    jsonTab.classList.toggle("active", !records);
  };
  recordsTab.onclick = () => show(true);
  jsonTab.onclick = () => show(false);
  return true;
}
