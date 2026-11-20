/**
 * Candidate ranking. Port of app.js:13006-13008 (composer filter + cap 8)
 * and 13157-13169 (editor: keywords first, then buffer identifiers).
 *
 * Editor keywords come from F-08 `editorKeywords(ext)` — this module does
 * not keep its own EDKW table.
 */

import type { ComposerMention } from "./mentions";

export type AcItem = {
  label: string;
  insert: string;
  sub: string;
  /** An `@` file row's version, kept beside the text when it is picked (ART-16). */
  mention?: ComposerMention;
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
    // The text gets the name only; the version is pinned through `mention`.
    insert: name,
    sub:
      (elsewhere ? fromOtherSession + " · " : "") +
      (version ? String(version).slice(2, 8) + " · " : "") +
      (a.content_type || ""),
    ...(version
      ? {
          mention: {
            filename: name,
            version_id: String(version),
            artifact_id: String(a.artifact_id || a.id || ""),
            root_frame_id: String(a.root_frame_id || ""),
          },
        }
      : {}),
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
  collection?: unknown;
}): AcItem {
  const item: AcItem = {
    label: s.displayName || s.name || "",
    insert: s.name || "",
    sub: s.description || "",
  };
  const collection = typeof s.collection === "string" ? s.collection : "";
  skillFacts.set(item, { curated: !s.collection, collection });
  return item;
}

/** Collection membership per skill row, kept off the shared `AcItem` shape. */
const skillFacts = new WeakMap<AcItem, { curated: boolean; collection: string }>();

/**
 * K2 leftover: `/bioskills` named a collection, but a member's name and
 * description never contain its collection's id, so the picker showed
 * nothing. The query (case, spaces, hyphens and underscores ignored; at least
 * three characters) reaches a collection's members when it starts that id.
 */
function namesCollection(item: AcItem, query: string): boolean {
  const id = (skillFacts.get(item)?.collection || "").toLowerCase().replace(/[\s_-]+/g, "");
  const q = query.replace(/-+/g, "");
  return q.length >= 3 && !!id && id.startsWith(q);
}

/** Rows the `/` popup renders at once; the rest are counted, not dropped silently. */
export const SKILL_AC_ROWS = 60;

/**
 * What a query term also means in skill names and descriptions, which are
 * English: Chinese research vocabulary, plus a few English words that name a
 * task rather than appear in a description ("pubmed" is a literature search).
 * A Chinese key matches anywhere in the typed term ("文献综述" hits both).
 */
const SKILL_TERMS: Record<string, readonly string[]> = {
  文献: ["literature", "citation", "paper"],
  综述: ["review", "literature"],
  引用: ["citation", "literature"],
  引文: ["citation", "literature"],
  检索: ["search", "retriev", "literature"],
  搜索: ["search"],
  论文: ["paper", "manuscript", "literature"],
  稿件: ["manuscript", "paper"],
  荟萃: ["meta-analy"],
  元分析: ["meta-analy"],
  单细胞: ["single-cell", "scrna"],
  空间: ["spatial"],
  转录组: ["transcriptom", "rna-seq"],
  基因组: ["genom"],
  测序: ["sequencing", "seq"],
  变异: ["variant"],
  差异表达: ["differential expression", "deseq"],
  蛋白质组: ["proteom"],
  蛋白: ["protein"],
  结构: ["structure"],
  折叠: ["fold"],
  对接: ["docking"],
  分子动力学: ["molecular dynamics"],
  药物: ["drug"],
  靶点: ["target"],
  化学: ["chem"],
  反应: ["reaction"],
  逆合成: ["retrosynthesis"],
  突变: ["mutation", "mutant"],
  序列: ["sequence"],
  进化: ["phylo", "evolution"],
  系统发育: ["phylo"],
  代谢: ["metabol"],
  富集: ["enrichment"],
  通路: ["pathway"],
  聚类: ["cluster"],
  降维: ["dimension", "umap"],
  统计: ["statistic"],
  生存: ["survival"],
  临床: ["clinical"],
  试验: ["trial"],
  绘图: ["figure", "plot"],
  作图: ["figure", "plot"],
  图表: ["figure", "plot", "chart"],
  配图: ["figure"],
  数据集: ["dataset"],
  机器学习: ["machine learning"],
  模型: ["model"],
  评估: ["evaluat"],
  实验设计: ["experiment", "design"],
  问卷: ["survey"],
  量表: ["survey", "measurement"],
  访谈: ["interview", "qualitative"],
  质性: ["qualitative"],
  面板: ["panel"],
  双重差分: ["difference-in-differences"],
  质控: ["quality control", "qc"],
  远程: ["remote"],
  适应症: ["indication"],
  pubmed: ["literature", "citation"],
  citation: ["literature"],
  citations: ["literature"],
  paper: ["literature", "paper"],
  papers: ["literature", "paper"],
  meta: ["meta-analy"],
  plot: ["figure"],
};

