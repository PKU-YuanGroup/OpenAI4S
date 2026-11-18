/**
 * What the 3D structure viewer reads from a structure, kept free of the DOM
 * and of 3Dmol so it is testable on its own: the PDB HEADER/TITLE, the polymer
 * chains and B-factor range, residue labels, and the 3Dmol style for each
 * representation and colour scheme.
 */

/** The 3Dmol atom fields the viewer reads. Every field is optional: formats differ. */
export type MolAtom = {
  atom?: string;
  name?: string;
  elem?: string;
  resn?: string;
  resi?: number | string;
  icode?: string;
  chain?: string;
  b?: number;
  hetflag?: boolean;
  serial?: number;
  /** 3Dmol's 0-based position of the atom in its model. */
  index?: number;
  x?: number;
  y?: number;
  z?: number;
};

export type StructureHeader = {
  /** HEADER idCode (columns 63-66), kept only when it has the shape of a PDB ID. */
  idCode: string;
  /** TITLE (columns 11-80), continuation records joined. */
  title: string;
  /** HEADER classification (columns 11-50). */
  classification: string;
};

export type StructureFacts = {
  /** Polymer chain IDs, in the order they first appear. */
  chains: string[];
  /** Lowest and highest polymer B-factor; null when absent or all equal. */
  bRange: [number, number] | null;
};

export type MolScheme = "default" | "chain" | "bfactor";
export const MOL_SCHEMES: readonly MolScheme[] = ["default", "chain", "bfactor"];

/** Formats whose text carries PDB header records. */
export const PDB_FORMATS: ReadonlySet<string> = new Set(["pdb", "ent", "pqr"]);

const PDB_ID = /^[0-9][0-9A-Za-z]{3}$/;
const TITLE_MAX = 400;

/**
 * HEADER and TITLE of a PDB file, or null when it has neither. Header records
 * precede the coordinates, so the scan stops at the first ATOM/HETATM/MODEL
 * record instead of walking a large file to its end.
 */
export function parsePdbHeader(text: string): StructureHeader | null {
  let idCode = "";
  let classification = "";
  const title: string[] = [];
  let at = 0;
  while (at < text.length) {
    let end = text.indexOf("\n", at);
    if (end < 0) end = text.length;
    const line = text.slice(at, end);
    at = end + 1;
    const record = line.slice(0, 6);
    if (record === "HEADER") {
      classification = line.slice(10, 50).trim();
      const id = line.slice(62, 66).trim();
      idCode = PDB_ID.test(id) ? id.toUpperCase() : "";
    } else if (record === "TITLE ") {
      const part = line.slice(10, 80).trim();
      if (part) title.push(part);
    } else if (record === "ATOM  " || record === "HETATM" || record === "MODEL ") {
      break;
    }
  }
  let joined = title.join(" ").replace(/\s+/g, " ").trim();
  if (joined.length > TITLE_MAX) joined = joined.slice(0, TITLE_MAX - 1).trimEnd() + "…";
  if (!idCode && !joined && !classification) return null;
  return { idCode, title: joined, classification };
}

export function chainOf(atom: MolAtom): string {
  return typeof atom.chain === "string" ? atom.chain.trim() : "";
}

/**
 * Chains and B-factor range of the polymer. Ligands, ions and water keep
 * their element colours in every scheme, so they do not decide whether a
 * scheme is offered.
 */
export function structureFacts(atoms: readonly MolAtom[]): StructureFacts {
  const chains: string[] = [];
  const seen = new Set<string>();
  let lo = Infinity;
  let hi = -Infinity;
  for (const atom of atoms) {
    if (!atom || atom.hetflag) continue;
    const chain = chainOf(atom);
    if (chain && !seen.has(chain)) {
      seen.add(chain);
      chains.push(chain);
    }
    const b = atom.b;
    if (typeof b === "number" && Number.isFinite(b)) {
      if (b < lo) lo = b;
      if (b > hi) hi = b;
    }
  }
  return { chains, bRange: hi > lo ? [lo, hi] : null };
}

export function schemeAvailable(scheme: MolScheme, facts: StructureFacts): boolean {
  if (scheme === "chain") return facts.chains.length > 0;
  if (scheme === "bfactor") return facts.bRange !== null;
  return true;
}

type Residue = { resn: string; resi: string; icode: string; chain: string };

function residueOf(atom: MolAtom): Residue | null {
  const resn = typeof atom.resn === "string" ? atom.resn.trim() : "";
  const raw = atom.resi;
  const resi =
    typeof raw === "number" && Number.isFinite(raw)
      ? String(raw)
      : typeof raw === "string" && /^-?\d+$/.test(raw.trim())
        ? raw.trim()
        : "";
  if (!resn || !resi) return null;
  const icode = typeof atom.icode === "string" ? atom.icode.trim() : "";
  return { resn, resi, icode, chain: chainOf(atom) };
}

function sameResidue(atom: MolAtom, residue: Residue): boolean {
  const other = residueOf(atom);
  return (
    !!other &&
    other.resi === residue.resi &&
    other.chain === residue.chain &&
    other.icode === residue.icode &&
    other.resn === residue.resn
  );
}

/** One key per residue (chain, number, insertion code, name); per atom when there is no residue. */
export function residueKey(atom: MolAtom): string {
  const residue = residueOf(atom);
  if (residue) return ["res", residue.chain, residue.resi, residue.icode, residue.resn].join("|");
  return ["atom", atom.index ?? atom.serial ?? "", atom.x, atom.y, atom.z].join("|");
}

/**
 * "GLY 65 · A". An atom outside any residue (XYZ, SDF) is its element and
 * its position in the file counted from 1, "C 1": those formats carry no
 * serial numbers and 3Dmol numbers their atoms from 0.
 */
