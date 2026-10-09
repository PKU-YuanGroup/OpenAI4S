"use strict";

const assert = require("node:assert/strict");
const renderer = require("../openai4s/server/webui/scientific_renderers.js");

const fasta = renderer.parseSequence(">alpha first\nACGTACGT\n>beta\nACGU\n", "reads.fasta");
assert.equal(fasta.format, "FASTA");
assert.equal(fasta.records.length, 2);
assert.equal(fasta.total_length, 12);
assert.equal(fasta.records[0].description, "first");

const alignment = renderer.parseAlignment(
  "CLUSTAL W\n\nseq1    AC-GT\nseq2    ACTGT\n        ** **\n\nseq1    AA\nseq2    A-\n",
  "example.aln",
);
assert.equal(alignment.format, "Clustal");
assert.deepEqual(alignment.records.map((record) => record.sequence), ["AC-GTAA", "ACTGTA-"]);
assert.equal(alignment.columns, 7);

const genome = renderer.parseGenome(
  "chr1\t10\t25\tfeature-a\nchr1\t30\t45\tfeature-b\nchr2\t5\t9\tfeature-c\n",
  "track.bed",
);
assert.equal(genome.format, "BED");
assert.equal(genome.features.length, 3);
assert.equal(genome.chromosomes.length, 2);

// BED has three required fields and nine ordered optional ones, so BED8–12 are
// BED.  Eight columns with an integer start used to satisfy the VCF sniff,
// which overruled `.bed` and drew [100, 200) as a variant at [99, 103).
const genomeRow = (...fields) => fields.join("\t");
const bed12 = ["chr1", "100", "200", "peak", "960", "+", "110", "190", "255,0,0", "2", "20,30,", "0,70,"];
for (let columns = 3; columns <= 12; columns += 1) {
  for (const filename of ["example.bed", null]) {
    const where = `BED${columns} named ${filename}`;
    const parsed = renderer.parseGenome(genomeRow(...bed12.slice(0, columns)), filename);
    assert.equal(parsed.format, "BED", where);
    assert.equal(parsed.invalid, 0, where);
    assert.deepEqual(parsed.features, [{
      chrom: "chr1", start: 100, end: 200,
      label: columns >= 4 ? "peak" : "feature", type: "feature",
      strand: columns >= 6 ? "+" : "", score: columns >= 5 ? "960" : "",
    }], where);
  }
}
const unnamedFormat = (...fields) => renderer.parseGenome(genomeRow(...fields)).format;
// A "." score leaves only thickEnd, never a VCF INFO value, to tell BED8 apart.
assert.equal(unnamedFormat("chr1", "100", "200", "peak", ".", "+", "110", "190"), "BED");
// BED6+2 with text extras: the numeric score is never a VCF ALT allele.
assert.equal(unnamedFormat("chr1", "100", "200", "peak", "960", "+", "geneA", "note"), "BED");
// Content never overrules a recognised name, whichever way it points.
const vcfRow = genomeRow("chr1", "101", "rs1", "A", "G", "50", "PASS", "DP=10");
assert.deepEqual(renderer.parseGenome(vcfRow, "calls.bed"), { format: "BED", features: [], chromosomes: [], invalid: 1 });
assert.equal(renderer.parseGenome(vcfRow, "genes.gff").format, "GFF");
// Unnamed VCF and GFF are still recognised, and recognised names still parse.
assert.equal(unnamedFormat("chr1", "101", "rs1", "A", "G", "50", "PASS", "DP=10"), "VCF");
assert.equal(unnamedFormat("chr1", "101", ".", "AC", "A,<DEL>", ".", ".", ".", "GT", "0/1"), "VCF");
assert.equal(unnamedFormat("chr1", "src", "gene", "101", "200", ".", "+", ".", "ID=g1"), "GFF");
assert.deepEqual(renderer.parseGenome(`##fileformat=VCFv4.2\n${vcfRow}`, "calls.vcf").features[0], {
  chrom: "chr1", start: 100, end: 101, label: "rs1", type: "variant", strand: "", score: "50",
});
// A sites-only record without INFO cannot prove VCF on its own: `.vcf` says
// so, and in an unnamed file the record before it already has.
const sitesOnly = genomeRow("chr1", "301", ".", "G", "T", "20", "PASS");
assert.deepEqual(renderer.parseGenome(sitesOnly, "calls.vcf").features.map((feature) => feature.label), ["G>T"]);
const unnamedVcf = renderer.parseGenome(`${vcfRow}\n${sitesOnly}`);
assert.equal(unnamedVcf.format, "VCF");
assert.deepEqual(unnamedVcf.features.map((feature) => feature.label), ["rs1", "G>T"]);
const gtf = renderer.parseGenome(genomeRow("chr1", "1", "gene", "101", "200", ".", "-", ".", "gene_id \"G1\";"), "genes.gtf");
assert.equal(gtf.format, "GTF");
assert.deepEqual(gtf.features, [{ chrom: "chr1", start: 100, end: 200, label: "G1", type: "gene", strand: "-", score: "." }]);
assert.equal(renderer.parseGenome(genomeRow("chr1", "100", "200", "1.5"), "signal.bedGraph").features[0].type, "signal");

const molfile = [
  "Water",
  "  OpenAI4S",
  "",
  "  3  2  0  0  0  0            999 V2000",
  "    0.0000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0",
  "   -0.8000   -0.6000    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0",
  "    0.8000   -0.6000    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0",
  "  1  2  1  0  0  0  0",
  "  1  3  1  0  0  0  0",
  "M  END",
].join("\n");
const molecule = renderer.parseMolfile(molfile);
assert.equal(molecule.title, "Water");
assert.equal(molecule.atoms.length, 3);
assert.equal(molecule.bonds.length, 2);

const latex = renderer.latexPreview("\\section{Result}\nThe value is $$\\alpha \\leq 1$$.");
assert.deepEqual(latex[0], { kind: "heading", level: 1, text: "Result" });
assert.equal(latex.some((block) => block.kind === "math" && block.text.includes("α")), true);

const catalog = [{ renderer_id: "sequence" }, { renderer_id: "download" }];
assert.equal(renderer.rendererIdFromDescriptor({ renderer: { renderer_id: "sequence" } }, catalog), "sequence");
assert.equal(renderer.rendererIdFromDescriptor({ renderer: { renderer_id: "unknown-script" } }, catalog), "download");

console.log("scientific renderer parser smoke: ok");