function skillTerms(token: string): string[] {
  const out = [token];
  for (const [key, also] of Object.entries(SKILL_TERMS)) {
    const cjk = /[^\x00-\x7f]/.test(key);
    if (cjk ? token.includes(key) : token === key) out.push(...also);
  }
  return out;
}

/** Lowercase, with spaces and underscores read as the hyphen skill names use. */
function normalizeSkillText(text: string): string {
  return text.toLowerCase().replace(/[\s_]+/g, "-");
}

function wordStarts(words: string[], term: string): boolean {
  return words.some((word) => word.startsWith(term));
}

/** -1 for no match; otherwise higher is better. */
function scoreSkill(item: AcItem, query: string): number {
  const name = normalizeSkillText(item.insert || item.label || "");
  const label = normalizeSkillText(item.label || "");
  const description = (item.sub || "").toLowerCase();
  const nameWords = name.split(/[-:/.]+/).filter(Boolean);
  const descWords = description.split(/[^a-z0-9㐀-鿿]+/).filter(Boolean);
  let score = 0;
  if (name === query || label === query) score += 1000;
  else if (name.startsWith(query) || label.startsWith(query)) score += 600;
  // Every term must match somewhere: "literature review" is two of them.
  for (const token of query.split("-").filter(Boolean)) {
    let best = 0;
    for (const term of skillTerms(token)) {
      const plain = term.replace(/\s+/g, "-");
      if (wordStarts(nameWords, plain) || name.startsWith(plain)) best = Math.max(best, 80);
      else if (name.includes(plain)) best = Math.max(best, 30);
      else if (wordStarts(descWords, term) || description.includes(" " + term)) {
        best = Math.max(best, 20);
      } else if (description.includes(term)) best = Math.max(best, 8);
    }
    if (best === 0) return -1;
    score += best;
  }
  return score;
}

/**
 * Every skill matching `query`, best first. A name match beats a word-start
 * match beats a description match, so `/lit` puts literature-review ahead of
 * is-qualitative-analysis (qua-lit-ative). Matches the description, Chinese
 * research vocabulary and hyphen/space/underscore spellings; ties keep the
 * catalog's order with curated skills before collection members. Nothing is
 * capped here: the popup shows the first `SKILL_AC_ROWS` and counts the rest.
 * `byCollection` (the composer's picker) also lists a collection's members when
 * the query names it; Settings shows a named collection as a row of its own.
 */
export function rankSkillItems(
  items: AcItem[],
  query: string,
  opts: { byCollection?: boolean } = {},
): AcItem[] {
  const q = normalizeSkillText((query || "").trim()).replace(/^-+|-+$/g, "");
  const scored = items.map((item, index) => ({
    item,
    index,
    curated: skillFacts.get(item)?.curated !== false,
    score: !q ? 0 : Math.max(scoreSkill(item, q), opts.byCollection && namesCollection(item, q) ? 10 : -1),
  }));
  return scored
    .filter((row) => row.score >= 0)
    .sort(
      (a, b) =>
        b.score - a.score ||
        Number(b.curated) - Number(a.curated) ||
        a.index - b.index,
    )
    .map((row) => row.item);
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