export function residueLabel(atom: MolAtom): string {
  const residue = residueOf(atom);
  if (residue) {
    const name = `${residue.resn} ${residue.resi}${residue.icode}`;
    return residue.chain ? `${name} · ${residue.chain}` : name;
  }
  const name = String(atom.elem || atom.atom || "?").trim() || "?";
  const position = typeof atom.index === "number" ? atom.index + 1 : atom.serial;
  return position != null ? `${name} ${position}` : name;
}

export type Point = { x: number; y: number; z: number };

function pointOf(atom: MolAtom): Point | null {
  const { x, y, z } = atom;
  if (typeof x !== "number" || typeof y !== "number" || typeof z !== "number") return null;
  return Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z) ? { x, y, z } : null;
}

/**
 * Where a residue label sits: the residue's CA (on the cartoon), else the
 * centre of the residue (a ligand), else the clicked atom.
 */
export function labelAnchor(atoms: readonly MolAtom[], atom: MolAtom): Point | null {
  const residue = residueOf(atom);
  if (!residue) return pointOf(atom);
  let sx = 0;
  let sy = 0;
  let sz = 0;
  let n = 0;
  for (const other of atoms) {
    if (!other || !sameResidue(other, residue)) continue;
    const point = pointOf(other);
    if (!point) continue;
    if ((other.atom || other.name) === "CA") return point;
    sx += point.x;
    sy += point.y;
    sz += point.z;
    n += 1;
  }
  return n ? { x: sx / n, y: sy / n, z: sz / n } : pointOf(atom);
}

/** Tableau 10: distinct neighbours, legible on the light and the dark background. */
export const CHAIN_PALETTE: readonly number[] = [
  0x4e79a7, 0xf28e2b, 0xe15759, 0x76b7b2, 0x59a14f, 0xedc948, 0xb07aa1, 0xff9da7, 0x9c755f,
  0xbab0ac,
];
export const NO_DATA_COLOR = 0x9e9e9e;
/** Low B-factor is blue, the middle white, high red (PyMOL's blue_white_red). */
export const B_LOW = 0x2166ac;
export const B_MID = 0xf7f7f7;
export const B_HIGH = 0xb2182b;

export function hexColor(color: number): string {
  return "#" + color.toString(16).padStart(6, "0");
}

export const B_GRADIENT_CSS = `linear-gradient(90deg, ${hexColor(B_LOW)}, ${hexColor(B_MID)}, ${hexColor(B_HIGH)})`;

/** The colour of the chain listed at `index` in `StructureFacts.chains`. */
export function chainColor(index: number): number {
  return CHAIN_PALETTE[index % CHAIN_PALETTE.length] ?? NO_DATA_COLOR;
}

function mix(from: number, to: number, f: number): number {
  let out = 0;
  for (const shift of [16, 8, 0]) {
    const a = (from >> shift) & 0xff;
    const c = (to >> shift) & 0xff;
    out |= Math.round(a + (c - a) * f) << shift;
  }
  return out;
}

/** Blue → white → red across `range`; values outside it are clamped. */
export function bFactorColor(b: unknown, range: readonly [number, number]): number {
  const [lo, hi] = range;
  if (typeof b !== "number" || !Number.isFinite(b) || !(hi > lo)) return NO_DATA_COLOR;
  const t = Math.min(1, Math.max(0, (b - lo) / (hi - lo)));
  return t < 0.5 ? mix(B_LOW, B_MID, t * 2) : mix(B_MID, B_HIGH, (t - 0.5) * 2);
}

export type MolColorFunc = (atom: MolAtom) => number;

/** The per-atom colour of a scheme; null keeps the default colouring. */
export function schemeColorFunc(scheme: MolScheme, facts: StructureFacts): MolColorFunc | null {
  if (scheme === "chain" && facts.chains.length) {
    const colors = new Map(facts.chains.map((chain, index) => [chain, chainColor(index)]));
    return (atom) => colors.get(chainOf(atom)) ?? NO_DATA_COLOR;
  }
  if (scheme === "bfactor" && facts.bRange) {
    const range = facts.bRange;
    return (atom) => bFactorColor(atom.b, range);
  }
  return null;
}

export type MolStyleSpec = {
  style: Record<string, unknown>;
  /** Surface options, only for the surface representation. */
  surface: Record<string, unknown> | null;
};

/**
 * The 3Dmol style for a representation. Without a colour function this is the
 * viewer's original look: a spectrum cartoon, Jmol element colours otherwise,
 * a white surface over the cartoon. CA-only models (e.g. synthetic backbones)
 * draw nothing as a plain cartoon, so they get a trace tube plus CA spheres.
 */
export function molStyleSpec(style: string, caOnly: boolean, colorfunc: MolColorFunc | null): MolStyleSpec {
  const paint = (fallback: Record<string, unknown>): Record<string, unknown> =>
    colorfunc ? { colorfunc } : fallback;
  if (style === "surface") {
    return {
      style: { cartoon: paint({ color: "spectrum" }) },
      surface: colorfunc ? { opacity: 0.85, colorfunc } : { opacity: 0.85, color: "white" },
    };
  }
  if (style === "cartoon") {
    return {
      style: caOnly
        ? {
            cartoon: { ...paint({ color: "spectrum" }), style: "trace" },
            sphere: { ...paint({ colorscheme: "Jmol" }), radius: 0.5 },
          }
        : { cartoon: paint({ color: "spectrum" }) },
      surface: null,
    };
  }
  if (style === "stick") return { style: { stick: paint({ colorscheme: "Jmol" }) }, surface: null };
  if (style === "sphere") return { style: { sphere: paint({ colorscheme: "Jmol" }) }, surface: null };
  return { style: { line: paint({ colorscheme: "Jmol" }) }, surface: null };
}
