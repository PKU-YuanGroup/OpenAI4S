/**
 * Candidate ranking. Port of app.js:13006-13008 (composer filter + cap 8)
 * and 13157-13169 (editor: keywords first, then buffer identifiers).
 *
 * Editor keywords come from F-08 `editorKeywords(ext)` — this module does
 * not keep its own EDKW table.
 */

export type AcItem = {
  label: string;
  insert: string;
  sub: string;
};

export const AC_LIMIT = 8;
export const EDITOR_SCAN_CAP = 200000;

export type ArtifactLike = {
  filename?: string | null;
  artifact_id?: string | null;
  id?: string | null;
  version_id?: string | null;
  root_frame_id?: string | null;
  content_type?: string | null;
};

/**
 * Deduped by artifact identity, not by filename. Two different artifacts
 * that share a name stay two rows; `artifact_id` (else `id`, else filename)
 * is the overlap key. Project list first, then the session list.
 */
export function mergeArtifactCandidates(
  projectList: ArtifactLike[],
  sessionList: ArtifactLike[],
): ArtifactLike[] {
  const seen = new Set<string>();
  const out: ArtifactLike[] = [];
  for (const a of [...projectList, ...sessionList]) {
    if (!a || !a.filename) continue;
    const key = String(a.artifact_id || a.id || a.filename);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(a);
  }
  return out;
}

export function artifactToAcItem(
  a: ArtifactLike,
  currentId: string | null,
  fromOtherSession: string,
): AcItem {
  const name = a.filename || "artifact";
  const version = a.version_id || "";
  const elsewhere = !!(a.root_frame_id && currentId && a.root_frame_id !== currentId);
  return {
    label: name,
    insert: version ? `${name}#${version}` : name,
    sub:
      (elsewhere ? fromOtherSession + " · " : "") +
      (version ? String(version).slice(2, 8) + " · " : "") +
      (a.content_type || ""),
  };
}

export function sessionToAcItem(f: {
  name?: string | null;
  task_summary?: string | null;
}): AcItem {
  const label = f.name || f.task_summary || "session";
  return { label, insert: label, sub: "" };
}

export function skillToAcItem(s: {
  displayName?: string | null;
  name?: string | null;
  description?: string | null;
}): AcItem {
  return {
    label: s.displayName || s.name || "",
    insert: s.name || "",
    sub: s.description || "",
  };
}

/** Filter by substring on label or insert, then cap. */
export function rankComposerItems(
  items: AcItem[],
  query: string,
  limit: number = AC_LIMIT,
): AcItem[] {
  const q = (query || "").toLowerCase();
  let out = items;
  if (q) {
    out = out.filter(
      (it) =>
        (it.label || "").toLowerCase().includes(q) ||
        (it.insert || "").toLowerCase().includes(q),
    );
  }
  return out.slice(0, limit);
}

/**
 * Harvest unique identifiers of length ≥ 2. Huge buffers skip the scan
 * (same 200_000 cap as app.js:13164).
 */
export function harvestBufferIdentifiers(
  text: string,
  cap: number = EDITOR_SCAN_CAP,
): string[] {
  if (text.length > cap) return [];
  const seen = new Set<string>();
  const words: string[] = [];
  const mm = text.match(/[A-Za-z_$][\w$]*/g) || [];
  for (const w of mm) {
    if (w.length >= 2 && !seen.has(w)) {
      seen.add(w);
      words.push(w);
    }
  }
  return words;
}

/**
 * Keywords first (prefix, skip the token already typed), then buffer
 * identifiers. `used` is the original-case word. Cap 8.
 */
export function rankEditorItems(
  keywords: readonly string[],
  bufferWords: readonly string[],
  query: string,
  keywordSub: string,
  limit: number = AC_LIMIT,
): AcItem[] {
  const ql = query.toLowerCase();
  const used = new Set<string>();
  const out: AcItem[] = [];
  const push = (list: readonly string[], sub: string): boolean => {
    for (const w of list) {
      const wl = w.toLowerCase();
      if (used.has(w) || wl === ql || !wl.startsWith(ql)) continue;
      used.add(w);
      out.push({ label: w, insert: w, sub });
      if (out.length >= limit) return true;
    }
    return false;
  };
  if (push(keywords, keywordSub)) return out;
  push(bufferWords, "");
  return out;
}
