import { describe, expect, it } from "vitest";
import {
  B_HIGH,
  B_LOW,
  B_MID,
  CHAIN_PALETTE,
  NO_DATA_COLOR,
  bFactorColor,
  chainColor,
  hexColor,
  labelAnchor,
  molStyleSpec,
  parsePdbHeader,
  residueKey,
  residueLabel,
  schemeAvailable,
  schemeColorFunc,
  structureFacts,
  type MolAtom,
} from "./mol-facts";

// Verbatim records from RCSB 2NYD and an AlphaFold DB model.
const NYD_HEADER = "HEADER    UNKNOWN FUNCTION                        20-NOV-06   2NYD              ";
const NYD_TITLE = "TITLE     CRYSTAL STRUCTURE OF STAPHYLOCOCCUS AUREUS HYPOTHETICAL PROTEIN SA1388";
const NYD_ATOM = "ATOM      1  N   ASP A  -1     -31.688 145.978 -27.218  1.00 48.14           N  ";
const AF_HEADER = "HEADER                                            01-AUG-25                     ";
const AF_TITLE = "TITLE     ALPHAFOLD MONOMER V2.0 PREDICTION FOR INSULIN (P01308)                ";

const atom = (over: Partial<MolAtom> = {}): MolAtom => ({
  atom: "CA",
  resn: "GLY",
  resi: 65,
  chain: "A",
  b: 20,
  x: 0,
  y: 0,
  z: 0,
  ...over,
});

describe("parsePdbHeader", () => {
  it("reads the ID code (cols 63-66), classification and TITLE of a real entry", () => {
    expect(parsePdbHeader([NYD_HEADER, NYD_TITLE, NYD_ATOM].join("\n"))).toEqual({
      idCode: "2NYD",
      title: "CRYSTAL STRUCTURE OF STAPHYLOCOCCUS AUREUS HYPOTHETICAL PROTEIN SA1388",
      classification: "UNKNOWN FUNCTION",
    });
  });

  it("joins TITLE continuation records and accepts CRLF line ends", () => {
    const text = [
      NYD_HEADER,
      "TITLE     CRYSTAL STRUCTURE OF THE CATALYTIC DOMAIN OF HUMAN                  ",
      "TITLE    2 PHOSPHODIESTERASE 4B                                              ",
      NYD_ATOM,
    ].join("\r\n");
    expect(parsePdbHeader(text)?.title).toBe(
      "CRYSTAL STRUCTURE OF THE CATALYTIC DOMAIN OF HUMAN PHOSPHODIESTERASE 4B",
    );
  });

  it("stops at the coordinates: a TITLE after ATOM is not a header", () => {
    const text = [NYD_HEADER, NYD_ATOM, "TITLE     LATE RECORD"].join("\n");
    expect(parsePdbHeader(text)?.title).toBe("");
  });

  it("keeps a model's TITLE when HEADER has no classification or ID", () => {
    expect(parsePdbHeader([AF_HEADER, AF_TITLE].join("\n"))).toEqual({
      idCode: "",
      title: "ALPHAFOLD MONOMER V2.0 PREDICTION FOR INSULIN (P01308)",
      classification: "",
    });
  });

  it("reads a short hand-written HEADER line without an ID", () => {
    expect(parsePdbHeader("HEADER    TEST PEPTIDE\n" + NYD_ATOM)).toEqual({
      idCode: "",
      title: "",
      classification: "TEST PEPTIDE",
    });
  });

  it("drops an ID code that is not shaped like a PDB ID", () => {
    const placeholder = NYD_HEADER.slice(0, 62) + "XXXX";
    expect(parsePdbHeader(placeholder)?.idCode).toBe("");
    expect(parsePdbHeader(NYD_HEADER.slice(0, 62) + "1ema")?.idCode).toBe("1EMA");
  });

  it("returns null without HEADER or TITLE, and caps a runaway title", () => {
    expect(parsePdbHeader(NYD_ATOM)).toBeNull();
    expect(parsePdbHeader("")).toBeNull();
    const long = Array.from({ length: 12 }, (_, i) => `TITLE   ${String(i + 1).padStart(2)} ${"X".repeat(60)}`);
    const title = parsePdbHeader(long.join("\n"))?.title ?? "";
    expect(title.length).toBe(400);
    expect(title.endsWith("…")).toBe(true);
  });
});

