/*
 * Dependency-free parsers used by the Artifact Viewer's scientific renderers.
 *
 * This module deliberately produces plain data only.  It never emits HTML and
 * never evaluates artifact content; app.js turns these records into DOM nodes
 * with textContent/programmatic SVG.  The small UMD wrapper keeps it usable by
 * the classic browser bundle and by the standalone Node contract test.
 */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.OpenAI4SScientificRenderers = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const LOCAL_RENDERER_IDS = new Set([
    "molecule-3d", "chemistry-2d", "genome-track", "sequence", "msa",
    "table", "image", "pdf", "html-preview", "latex", "markdown", "text",
    "download",
  ]);

  function normalizeLines(text) {
    return String(text == null ? "" : text).replace(/\r\n?/g, "\n").split("\n");
  }

  function cleanSequence(value) {
    return String(value == null ? "" : value)
      .replace(/[\s0-9]/g, "")
      .replace(/[^A-Za-z*?.~-]/g, "")
      .toUpperCase();
  }

  function parseFasta(text) {
    const records = [];
    let current = null;
    const flush = () => {
      if (!current) return;
      current.sequence = cleanSequence(current.parts.join(""));
      delete current.parts;
      if (current.sequence || current.name) records.push(current);
      current = null;
    };
    for (const raw of normalizeLines(text)) {
      const line = raw.trim();
      if (!line || (line[0] === ";" && !current)) continue;
      if (line[0] === ">") {
        flush();
        const heading = line.slice(1).trim();
        const split = heading.search(/\s/);
        current = {
          name: split < 0 ? (heading || `sequence_${records.length + 1}`) : heading.slice(0, split),
          description: split < 0 ? "" : heading.slice(split).trim(),
          parts: [],
        };
      } else {
        if (!current) current = { name: `sequence_${records.length + 1}`, description: "", parts: [] };
        if (line[0] !== ";") current.parts.push(line);
      }
    }
    flush();
    return records;
  }

  function parseFastq(text) {
    const lines = normalizeLines(text);
    const records = [];
    let index = 0;
    while (index < lines.length) {
      while (index < lines.length && !lines[index].trim()) index += 1;
      if (index >= lines.length || lines[index][0] !== "@") break;
      const heading = lines[index].slice(1).trim();
      index += 1;
      const sequenceParts = [];
      while (index < lines.length && lines[index][0] !== "+") {
        sequenceParts.push(lines[index]);
        index += 1;
      }
      if (index >= lines.length) break;
      index += 1;
      const sequence = cleanSequence(sequenceParts.join(""));
      let qualityLength = 0;
      while (index < lines.length && qualityLength < sequence.length) {
        qualityLength += lines[index].length;
        index += 1;
      }
      const split = heading.search(/\s/);
      records.push({
        name: split < 0 ? (heading || `read_${records.length + 1}`) : heading.slice(0, split),
        description: split < 0 ? "" : heading.slice(split).trim(),
        sequence,
        quality_length: qualityLength,
      });
    }
    return records;
  }

  function sequenceAlphabet(records) {
    const sample = records.map((record) => record.sequence || "").join("").slice(0, 20000);
    if (!sample) return "unknown";
    const letters = sample.replace(/[-.*?~]/g, "");
    if (!letters) return "unknown";
    if (/^[ACGTUN]+$/i.test(letters)) return /U/i.test(letters) && !/T/i.test(letters) ? "RNA" : "DNA";
    return "protein";
  }

  function parseSequence(text, filename) {
    const lower = String(filename || "").toLowerCase();
    const trimmed = String(text == null ? "" : text).trimStart();
    const fastq = /\.(fastq|fq)$/.test(lower) || /^@[^\n]*\n[^\n]+\n\+/m.test(trimmed);
    let records = fastq ? parseFastq(text) : parseFasta(text);
    if (!records.length && !fastq) {
      const sequence = cleanSequence(text);
      if (sequence) records = [{ name: filename || "sequence", description: "", sequence }];
    }
    return {
      format: fastq ? "FASTQ" : "FASTA",
      alphabet: sequenceAlphabet(records),
      records,
      total_length: records.reduce((sum, record) => sum + record.sequence.length, 0),
    };
  }

  function appendAlignmentRecord(order, records, name, sequence) {
    const cleaned = cleanSequence(sequence);
    if (!name || !cleaned) return;
    if (!Object.prototype.hasOwnProperty.call(records, name)) {
      records[name] = "";
      order.push(name);
    }
    records[name] += cleaned;
  }

  function parseAlignment(text, filename) {
    const raw = String(text == null ? "" : text);
    const lower = String(filename || "").toLowerCase();
    if (/^\s*>/m.test(raw) || /\.(a2m|a3m)$/.test(lower)) {
      const parsed = parseSequence(raw, filename);
      const width = parsed.records.reduce((max, record) => Math.max(max, record.sequence.length), 0);
      return { format: /\.a3m$/.test(lower) ? "A3M" : "FASTA", records: parsed.records, columns: width, alphabet: parsed.alphabet };
    }
    const lines = normalizeLines(raw);
    const first = (lines.find((line) => line.trim()) || "").trim();
    const stockholm = /^#\s*STOCKHOLM/i.test(first) || /\.sto(?:ckholm)?$/.test(lower);
    const clustal = /^(CLUSTAL|MUSCLE|PROBCONS)/i.test(first) || /\.aln$/.test(lower);
    const order = [];
    const byName = Object.create(null);
    for (const rawLine of lines) {
      const line = rawLine.trimEnd();
      if (!line.trim() || line.trim() === "//") continue;
      if (stockholm && line.trimStart()[0] === "#") continue;
      if (clustal && (/^(CLUSTAL|MUSCLE|PROBCONS)/i.test(line.trim()) || /^\s/.test(rawLine))) continue;
      const match = line.trim().match(/^(\S+)\s+([A-Za-z*?.~-]+)(?:\s+\d+)?$/);
      if (match) appendAlignmentRecord(order, byName, match[1], match[2]);
    }
    const records = order.map((name) => ({ name, description: "", sequence: byName[name] }));
    const columns = records.reduce((max, record) => Math.max(max, record.sequence.length), 0);
    return { format: stockholm ? "Stockholm" : (clustal ? "Clustal" : "alignment"), records, columns, alphabet: sequenceAlphabet(records) };
  }

  function residueClass(value, alphabet) {
    const residue = String(value || "").toUpperCase();
    if ((alphabet === "DNA" || alphabet === "RNA") && "ACGTU".includes(residue)) return `base-${residue.toLowerCase()}`;
    if ("AVILM".includes(residue)) return "hydrophobic";
    if ("FWY".includes(residue)) return "aromatic";
    if ("KRH".includes(residue)) return "positive";
    if ("DE".includes(residue)) return "negative";
    if ("STNQ".includes(residue)) return "polar";
    if ("CGP".includes(residue)) return "special";
    if ("-.~".includes(residue)) return "gap";
    return "other";
  }

  // A recognised extension names the format outright, for every line of the
  // file.  Content is only consulted when the name says nothing: a wide line
  // is not evidence against its own name, and BED8–12 are ordinary BED (three
  // required fields, then nine ordered optional ones).
  function genomeFormatFromName(lower) {
    if (/\.vcf(?:\.gz)?$/.test(lower)) return "VCF";
    if (/\.gtf$/.test(lower)) return "GTF";
    if (/\.gff3?$/.test(lower)) return "GFF";
    if (/\.bedgraph$/.test(lower)) return "bedGraph";
    if (/\.bed$/.test(lower)) return "BED";
    return "";
  }

  const BARE_NUMBER = /^[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?$/i;

  // Each test rests on a column the formats cannot share.  A VCF ALT (5th) or
  // INFO (8th) column is never a bare number, where BED keeps its score and
  // thickEnd; a GFF 7th column is always a strand, where BED keeps thickStart.
  function sniffGenomeFormat(fields) {
    if (fields.length >= 8 && /^\d+$/.test(fields[1]) && fields[3] && fields[4]
      && !BARE_NUMBER.test(fields[4]) && !BARE_NUMBER.test(fields[7])) return "VCF";
    if (fields.length >= 9 && /^[-+.?]$/.test(fields[6])) return "GFF";
    return "BED";
  }

  function parseGenome(text, filename) {
    const lower = String(filename || "").toLowerCase();
    const features = [];
    let invalid = 0;
    const named = genomeFormatFromName(lower);
    let format = named || "BED";
    for (const raw of normalizeLines(text)) {
      const line = raw.trim();
      if (!line || line[0] === "#" || /^track\s|^browser\s/i.test(line)) continue;
      const fields = raw.split("\t");
      // An unnamed file reads as BED until one line proves VCF or GFF, and
      // keeps that format for the rest of the file.
      if (!named && format === "BED") format = sniffGenomeFormat(fields);
      let feature = null;
      if (format === "VCF") {
        const pos = Number(fields[1]);
        const ref = fields[3] || "";
        if (Number.isFinite(pos)) feature = {
          chrom: fields[0], start: Math.max(0, pos - 1), end: Math.max(pos, pos - 1 + Math.max(1, ref.length)),
          label: fields[2] && fields[2] !== "." ? fields[2] : `${ref}>${fields[4] || "?"}`,
          type: "variant", strand: "", score: fields[5] || "",
        };
      } else if (format === "GFF" || format === "GTF") {
        const start = Number(fields[3]); const end = Number(fields[4]);
        if (Number.isFinite(start) && Number.isFinite(end)) feature = {
          chrom: fields[0], start: Math.max(0, start - 1), end,
          label: attributeLabel(fields[8]) || fields[2] || "feature", type: fields[2] || "feature",
          strand: fields[6] || "", score: fields[5] || "",
        };
      } else if (fields.length >= 3) {
        const start = Number(fields[1]); const end = Number(fields[2]);
        if (Number.isFinite(start) && Number.isFinite(end)) feature = {
          chrom: fields[0], start, end,
          label: fields[3] || "feature", type: format === "bedGraph" ? "signal" : "feature",
          strand: fields[5] || "", score: fields[4] || (format === "bedGraph" ? fields[3] : ""),
        };
      }
      if (!feature || !feature.chrom || feature.start < 0 || feature.end < feature.start) invalid += 1;
      else features.push(feature);
    }
    const chromosomes = Object.create(null);
    for (const feature of features) {
      const stats = chromosomes[feature.chrom] || { chrom: feature.chrom, start: feature.start, end: feature.end, count: 0 };
      stats.start = Math.min(stats.start, feature.start);
      stats.end = Math.max(stats.end, feature.end);
      stats.count += 1;
      chromosomes[feature.chrom] = stats;
    }
    return { format, features, chromosomes: Object.values(chromosomes), invalid };
  }

  function attributeLabel(raw) {
    const text = String(raw || "");
    const match = text.match(/(?:^|;)\s*(?:Name|gene_name|gene_id|ID)\s*(?:=|\s)\s*"?([^;"\s]+(?: [^;"]+)?)"?/i);
    return match ? match[1].trim() : "";
  }

  function parseMolfile(text) {
    const firstRecord = String(text == null ? "" : text).split(/^\$\$\$\$/m, 1)[0];
    const lines = normalizeLines(firstRecord);
    if (lines.length < 4 || /V3000/i.test(lines[3] || "")) return null;
    const countLine = lines[3] || "";
    let atomCount = Number.parseInt(countLine.slice(0, 3).trim(), 10);
    let bondCount = Number.parseInt(countLine.slice(3, 6).trim(), 10);
    if (!Number.isFinite(atomCount) || !Number.isFinite(bondCount)) {
      const counts = countLine.trim().split(/\s+/);
      atomCount = Number.parseInt(counts[0], 10); bondCount = Number.parseInt(counts[1], 10);
    }
    if (!Number.isFinite(atomCount) || !Number.isFinite(bondCount) || atomCount < 1 || atomCount > 2000 || bondCount > 4000) return null;
    const atoms = [];
    for (let index = 0; index < atomCount; index += 1) {
      const line = lines[4 + index] || "";
      const pieces = line.trim().split(/\s+/);
      const x = Number.parseFloat(line.slice(0, 10).trim() || pieces[0]);
      const y = Number.parseFloat(line.slice(10, 20).trim() || pieces[1]);
      const element = (line.slice(31, 34).trim() || pieces[3] || "C").replace(/[^A-Za-z]/g, "").slice(0, 3) || "C";
      if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
      atoms.push({ x, y, element });
    }
    const bonds = [];
    for (let index = 0; index < bondCount; index += 1) {
      const line = lines[4 + atomCount + index] || "";
      const pieces = line.trim().split(/\s+/);
      const a = Number.parseInt(line.slice(0, 3).trim() || pieces[0], 10) - 1;
      const b = Number.parseInt(line.slice(3, 6).trim() || pieces[1], 10) - 1;
      const order = Number.parseInt(line.slice(6, 9).trim() || pieces[2], 10) || 1;
      if (a >= 0 && b >= 0 && a < atoms.length && b < atoms.length) bonds.push({ a, b, order: Math.max(1, Math.min(3, order)) });
    }
    return { title: (lines[0] || "Molecule").trim() || "Molecule", atoms, bonds };
  }

  function smilesLines(text) {
    return normalizeLines(text).map((line) => line.trim()).filter((line) => line && line[0] !== "#").slice(0, 200).map((line, index) => {
      const fields = line.split(/\s+/);
      return { smiles: fields[0].slice(0, 2000), name: fields.slice(1).join(" ").slice(0, 240) || `molecule_${index + 1}` };
    });
  }

  const LATEX_SYMBOLS = Object.freeze({
    alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", theta: "θ", lambda: "λ", mu: "μ",
    pi: "π", rho: "ρ", sigma: "σ", tau: "τ", phi: "φ", omega: "ω", Delta: "Δ", Sigma: "Σ", Omega: "Ω",
    times: "×", cdot: "·", pm: "±", leq: "≤", geq: "≥", neq: "≠", approx: "≈", infty: "∞", rightarrow: "→", leftarrow: "←",
  });

  function latexPlain(value) {
    let text = String(value == null ? "" : value);
    text = text.replace(/\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, "($1)⁄($2)");
    text = text.replace(/\\(?:text|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}/g, "$1");
    text = text.replace(/\\([A-Za-z]+)\b/g, (match, name) => Object.prototype.hasOwnProperty.call(LATEX_SYMBOLS, name) ? LATEX_SYMBOLS[name] : match);
    text = text.replace(/\^\{([^{}]*)\}/g, "^$1").replace(/_\{([^{}]*)\}/g, "_$1");
    text = text.replace(/[{}]/g, "").replace(/\\[,;!quadenspace]+/g, " ");
    return text.trim();
  }

  function latexPreview(text) {
    let source = String(text == null ? "" : text).replace(/\r\n?/g, "\n");
    source = source.replace(/(^|[^\\])%.*$/gm, "$1");
    const doc = source.match(/\\begin\s*\{document\}([\s\S]*?)\\end\s*\{document\}/i);
    if (doc) source = doc[1];
    source = source.replace(/\\(?:documentclass|usepackage|title|author|date)\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}/g, "");
    const blocks = [];
    const token = /\\(section|subsection|subsubsection)\*?\s*\{([^{}]*)\}|\\\[([\s\S]*?)\\\]|\$\$([\s\S]*?)\$\$|\\begin\s*\{(?:equation\*?|align\*?)\}([\s\S]*?)\\end\s*\{(?:equation\*?|align\*?)\}/g;
    let cursor = 0; let match;
    const addParagraphs = (chunk) => {
      chunk.split(/\n\s*\n/).map((part) => latexPlain(part.replace(/\\(?:begin|end)\s*\{[^{}]*\}/g, " ").replace(/\s+/g, " "))).filter(Boolean)
        .forEach((part) => blocks.push({ kind: "paragraph", text: part }));
    };
    while ((match = token.exec(source))) {
      addParagraphs(source.slice(cursor, match.index));
      if (match[1]) blocks.push({ kind: "heading", level: match[1] === "section" ? 1 : (match[1] === "subsection" ? 2 : 3), text: latexPlain(match[2]) });
      else blocks.push({ kind: "math", text: latexPlain(match[3] || match[4] || match[5] || "") });
      cursor = token.lastIndex;
    }
    addParagraphs(source.slice(cursor));
    return blocks.slice(0, 500);
  }

  function rendererIdFromDescriptor(descriptor, catalog) {
    const renderer = descriptor && descriptor.renderer;
    const id = renderer && typeof renderer.renderer_id === "string" ? renderer.renderer_id : "";
    if (!LOCAL_RENDERER_IDS.has(id)) return "download";
    if (Array.isArray(catalog) && catalog.length) {
      const allowed = catalog.some((item) => item && item.renderer_id === id);
      if (!allowed) return "download";
    }
    return id;
  }

  return Object.freeze({
    LOCAL_RENDERER_IDS,
    parseAlignment,
    parseFasta,
    parseFastq,
    parseGenome,
    parseMolfile,
    parseSequence,
    residueClass,
    rendererIdFromDescriptor,
    smilesLines,
    latexPlain,
    latexPreview,
  });
}));