describe("structureFacts / schemeAvailable", () => {
  it("lists polymer chains in order of appearance and the polymer B-factor range", () => {
    const facts = structureFacts([
      atom({ chain: "B", b: 30 }),
      atom({ chain: "A", b: 12.5 }),
      atom({ chain: "B", b: 80 }),
      atom({ chain: "W", hetflag: true, b: 99 }),
      atom({ chain: " ", b: Number.NaN }),
    ]);
    expect(facts).toEqual({ chains: ["B", "A"], bRange: [12.5, 80] });
    expect(schemeAvailable("chain", facts)).toBe(true);
    expect(schemeAvailable("bfactor", facts)).toBe(true);
  });

  it("offers neither scheme for a small molecule (XYZ/SDF atoms are all hetero)", () => {
    const facts = structureFacts([
      { elem: "C", hetflag: true, x: 0, y: 0, z: 0 },
      { elem: "H", hetflag: true, x: 1, y: 0, z: 0 },
    ]);
    expect(facts).toEqual({ chains: [], bRange: null });
    expect(schemeAvailable("default", facts)).toBe(true);
    expect(schemeAvailable("chain", facts)).toBe(false);
    expect(schemeAvailable("bfactor", facts)).toBe(false);
  });

  it("offers no B-factor scheme when every B-factor is equal or missing", () => {
    expect(structureFacts([atom({ b: 20 }), atom({ b: 20 })]).bRange).toBeNull();
    expect(structureFacts([atom({ b: undefined }), atom({ b: undefined })]).bRange).toBeNull();
  });
});

describe("residue labels", () => {
  it("names a residue by name, number, insertion code and chain", () => {
    expect(residueLabel(atom())).toBe("GLY 65 · A");
    expect(residueLabel(atom({ resn: "CRO", resi: 66, icode: "A" }))).toBe("CRO 66A · A");
    expect(residueLabel(atom({ resn: "ALA", resi: 1, chain: " ", icode: " " }))).toBe("ALA 1");
    expect(residueLabel(atom({ resi: "-3" }))).toBe("GLY -3 · A");
  });

  it("names an atom outside any residue by element and 1-based position", () => {
    expect(residueLabel({ elem: "C", index: 0, hetflag: true })).toBe("C 1");
    expect(residueLabel({ elem: "N", serial: 7 })).toBe("N 7");
    expect(residueLabel({ elem: "H" })).toBe("H");
  });

  it("keys every atom of a residue alike and residues of other chains apart", () => {
    expect(residueKey(atom({ atom: "N" }))).toBe(residueKey(atom({ atom: "CA" })));
    expect(residueKey(atom({ chain: "A" }))).not.toBe(residueKey(atom({ chain: "B" })));
    expect(residueKey(atom({ icode: "A" }))).not.toBe(residueKey(atom()));
    expect(residueKey({ elem: "C", index: 0, x: 0, y: 0, z: 0 })).not.toBe(
      residueKey({ elem: "C", index: 1, x: 0, y: 0, z: 0 }),
    );
  });

  it("anchors a label on the residue's CA, a ligand's centre, or the atom itself", () => {
    const residue = [
      atom({ atom: "N", x: 0, y: 0, z: 0 }),
      atom({ atom: "CA", x: 1, y: 2, z: 3 }),
      atom({ atom: "C", x: 5, y: 5, z: 5 }),
    ];
    const other = atom({ resi: 66, atom: "CA", x: 9, y: 9, z: 9 });
    expect(labelAnchor([...residue, other], residue[0]!)).toEqual({ x: 1, y: 2, z: 3 });
    const ligand = [
      atom({ resn: "CRO", resi: 66, atom: "C1", hetflag: true, x: 2, y: 4, z: 6 }),
      atom({ resn: "CRO", resi: 66, atom: "N2", hetflag: true, x: 4, y: 8, z: 12 }),
    ];
    expect(labelAnchor([...residue, ...ligand], ligand[1]!)).toEqual({ x: 3, y: 6, z: 9 });
    expect(labelAnchor([], { elem: "C", index: 0, x: 1, y: 1, z: 1 })).toEqual({ x: 1, y: 1, z: 1 });
    expect(labelAnchor([], { elem: "C" })).toBeNull();
  });
});

describe("colour schemes", () => {
  it("maps B-factors blue → white → red across the range, clamped", () => {
    expect(bFactorColor(10, [10, 50])).toBe(B_LOW);
    expect(bFactorColor(30, [10, 50])).toBe(B_MID);
    expect(bFactorColor(50, [10, 50])).toBe(B_HIGH);
    expect(bFactorColor(-5, [10, 50])).toBe(B_LOW);
    expect(bFactorColor(500, [10, 50])).toBe(B_HIGH);
    expect(hexColor(bFactorColor(20, [10, 50]))).toBe("#8cafd2");
    expect(bFactorColor(undefined, [10, 50])).toBe(NO_DATA_COLOR);
    expect(bFactorColor(20, [10, 10])).toBe(NO_DATA_COLOR);
  });

  it("colours chains from the palette in order, wrapping past its end", () => {
    expect(chainColor(0)).toBe(CHAIN_PALETTE[0]);
    expect(chainColor(1)).toBe(CHAIN_PALETTE[1]);
    expect(chainColor(CHAIN_PALETTE.length)).toBe(CHAIN_PALETTE[0]);
    expect(hexColor(0x0000ff)).toBe("#0000ff");
  });

  it("gives each available scheme a per-atom colour function, the default none", () => {
    const facts = structureFacts([atom({ chain: "A", b: 10 }), atom({ chain: "B", b: 50 })]);
    expect(schemeColorFunc("default", facts)).toBeNull();
    const byChain = schemeColorFunc("chain", facts)!;
    expect(byChain(atom({ chain: "A" }))).toBe(CHAIN_PALETTE[0]);
    expect(byChain(atom({ chain: "B" }))).toBe(CHAIN_PALETTE[1]);
    expect(byChain(atom({ chain: "Z" }))).toBe(NO_DATA_COLOR);
    const byB = schemeColorFunc("bfactor", facts)!;
    expect(byB(atom({ b: 10 }))).toBe(B_LOW);
    expect(byB(atom({ b: 50 }))).toBe(B_HIGH);
    expect(schemeColorFunc("chain", { chains: [], bRange: null })).toBeNull();
    expect(schemeColorFunc("bfactor", { chains: ["A"], bRange: null })).toBeNull();
  });

  it("keeps the original look for the default scheme", () => {
    expect(molStyleSpec("cartoon", false, null)).toEqual({
      style: { cartoon: { color: "spectrum" } },
      surface: null,
    });
    expect(molStyleSpec("cartoon", true, null)).toEqual({
      style: { cartoon: { color: "spectrum", style: "trace" }, sphere: { colorscheme: "Jmol", radius: 0.5 } },
      surface: null,
    });
    expect(molStyleSpec("stick", false, null).style).toEqual({ stick: { colorscheme: "Jmol" } });
    expect(molStyleSpec("sphere", false, null).style).toEqual({ sphere: { colorscheme: "Jmol" } });
    expect(molStyleSpec("line", false, null).style).toEqual({ line: { colorscheme: "Jmol" } });
    expect(molStyleSpec("surface", false, null)).toEqual({
      style: { cartoon: { color: "spectrum" } },
      surface: { opacity: 0.85, color: "white" },
    });
  });

  it("paints every representation, and the surface, with a scheme's colour function", () => {
    const paint = () => 0x123456;
    expect(molStyleSpec("cartoon", false, paint).style).toEqual({ cartoon: { colorfunc: paint } });
    expect(molStyleSpec("cartoon", true, paint).style).toEqual({
      cartoon: { colorfunc: paint, style: "trace" },
      sphere: { colorfunc: paint, radius: 0.5 },
    });
    for (const style of ["stick", "sphere", "line"]) {
      expect(molStyleSpec(style, false, paint)).toEqual({ style: { [style]: { colorfunc: paint } }, surface: null });
    }
    expect(molStyleSpec("surface", false, paint)).toEqual({
      style: { cartoon: { colorfunc: paint } },
      surface: { opacity: 0.85, colorfunc: paint },
    });
  });
});
